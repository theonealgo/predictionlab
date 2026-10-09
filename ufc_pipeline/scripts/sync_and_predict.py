#!/usr/bin/env python3
"""Refresh ESPN history/upcoming + Odds API and rewrite UFC predictions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import ensure_predictions  # noqa: E402


def main() -> None:
    meta = ensure_predictions(refresh=True)
    print(meta)


if __name__ == "__main__":
    main()
