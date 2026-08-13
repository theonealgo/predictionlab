#!/usr/bin/env python3
"""Thin NHL77FINAL sidecar for Sports Sandbox hub (separate process).

Hub proxies MLB/Soccer/WNBA live-parity pages here so the hub process never
imports NHL77FINAL (avoids macOS OOM kill of the hub).

  PORT=5052 python hub/live_sidecar.py

Sandbox only — no push / no production.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

LIVE_ROOT = Path.home() / "Documents/Personal/predictionlabfix_work"
PORT = int(os.environ.get("PORT", os.environ.get("HUB_LIVE_SIDECAR_PORT", "5052")))


def main() -> None:
    if not LIVE_ROOT.is_dir():
        raise SystemExit(f"Live root missing: {LIVE_ROOT}")
    os.chdir(LIVE_ROOT)
    # Keep live root first so `sports` resolves to predictionlabfix_work.
    live = str(LIVE_ROOT.resolve())
    sys.path = [p for p in sys.path if str(Path(p).resolve()) != live]
    sys.path.insert(0, live)
    os.environ.setdefault("FLASK_ENV", "development")
    os.environ["PORT"] = str(PORT)

    import NHL77FINAL as N  # noqa: WPS433

    # Absolute DB — hub / isolation cwd must not steal relative DATABASE.
    db = LIVE_ROOT / "sports_predictions_original.db"
    if db.is_file():
        N.DATABASE = str(db.resolve())

    # Sandbox only: unlock predicted totals / projections so Total Edge can
    # read real model totals (no login). Never shipped to production.
    def _sandbox_premium_unlock() -> bool:
        return True

    try:
        import auth_system as _auth

        _auth.is_premium_user = _sandbox_premium_unlock  # type: ignore[method-assign]
        if hasattr(N, "is_premium_user"):
            N.is_premium_user = _sandbox_premium_unlock  # type: ignore[method-assign]
        print("[live-sidecar] sandbox premium unlock ON (local Total Edge)", flush=True)
    except Exception as e:
        print(f"[live-sidecar] premium unlock skipped: {e}", flush=True)

    print(f"[live-sidecar] NHL77FINAL → http://127.0.0.1:{PORT}", flush=True)
    print("[live-sidecar] Sandbox only — not production", flush=True)
    N.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
