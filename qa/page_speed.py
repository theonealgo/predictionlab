"""Shared page-load budget for chrome + site checkers."""
from __future__ import annotations

import os
import time

try:
    from audit_config import PAGE_SPEED_BUDGET as _CFG_BUDGET
except Exception:
    _CFG_BUDGET = 5.0

PAGE_SPEED_BUDGET = float(os.environ.get("AUDIT_SPEED_BUDGET", _CFG_BUDGET))


def is_slow(elapsed: float, budget: float | None = None) -> bool:
    return float(elapsed or 0) > float(PAGE_SPEED_BUDGET if budget is None else budget)


def is_results_path(path: str) -> bool:
    base = (path or "").split("?")[0].rstrip("/")
    return base.endswith("-results") or base.endswith("/results")


def results_wont_open_message(path: str, elapsed: float, budget: float | None = None) -> str:
    limit = float(PAGE_SPEED_BUDGET if budget is None else budget)
    return (
        f"Won't open — took {elapsed:.1f}s (over {limit:.0f}s). "
        "A results page that sits on the spinner is a fail, even if HTTP 200 arrives later."
    )


def speed_fail_message(path: str, elapsed: float, budget: float | None = None) -> str:
    limit = float(PAGE_SPEED_BUDGET if budget is None else budget)
    if is_results_path(path):
        return results_wont_open_message(path, elapsed, limit)
    return (
        f"{path} took {elapsed:.1f}s (over {limit:.0f}s). "
        f"Pages must load in {limit:.0f}s."
    )


def timed_get(session, url: str, timeout: float):
    """GET url and return (resp_or_none, elapsed, error_or_none)."""
    started = time.perf_counter()
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        return resp, time.perf_counter() - started, None
    except Exception as exc:
        return None, time.perf_counter() - started, exc
