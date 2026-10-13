#!/usr/bin/env python3
"""Every link opens, and login / logout work.

    python qa/site_links_check.py [BASE_URL]

BASE_URL defaults to http://127.0.0.1:5122. Requests carry the public Host
header so the server treats them as real visitors. A throwaway paid account and
free account are made for the run and deleted after it (qa/test_accounts.py).

The homepage, account pages and every sport's picks and results pages are
opened as a logged-out visitor, a free account and a paid account. Every link
on them (header, footer, hamburger menus, page body) is collected and opened.

FAIL when:
  - a link answers with an error or a missing page, or does not open in 60s
  - a page on this site takes longer than SLOW_S seconds to open
  - the login or signup page has no form, or login has no Google sign-in
  - Google sign-in does not hand off to Google
  - logging in does not show Sign out, or a logged-out visitor sees Sign out
  - after Sign out the visitor is still signed in
  - a page that was still loading when the visitor signed out signs them back in
"""
from __future__ import annotations

import contextlib
import importlib.util
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_HOST = "predictionlab.io"
HEADERS = {"Host": PUBLIC_HOST, "X-Forwarded-Proto": "https", "User-Agent": "pl-links-check"}
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SIGN_OUT = 'href="/logout"'
SLOW_S = 10.0
TIMEOUT_S = 60
WORKERS = 6
SPORTS = ("mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab", "ncaaw", "wnba", "cfl",
          "soccer", "tennis", "ufc", "golf")
START_PAGES = ["/", "/plans", "/account", "/blog", "/performance", "/all-sports-results"] + [
    f"/{s}-{kind}" for s in SPORTS for kind in ("picks", "results")
]
# Links that start a payment, change an account, or need Google's reply are not opened.
SKIP_PREFIXES = ("/logout", "/checkout", "/create-portal-session", "/billing", "/stripe",
                 "/admin", "/auth/google/callback", "/reset-password", "/set-password",
                 "/claim-account", "/smtp-diag", "/change-password")
SITE_HOSTS = {PUBLIC_HOST, "www." + PUBLIC_HOST}
_A_HREF = re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']", re.I)
_JS_MENU = re.compile(r"\bh:\s*'([^']+)'")


def _test_accounts():
    spec = importlib.util.spec_from_file_location("pl_test_accounts", ROOT / "qa" / "test_accounts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Visitor:
    """A browser-like cookie jar talking to BASE with the public Host header."""

    def __init__(self, base: str):
        self.base = base
        self.jar: dict[str, str] = {}

    def apply(self, resp: requests.Response) -> None:
        for raw in resp.raw.headers.getlist("Set-Cookie") if resp.raw is not None else []:
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:
                continue
            for name, morsel in jar.items():
                gone = morsel.value == "" or morsel["max-age"] == "0"
                if morsel["expires"]:
                    try:
                        from email.utils import parsedate_to_datetime
                        gone = gone or parsedate_to_datetime(morsel["expires"]).timestamp() < time.time()
                    except Exception:
                        pass
                if gone:
                    self.jar.pop(name, None)
                else:
                    self.jar[name] = morsel.value

    def headers(self, jar: dict[str, str] | None = None) -> dict[str, str]:
        h = dict(HEADERS)
        jar = self.jar if jar is None else jar
        if jar:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        return h

    def get(self, path: str, hops: int = 5, jar: dict[str, str] | None = None, keep: bool = True):
        """(status, html, final path, seconds). Follows same-site redirects only."""
        t0 = time.time()
        for _ in range(hops):
            try:
                r = requests.get(self.base + path, headers=self.headers(jar), allow_redirects=False,
                                 timeout=TIMEOUT_S, stream=False)
            except requests.RequestException:
                return -1, "", path, time.time() - t0
            if keep and jar is None:
                self.apply(r)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                loc = urlsplit(urljoin(path, r.headers["Location"]))
                if loc.netloc and loc.netloc not in SITE_HOSTS and loc.netloc != urlsplit(self.base).netloc:
                    return r.status_code, r.headers["Location"], path, time.time() - t0
                path = (loc.path or "/") + (("?" + loc.query) if loc.query else "")
                continue
            return r.status_code, r.text, path, time.time() - t0
        return 0, "", path, time.time() - t0

    def login(self, email: str, password: str) -> bool:
        code, html, _, _ = self.get("/login")
        tok = re.search(r'name="csrf_token" value="([^"]+)"', html or "")
        data = {"email": email, "password": password, "csrf_token": tok.group(1) if tok else ""}
        try:
            r = requests.post(self.base + "/login", headers=self.headers(), data=data,
                              allow_redirects=False, timeout=TIMEOUT_S)
        except requests.RequestException:
            return False
        self.apply(r)
        _, html, _, _ = self.get("/account")
        return SIGN_OUT in (html or "")


def _links(html: str, page: str) -> tuple[set[str], set[str]]:
    """(same-site paths, outside URLs) linked from one page."""
    site, outside = set(), set()
    for href in _A_HREF.findall(html) + _JS_MENU.findall(html):
        href = href.strip().replace("&amp;", "&")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:")):
            continue
        if any(js in href for js in ("{{", "${", "'+", "' +", '"+', '" +')):
            continue  # built by page script at run time, not a real link
        url = urlsplit(urljoin("https://" + PUBLIC_HOST + page, href))
        if url.scheme not in ("http", "https"):
            continue
        if url.netloc in SITE_HOSTS:
            path = (url.path or "/") + (("?" + url.query) if url.query else "")
            if not path.startswith(SKIP_PREFIXES):
                site.add(path)
        else:
            outside.add(url._replace(fragment="").geturl())
    return site, outside


def _check_site_link(v: Visitor, who: str, path: str, found_on: str) -> list[str]:
    code, body, final, secs = v.get(path, keep=False)
    where = f"{v.base}{path} (linked from {found_on}, {who})"
    if code == -1:
        return [f"LINK: {where} | does not open within {TIMEOUT_S}s | should be: opens"]
    if code in (301, 302, 303, 307, 308):
        return []  # hands off to Google, Stripe, or another site
    if code != 200:
        return [f"LINK: {where} | answers HTTP {code} | should be: opens (HTTP 200)"]
    if not body.strip():
        return [f"LINK: {where} | opens an empty page | should be: page content"]
    if secs > SLOW_S:
        return [f"SLOW: {where} | took {secs:.1f}s to open | should be: under {SLOW_S:.0f}s"]
    return []


def _check_outside_link(url: str, found_on: str) -> list[str]:
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20, allow_redirects=True)
    except requests.RequestException as exc:
        return [f"LINK: {url} (linked from {found_on}) | does not open ({type(exc).__name__}) | should be: opens"]
    if r.status_code in (404, 410):
        return [f"LINK: {url} (linked from {found_on}) | answers HTTP {r.status_code} | should be: opens"]
    return []


def _login_pages(base: str) -> list[str]:
    fails = []
    v = Visitor(base)
    code, login_html, final, _ = v.get("/login")
    if code != 200 or 'name="email"' not in login_html or 'name="password"' not in login_html:
        fails.append(f"LOGIN: {base}/login | login page HTTP {code}, no email/password form "
                     f"| should be: login form")
    code, html, _, _ = v.get("/signup")
    if code != 200 or 'name="email"' not in html or 'name="password"' not in html:
        fails.append(f"LOGIN: {base}/signup | signup page HTTP {code}, no email/password form "
                     f"| should be: signup form")
    try:
        r = requests.get(base + "/auth/google", headers=v.headers(), allow_redirects=False, timeout=TIMEOUT_S)
    except requests.RequestException:
        return fails + [f"LOGIN: {base}/auth/google | does not open within {TIMEOUT_S}s "
                        f"| should be: hands off to accounts.google.com"]
    if "not configured" in (r.text or ""):
        return fails + [f"LOGIN: {base}/auth/google | Google sign-in is not set up on this server "
                        f"(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing), so it could not be tested "
                        f"| should be: hands off to accounts.google.com"]
    loc = r.headers.get("Location", "")
    if r.status_code not in (301, 302, 303, 307, 308) or "accounts.google.com" not in loc:
        said = " ".join(re.sub(r"<[^>]+>", " ", r.text or "").split())[:120]
        fails.append(f"LOGIN: {base}/auth/google | answers HTTP {r.status_code} {said!r} "
                     f"| should be: hands off to accounts.google.com")
    if "/auth/google" not in login_html:
        fails.append(f"LOGIN: {base}/login | no Google sign-in button | should be: Sign in with Google")
    return fails


def _paywall_at_own_address(base: str) -> list[str]:
    """Signed out at the address being checked (not the public name), picks stay locked."""
    host = urlsplit(base).hostname or ""
    if host in ("127.0.0.1", "localhost"):
        return []
    spec = importlib.util.spec_from_file_location("pl_paywall_rules", ROOT / "paywall.py")
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    fails = []
    for page in ("/nfl-picks", "/ncaaf-picks", "/nhl-picks", "/mlb-picks"):
        try:
            html = requests.get(base + page, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT_S).text
        except requests.RequestException:
            continue
        leaks = rules.leaked_values(html)
        if leaks:
            fails.append(f"PAYWALL: {base}{page} | signed-out visitor at {host} sees: {', '.join(leaks)} "
                         f"| should be: locked")
    return fails


def _login_logout(base: str, who: str, email: str, password: str) -> tuple[list[str], Visitor | None]:
    fails = []
    v = Visitor(base)
    if not v.login(email, password):
        return [f"LOGIN: {base}/login | {who} logs in but /account does not show Sign out "
                f"| should be: signed in"], None
    for page in ("/", "/plans", "/nfl-picks", "/nfl-results"):
        code, html, final, _ = v.get(page)
        if code == 200 and SIGN_OUT not in html:
            fails.append(f"LOGIN: {base}{final} | signed-in {who} sees Login | should be: Sign out")
    out = Visitor(base)
    out.jar = dict(v.jar)
    try:
        r = requests.get(base + "/logout", headers=out.headers(), allow_redirects=False, timeout=TIMEOUT_S)
        out.apply(r)
    except requests.RequestException:
        return fails + [f"LOGIN: {base}/logout | Sign out does not open within {TIMEOUT_S}s "
                        f"| should be: signs out"], v
    code, html, final, _ = out.get("/account")
    if SIGN_OUT in html or not final.startswith("/login"):
        fails.append(f"LOGIN: {base}/logout | {who} clicked Sign out, /account still opens signed in "
                     f"| should be: sent to Login")
    code, html, final, _ = out.get("/")
    if SIGN_OUT in html:
        fails.append(f"LOGIN: {base}/ | after Sign out the {who} still sees Sign out | should be: Login")
    return fails, v


def _logout_during_slow_page(base: str, email: str, password: str) -> list[str]:
    """A page still loading at Sign out must not sign the visitor back in."""
    for page in ("/soccer-picks?region=all", "/all-sports-results", "/nfl-results", "/performance", "/"):
        v = Visitor(base)
        if not v.login(email, password):
            return []
        before = dict(v.jar)
        slow: dict = {}

        def _load():
            try:
                slow["resp"] = requests.get(base + page, headers=v.headers(before), allow_redirects=False,
                                            timeout=TIMEOUT_S)
            except requests.RequestException:
                pass
            slow["done"] = time.time()

        t = threading.Thread(target=_load)
        t.start()
        time.sleep(0.3)
        try:
            r = requests.get(base + "/logout", headers=v.headers(before), allow_redirects=False,
                             timeout=TIMEOUT_S)
        except requests.RequestException:
            t.join()
            continue
        logged_out_at = time.time()
        v.apply(r)
        t.join()
        if "resp" not in slow or slow.get("done", 0) <= logged_out_at:
            continue  # the page finished before Sign out; try a slower one
        v.apply(slow["resp"])
        code, html, final, _ = v.get("/account")
        if SIGN_OUT in html or not final.startswith("/login"):
            return [f"LOGIN: {base}{page} | Sign out clicked while this page was still loading; when it "
                    f"finished the visitor was signed back in | should be: stays signed out"]
        return []
    return []


def run(base: str) -> tuple[list[str], dict]:
    base = base.rstrip("/")
    fails: list[str] = []
    stats = {"pages": 0, "links": 0, "outside": 0}
    with contextlib.ExitStack() as stack:
        try:
            logins = stack.enter_context(_test_accounts().accounts(ROOT))
        except Exception as exc:
            return [f"ERROR: could not make the throwaway test accounts: {exc}"], stats
        fails += _login_pages(base)
        fails += _paywall_at_own_address(base)
        viewers: dict[str, Visitor] = {"logged-out visitor": Visitor(base)}
        for who, key in (("paid account", "paid"), ("free account", "free")):
            f, v = _login_logout(base, who, *logins[key])
            fails += f
            if v is not None and v.login(*logins[key]):
                viewers[who] = v
        fails += _logout_during_slow_page(base, *logins["paid"])

        site_links: dict[str, tuple[str, str]] = {}
        outside_links: dict[str, str] = {}
        for who, v in viewers.items():
            for page in START_PAGES:
                if who == "logged-out visitor" and page == "/account":
                    continue
                code, html, final, secs = v.get(page)
                stats["pages"] += 1
                if code != 200 or not html:
                    fails.append(f"LINK: {base}{page} ({who}) | answers HTTP {code} | should be: opens")
                    continue
                if secs > SLOW_S:
                    fails.append(f"SLOW: {base}{page} ({who}) | took {secs:.1f}s to open "
                                 f"| should be: under {SLOW_S:.0f}s")
                if who == "logged-out visitor" and SIGN_OUT in html:
                    fails.append(f"LOGIN: {base}{final} | logged-out visitor sees Sign out | should be: Login")
                site, outside = _links(html, final)
                for path in site:
                    site_links.setdefault(path, (who, page))
                for url in outside:
                    outside_links.setdefault(url, page)
        stats["links"], stats["outside"] = len(site_links), len(outside_links)
        with ThreadPoolExecutor(WORKERS) as pool:
            jobs = [pool.submit(_check_site_link, viewers[who], who, path, page)
                    for path, (who, page) in sorted(site_links.items())]
            for job in jobs:
                fails += job.result()
    return fails, stats


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5122").rstrip("/")
    t0 = time.time()
    fails, stats = run(base)
    print(f"opened {stats['pages']} pages and {stats['links']} site links in {time.time() - t0:.0f}s")
    print(f"---- site links + login/logout: {len(fails)} fail(s) ----")
    for line in fails:
        print(f"[FAIL] {line}")
    if not fails:
        print("- none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
