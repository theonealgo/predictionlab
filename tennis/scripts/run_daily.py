#!/usr/bin/env python3
"""Daily scheduler stub for Tennis: predict → grade → optional retrain."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tennis.results.grader import grade_pending, performance


def main() -> None:
    n = grade_pending()
    perf = performance()
    print(f"Tennis daily: graded={n} performance={perf}")


if __name__ == "__main__":
    main()
