"""Where saved picks / results HTML lives.

Render wipes the app directory on every deploy. Only the /data disk survives.
Prediction slates already persist under /data/.cache/predictions; served pages
used to sit next to the code, so every push started with no pages and the
single 2 GB worker rebuilt every sport at once.

Locally (/data missing) this is still <repo>/.cache.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent
_DATA = Path("/data")


def cache_dir() -> Path:
    d = (_DATA / ".cache") if _DATA.is_dir() else (_REPO / ".cache")
    d.mkdir(parents=True, exist_ok=True)
    return d
