#!/usr/bin/env python3
"""Fail when approved frontend files change without updating the reviewed baseline."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "frontend-lock.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    failures = []

    for relative_path, expected in lock["files"].items():
        path = ROOT / relative_path
        if not path.is_file():
            failures.append(f"{relative_path}: protected file is missing")
            continue
        actual = sha256(path)
        if actual != expected:
            failures.append(
                f"{relative_path}: checksum changed "
                f"(expected {expected[:12]}, got {actual[:12]})"
            )

    if failures:
        print("FRONTEND LOCK FAILED")
        for failure in failures:
            print(f"  - {failure}")
        print(
            "Do not refresh frontend-lock.json without the user's explicit written "
            "approval for the exact UI change."
        )
        return 1

    print(f"Frontend lock verified: {len(lock['files'])} protected files unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
