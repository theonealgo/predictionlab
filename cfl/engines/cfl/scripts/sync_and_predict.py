#!/usr/bin/env python3
"""Sync official CFL slate + write sandbox predictions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline import ensure_predictions  # noqa: E402


def main() -> None:
    meta = ensure_predictions(refresh=True)
    cards = meta.pop("cards", [])
    print(json.dumps({**meta, "sample": cards[:3]}, indent=2, default=str))


if __name__ == "__main__":
    main()
