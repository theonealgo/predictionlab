#!/usr/bin/env python3
"""Paywall check: free visitors vs paying users on every sport page.

    python qa/paywall_check.py [BASE_URL]

BASE_URL defaults to http://127.0.0.1:5122. Requests are sent with the public
Host header so the server applies real visitor rules (local addresses are
always treated as paid). The paid visitor logs in with PL_PAYWALL_EMAIL /
PL_PAYWALL_PASSWORD, falling back to LOCAL_TEST_LOGIN.txt.

FAIL when:
  - a free visitor can read a paid value on a picks page (model boxes,
    projected scores, Prediction Lab / XSharp odds and lines, page source)
  - a paying user sees any lock or Join Premium upsell
  - a results page is locked for anyone (results are free)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import paywall  # noqa: E402

SPORTS = ("mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab", "ncaaw", "wnba", "cfl",
          "soccer", "tennis", "ufc", "golf")
PUBLIC_HOST = "predictionlab.io"
HEADERS = {"Host": PUBLIC_HOST, "X-Forwarded-Proto": "https", "User-Agent": "pl-paywall-check"}


def _credentials() -> tuple[str, str]:
    email = os.environ.get("PL_PAYWALL_EMAIL", "")
    password = os.environ.get("PL_PAYWALL_PASSWORD", "")
    if email and password:
        return email, password
    for path in (ROOT / "LOCAL_TEST_LOGIN.txt", ROOT.parent / "pl_cursor" / "LOCAL_TEST_LOGIN.txt"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        e = re.search(r"email:\s*(\S+)", text)
        p = re.search(r"password:\s*(\S+)", text)
        if e and p:
            return e.group(1), p.group(1)
    return "", ""


def _get(base: str, path: str, cookie: str = "", hops: int = 4) -> tuple[int, str, str]:
    """GET on BASE with the public Host; follow only same-site redirects."""
    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    for _ in range(hops):
        try:
            r = requests.get(base + path, headers=headers, allow_redirects=False, timeout=60)
        except requests.RequestException:
            return -1, "", path
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
            loc = urlsplit(r.headers["Location"])
            if loc.netloc and loc.netloc not in (PUBLIC_HOST, "www." + PUBLIC_HOST, urlsplit(base).netloc):
                return r.status_code, "", path
            path = (loc.path or "/") + (("?" + loc.query) if loc.query else "")
            continue
        return r.status_code, r.text, path
    return 0, "", path


def _login(base: str) -> str:
    email, password = _credentials()
    if not email:
        return ""
    s = requests.Session()
    r = s.get(base + "/login", headers=HEADERS, allow_redirects=False, timeout=60)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    data = {"email": email, "password": password, "csrf_token": tok.group(1) if tok else ""}
    jar = {c.name: c.value for c in r.cookies}
    r = requests.post(base + "/login", headers=HEADERS, data=data, cookies=jar,
                      allow_redirects=False, timeout=60)
    jar.update({c.name: c.value for c in r.cookies})
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    _, html, _ = _get(base, "/account", cookie)
    return cookie if html and "/logout" in html else ""


def run(base: str, sports: tuple[str, ...] = SPORTS) -> list[str]:
    fails: list[str] = []
    paid_cookie = _login(base)
    if not paid_cookie:
        fails.append("ERROR: could not log in as the paid test user; paid checks skipped")
    for sport in sports:
        for kind, views in (("picks", ("",)), ("results", ("", "?view=chart"))):
            for view in views:
                path = f"/{sport}-{kind}{view}"
                code, free_html, final = _get(base, path)
                label = f"{sport} {kind}{' chart' if view else ''}"
                if code == -1:
                    fails.append(f"PAYWALL: {label} | {base}{path} | free visitor: page did not load in 60s")
                    continue
                if code != 200 or not free_html:
                    continue
                if kind == "picks":
                    leaks = paywall.leaked_values(free_html)
                    if leaks:
                        fails.append(f"PAYWALL: {label} | {base}{final} | free visitor sees: "
                                     f"{', '.join(leaks)} | should be: locked")
                else:
                    locks = paywall.locked_for_paid(free_html)
                    locks = [x for x in locks if "Join Premium" not in x and "flagged free" not in x]
                    if locks:
                        fails.append(f"PAYWALL: {label} | {base}{final} | free visitor sees: "
                                     f"{', '.join(locks)} | should be: results are free")
                if paid_cookie:
                    pcode, paid_html, pfinal = _get(base, path, paid_cookie)
                    if pcode == -1:
                        fails.append(f"PAYWALL: {label} | {base}{path} | paying user: page did not load in 60s")
                    elif pcode == 200 and paid_html:
                        locks = paywall.locked_for_paid(paid_html)
                        if locks:
                            fails.append(f"PAYWALL: {label} | {base}{pfinal} | paying user sees: "
                                         f"{', '.join(locks)} | should be: everything unlocked")
    return fails


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5122").rstrip("/")
    sports = tuple(s.lower() for s in sys.argv[2:]) or SPORTS
    fails = run(base, sports)
    print(f"---- paywall: {len(fails)} fail(s) ----")
    for line in fails:
        print(f"- {line}")
    if not fails:
        print("- none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
