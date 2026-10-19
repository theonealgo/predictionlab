"""Run expensive after-request page passes once per page version, not once per visit.

The passes rewrite the full page HTML (layout, repair, template gaps, record
checks). Their output depends only on the page they receive, the URL, the
visitor tier, login state, and the day. One visit used to take seconds of CPU,
and the single app process serves every other visitor from the same CPU, so a
few sport-page hits stalled the homepage for everyone.
"""
from __future__ import annotations

import functools
import hashlib
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import request

TTL_SECONDS = 300.0
MAX_BYTES = 96 * 1024 * 1024
_ET = ZoneInfo("America/New_York")

# Some saved pages gain a blank line each time they are re-saved; that must not
# make every visit look like a new page.
_BLANK_LINES = re.compile(rb"\n\s*\n(?:\s*\n)+")

_lock = threading.Lock()
_memo: "OrderedDict[tuple, tuple[float, bytes, tuple]]" = OrderedDict()
_stored_bytes = 0
stats = {"hit": 0, "miss": 0, "skip": 0}


def _remember(key: tuple, out: bytes, headers: tuple) -> None:
    global _stored_bytes
    with _lock:
        old = _memo.pop(key, None)
        if old:
            _stored_bytes -= len(old[1])
        _memo[key] = (time.time(), out, headers)
        _stored_bytes += len(out)
        while _memo and _stored_bytes > MAX_BYTES:
            _, (_, dropped, _) = _memo.popitem(last=False)
            _stored_bytes -= len(dropped)


def _recall(key: tuple):
    with _lock:
        hit = _memo.get(key)
        if hit is None:
            return None
        if time.time() - hit[0] >= TTL_SECONDS:
            global _stored_bytes
            _memo.pop(key, None)
            _stored_bytes -= len(hit[1])
            return None
        _memo.move_to_end(key)
        return hit


def memoize_hook(hook, viewer):
    """Wrap one after-request hook. ``viewer()`` returns what makes a visitor's page differ."""
    name = getattr(hook, "__name__", repr(hook))

    @functools.wraps(hook)
    def run(response):
        try:
            if (
                request.method != "GET"
                or response.status_code != 200
                or response.direct_passthrough
                or "html" not in (response.mimetype or "").lower()
            ):
                return hook(response)
            body = response.get_data()
            key = (
                name,
                hashlib.sha1(_BLANK_LINES.sub(b"\n\n", body)).hexdigest(),
                request.full_path,
                viewer(),
                datetime.now(_ET).strftime("%Y-%m-%d"),
            )
        except Exception:
            return hook(response)
        hit = _recall(key)
        if hit is not None:
            stats["hit"] += 1
            response.set_data(hit[1])
            for header, value in hit[2]:
                response.headers[header] = value
            return response
        before = [(k, v) for k, v in response.headers.items() if k.lower() != "content-length"]
        out = hook(response)
        try:
            if out is not response or response.status_code != 200:
                stats["skip"] += 1
                return out
            after = [(k, v) for k, v in response.headers.items() if k.lower() != "content-length"]
            added = tuple(item for item in after if item not in before)
            removed = [item for item in before if item not in after]
            names = [k.lower() for k, _ in added]
            if removed or "set-cookie" in names or len(names) != len(set(names)):
                stats["skip"] += 1
                return out
            stats["miss"] += 1
            _remember(key, response.get_data(), added)
        except Exception:
            pass
        return out

    run._pl_memoized = True
    return run


def install(app, hook_names, viewer) -> None:
    """Swap the named registered after-request hooks for memoized versions."""
    funcs = app.after_request_funcs.get(None) or []
    for i, fn in enumerate(funcs):
        if getattr(fn, "__name__", "") in hook_names and not getattr(fn, "_pl_memoized", False):
            funcs[i] = memoize_hook(fn, viewer)
