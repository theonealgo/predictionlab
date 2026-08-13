"""HTTP helpers for ESPN / external fetches."""
from __future__ import annotations

from typing import Any, Optional

import requests

DEFAULT_TIMEOUT = 20
UA = "SportsSandboxIndependent/1.0 (+local; not-predictionlab)"


def safe_get(url: str, *, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> Optional[requests.Response]:
    try:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", UA)
        r = requests.get(url, timeout=timeout, headers=headers, **kwargs)
        if r.status_code == 200:
            return r
    except Exception:
        return None
    return None


def get_json(url: str, **kwargs) -> Any:
    r = safe_get(url, **kwargs)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None
