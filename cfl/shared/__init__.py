"""Shared utilities for Sports Sandbox independent sports modules."""
from pathlib import Path

SANDBOX_ROOT = Path(__file__).resolve().parent.parent
ISOLATION_MLB = Path.home() / "Documents/Personal/mlb"
ISOLATION_SOCCER = Path.home() / "Documents/Personal/soccer"
ISOLATION_WNBA = Path.home() / "Documents/Personal/wnba"
