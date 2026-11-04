#!/usr/bin/env python3
"""Pre-push gate: run a commit the way Render runs it and open every page.

Checks out the commit into a temporary worktree (only tracked files, like a
fresh deploy), starts `gunicorn -c gunicorn.conf.py NHL77FINAL:app` (one worker,
8 threads), and crawls every page for several rounds across the boot warm-up
window. The push is blocked when:

  - any page returns 5xx, a connection error, or takes longer than --timeout
  - a page that answers 200 on the baseline commit does not answer on this one
  - the worker restarts (pid changes) or the master dies
  - worker memory goes over --mem-mb, or over the baseline peak by --mem-growth

Usage:
  python qa/prepush_gate.py                 # gate HEAD vs origin/main
  python qa/prepush_gate.py <sha> --base <sha>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIT = ["git", "--git-dir", str(ROOT / ".pushgit")] if (ROOT / ".pushgit").is_dir() else ["git"]
VENV_PY = ROOT / ".venv" / "bin" / "python"

PAGES = [
    "/", "/healthz",
    "/mlb-picks", "/nhl-picks", "/nba-picks", "/nfl-picks", "/ncaaf-picks",
    "/ncaab-picks", "/ncaaw-picks", "/wnba-picks", "/cfl-picks",
    "/soccer-picks?region=all", "/tennis-picks", "/ufc-picks", "/golf-picks",
    "/mlb-results", "/nhl-results", "/nba-results", "/nfl-results", "/ncaaf-results",
    "/ncaab-results", "/ncaaw-results", "/wnba-results", "/cfl-results",
    "/soccer-results", "/tennis-results", "/ufc-results", "/golf-results",
    "/mlb-results?view=chart", "/nfl-results?view=chart", "/ncaaf-results?view=chart",
    "/all-sports-results", "/performance", "/daily-report", "/ladder",
    "/blog", "/login", "/signup",
]

# Live reads its own current database (the /data copy), not the one in git, so both runs get today's copy.
COPY_UNTRACKED = [".env", "sports_predictions_original.db"]

# Card values checked on every sport's classic picks page (paid view), live commit vs this one.
SPORTS = ["mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab", "ncaaw", "wnba", "cfl", "soccer", "tennis", "ufc", "golf"]
CARD_VALUES = ("m-grinder2", "m-takedown", "m-edge", "m-xsharp", "m-efficiency", "pl-spread", "pl-total")
BLANK_VALUES = {"", "N/A", "—", "-", "--", "None", "nan"}
# Any host that is not local gets the free-visitor view.
FREE_HOST = "plpreview.test"
sys.path.insert(0, str(ROOT))


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output([*GIT, *args], cwd=cwd, text=True).strip()


def _rss_mb(pid: int) -> float:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)], text=True)
        return int(out.strip() or 0) / 1024.0
    except Exception:
        return 0.0


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.check_output(["pgrep", "-P", str(pid)], text=True)
        return [int(x) for x in out.split()]
    except subprocess.CalledProcessError:
        return []


GOOGLE_TAG = '<script async src="https://www.googletagmanager.com/gtag/js?id=AW-18345189026"></script>'
_tag_missing: set[str] = set()


def _get(url: str, timeout: float) -> tuple[int, float, str]:
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "pl-prepush-gate"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read()
            path = urllib.parse.urlsplit(url).path or "/"
            if path != "/healthz" and "html" in (r.headers.get("Content-Type") or "") and GOOGLE_TAG.encode() not in body:
                _tag_missing.add(path)
            return r.status, time.time() - t0, ""
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, ""
    except Exception as e:  # timeout, refused, reset
        return 0, time.time() - t0, type(e).__name__ + ": " + str(e)[:120]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _fetch(url: str, timeout: float, host: str | None = None) -> tuple[int, str]:
    headers = {"User-Agent": "pl-prepush-gate"}
    if host:
        headers["Host"] = host
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def _picks_path(base: str, sport: str, timeout: float) -> str:
    """The classic picks page: /classic/... on a build that serves the new pages, else the plain URL."""
    path = f"/{sport}-picks" + ("?region=all" if sport == "soccer" else "")
    code, _ = _fetch(base + "/classic" + path, timeout)
    return "/classic" + path if code == 200 else path


def _card_counts(html: str) -> dict:
    import re
    counts = {"cards": len(set(re.findall(r'data-game-id="([^"]+)"', html))), "na": html.count(">N/A<")}
    for name, value in re.findall(r'\bdata-(' + "|".join(CARD_VALUES) + r')="([^"]*)"', html):
        counts[name] = counts.get(name, 0) + (value.strip() not in BLANK_VALUES)
    return counts


def measure_sports(base: str, timeout: float) -> dict:
    """Per sport: cards and filled values (paid view), free-view leaks, and the new pages' cards feed."""
    import paywall
    out = {}
    for sport in SPORTS:
        path = _picks_path(base, sport, timeout)
        code, html = _fetch(base + path, timeout)
        rec = {"path": path, "code": code, "bytes": len(html), **_card_counts(html)}
        fcode, free_html = _fetch(base + path, timeout, host=FREE_HOST)
        rec["free_code"] = fcode
        rec["free_leaks"] = [x for x in paywall.leaked_values(free_html) if x != "page is flagged premium"] if fcode == 200 else []
        if path.startswith("/classic"):
            fc, feed = _fetch(f"{base}/api/web/sports/{sport}-picks/cards", timeout)
            try:
                cards = json.loads(feed).get("cards") or [] if fc == 200 else None
            except ValueError:
                cards = None
            rec["feed_cards"] = None if cards is None else len(cards)
            fc, free_feed = _fetch(f"{base}/api/web/sports/{sport}-picks/cards", timeout, host=FREE_HOST)
            try:
                free_cards = json.loads(free_feed).get("cards") or [] if fc == 200 else []
            except ValueError:
                free_cards = []
            joined = "".join(c.get("html", "") for c in free_cards if isinstance(c, dict))
            rec["feed_free_leaks"] = [x for x in paywall.leaked_values(joined) if x != "page is flagged premium"]
        out[sport] = rec
        print(f"    {sport}: {rec['cards']} cards, {rec['na']} N/A", flush=True)
    return out


def run_commit(sha: str, port: int, rounds: int, min_seconds: int, timeout: float,
               log_dir: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"pl_gate_{sha[:8]}_"))
    shutil.rmtree(work)
    subprocess.check_call([*GIT, "worktree", "add", "--detach", "-f", str(work), sha],
                          cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in COPY_UNTRACKED:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, work / name)

    log_path = log_dir / f"gunicorn_{sha[:8]}.log"
    env = dict(os.environ, PORT=str(port), PYTHONUNBUFFERED="1")
    env.pop("RENDER", None)
    log = open(log_path, "w")
    master = subprocess.Popen(
        [str(VENV_PY), "-m", "gunicorn", "-c", "gunicorn.conf.py", "NHL77FINAL:app"],
        cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    result = {"sha": sha, "pages": {}, "fails": [], "peak_mb": 0.0, "log": str(log_path)}
    _tag_missing.clear()
    try:
        t_start = time.time()
        up = False
        while time.time() - t_start < 180:
            if master.poll() is not None:
                result["fails"].append(f"gunicorn exited during boot (code {master.returncode})")
                return result
            code, _, _ = _get(base + "/healthz", 5)
            if code == 200:
                up = True
                break
            time.sleep(2)
        if not up:
            result["fails"].append("app did not answer /healthz within 180s of start")
            return result
        result["boot_s"] = round(time.time() - t_start, 1)

        workers = _children(master.pid)
        worker0 = workers[0] if workers else None
        rnd = 0
        while rnd < rounds or time.time() - t_start < min_seconds:
            rnd += 1
            for path in PAGES:
                code, secs, err = _get(base + path, timeout)
                rec = result["pages"].setdefault(path, [])
                rec.append([rnd, code, round(secs, 2), err])
                if master.poll() is not None:
                    result["fails"].append(f"gunicorn master died (round {rnd}, after {path})")
                    return result
                ws = _children(master.pid)
                if worker0 and ws and ws[0] != worker0:
                    result["fails"].append(
                        f"worker restarted (round {rnd}, after {path}) — out of memory or crash")
                    worker0 = ws[0]
                for w in ws:
                    result["peak_mb"] = max(result["peak_mb"], _rss_mb(w))
            print(f"  [{sha[:8]}] round {rnd} done at {time.time() - t_start:.0f}s, "
                  f"worker {result['peak_mb']:.0f} MB peak", flush=True)
        result["tag_missing"] = sorted(_tag_missing)
        print(f"  [{sha[:8]}] card values:", flush=True)
        result["sports"] = measure_sports(base, timeout)
        return result
    finally:
        try:
            os.killpg(master.pid, signal.SIGTERM)
            master.wait(timeout=20)
        except Exception:
            try:
                os.killpg(master.pid, signal.SIGKILL)
            except Exception:
                pass
        log.close()
        subprocess.call([*GIT, "worktree", "remove", "--force", str(work)], cwd=ROOT,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(work, ignore_errors=True)


def _page_ok(rows: list) -> bool:
    return any(code in (200, 301, 302, 303, 304) for _, code, _, _ in rows)


def judge(new: dict, base: dict | None, timeout: float, mem_mb: float, growth: float) -> list[str]:
    fails = list(new["fails"])
    fails += [f"{path}: Google tag missing" for path in sorted(new.get("tag_missing", ()))]
    for path, rows in new["pages"].items():
        bad = [r for r in rows if r[1] >= 500 or r[1] == 0 or r[2] >= timeout]
        for rnd, code, secs, err in bad:
            what = err or (f"HTTP {code}" if code else "no answer")
            if secs >= timeout:
                what = f"took {secs}s (limit {timeout:.0f}s)"
            fails.append(f"{path}: round {rnd}: {what}")
        if base and _page_ok(base["pages"].get(path, [])) and not _page_ok(rows):
            fails.append(f"{path}: works on the live commit but not on this one")
    fails += judge_sports(new.get("sports") or {}, (base or {}).get("sports") or {})
    if new["peak_mb"] > mem_mb:
        fails.append(f"worker memory {new['peak_mb']:.0f} MB is over the {mem_mb:.0f} MB limit")
    if base and base["peak_mb"] and new["peak_mb"] > base["peak_mb"] * (1 + growth):
        fails.append(f"worker memory {new['peak_mb']:.0f} MB vs live commit "
                     f"{base['peak_mb']:.0f} MB (over +{growth:.0%})")
    return fails


def judge_sports(new: dict, base: dict) -> list[str]:
    fails = []
    for sport, rec in new.items():
        was = base.get(sport) or {}
        if rec["code"] != 200:
            fails.append(f"{sport}: picks page {rec['path']} answered {rec['code']}")
            continue
        if rec.get("free_code") == 200 and rec["free_leaks"]:
            fails.append(f"{sport}: logged-out picks page shows paid values: {', '.join(rec['free_leaks'])}")
        if rec.get("feed_free_leaks"):
            fails.append(f"{sport}: logged-out cards feed shows paid values: {', '.join(rec['feed_free_leaks'])}")
        if rec["cards"] and rec.get("feed_cards") == 0:
            fails.append(f"{sport}: new page cards feed is empty ({rec['cards']} cards on the classic page)")
        elif rec.get("feed_cards") is None and rec["path"].startswith("/classic"):
            fails.append(f"{sport}: new page cards feed did not answer")
        if not was or was.get("code") != 200:
            continue
        # A game or two can leave the window between the two runs.
        if rec["cards"] < was["cards"] - max(2, was["cards"] // 10):
            fails.append(f"{sport}: {rec['cards']} cards vs {was['cards']} on live")
        for name in CARD_VALUES:
            if name not in was or not was["cards"] or not rec["cards"]:
                continue
            before, after = was[name] / was["cards"], rec.get(name, 0) / rec["cards"]
            if after < before - 0.02:
                fails.append(f"{sport}: {name} filled on {after:.0%} of cards vs {before:.0%} on live")
        # N/A is display text (a line can read "—" on one build and "N/A" on another); the filled values above decide.
        if was["cards"] and rec["cards"] and rec["na"] / rec["cards"] > was["na"] / was["cards"] + 0.5:
            print(f"  note: {sport}: {rec['na']} N/A on {rec['cards']} cards vs {was['na']} on {was['cards']} on live")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sha", nargs="?", default="HEAD")
    ap.add_argument("--base", default="origin/main", help="known-good commit to compare against ('' to skip)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--min-seconds", type=int, default=240, help="keep crawling at least this long after start")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--mem-mb", type=float, default=float(os.environ.get("PL_GATE_MEM_MB", 1800)))
    ap.add_argument("--mem-growth", type=float, default=0.25)
    ap.add_argument("--port", type=int, default=5199)
    args = ap.parse_args()
    if args.port == 5122:
        print("prepush gate: 5122 is the local site; use another port", file=sys.stderr)
        return 1
    if os.environ.get("PL_BACKUP_DONE") != "1":
        subprocess.call([str(VENV_PY), str(ROOT / "tools" / "pl_backup.py"), "--reason", "before prepush gate"])

    sha = _git("rev-parse", args.sha)
    base_sha = _git("rev-parse", args.base) if args.base else ""
    log_dir = ROOT / ".cache" / "prepush_gate"
    log_dir.mkdir(parents=True, exist_ok=True)

    base_res = None
    if base_sha and base_sha != sha:
        cache = log_dir / f"base_{base_sha[:12]}_{time.strftime('%Y%m%d')}.json"
        if cache.exists():
            base_res = json.loads(cache.read_text())
            print(f"baseline {base_sha[:8]}: cached ({base_res['peak_mb']:.0f} MB peak)")
        else:
            print(f"baseline {base_sha[:8]}: running…", flush=True)
            base_res = run_commit(base_sha, args.port, args.rounds, args.min_seconds, args.timeout, log_dir)
            if not base_res["fails"]:
                cache.write_text(json.dumps(base_res))

    print(f"candidate {sha[:8]}: running…", flush=True)
    new_res = run_commit(sha, args.port, args.rounds, args.min_seconds, args.timeout, log_dir)
    fails = judge(new_res, base_res, args.timeout, args.mem_mb, args.mem_growth)

    report = log_dir / f"report_{sha[:8]}.json"
    report.write_text(json.dumps({"candidate": new_res, "baseline": base_res, "fails": fails}, indent=1))
    print(f"\npages: {len(new_res['pages'])}  worker peak: {new_res['peak_mb']:.0f} MB"
          + (f"  (live commit {base_res['peak_mb']:.0f} MB)" if base_res else ""))
    if fails:
        print(f"PUSH BLOCKED — {len(fails)} problem(s):")
        for f in fails[:60]:
            print("  -", f)
        print(f"server log: {new_res['log']}\nreport: {report}")
        return 1
    print("PASS — every page answered on the deploy-style server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
