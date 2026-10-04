"""Frozen season result snapshots (NHL first; multi-sport scaffold).

At end of regular season: run scripts/build_season_snapshot.py, commit JSON, deploy.
See docs/season-snapshots.md for the operational workflow.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / 'data' / 'season_snapshots'


def snapshot_filename(sport: str, season: str, phase: str = 'regular') -> str:
    return f'{sport}_{season}_{phase}.json'


def snapshot_path(sport: str, season: str, phase: str = 'regular') -> Path:
    return SNAPSHOT_DIR / snapshot_filename(sport, season, phase)


def load_season_snapshot(
    sport: str,
    season: str,
    phase: str = 'regular',
) -> Optional[dict[str, Any]]:
    path = snapshot_path(sport, season, phase)
    if not path.is_file():
        return None
    try:
        with path.open(encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get('sport') != sport or data.get('season') != season:
        return None
    return data


def save_season_snapshot(
    payload: dict[str, Any],
    sport: str,
    season: str,
    phase: str = 'regular',
) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault('schema_version', SCHEMA_VERSION)
    payload.setdefault('sport', sport)
    payload.setdefault('season', season)
    payload.setdefault('phase', phase)
    payload.setdefault('built_at', datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))
    path = snapshot_path(sport, season, phase)
    with path.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write('\n')
    return path


def season_label_from_bounds(season_start: datetime) -> str:
    """e.g. Oct 2025 start -> '2025-26'."""
    return f'{season_start.year}-{str(season_start.year + 1)[-2:]}'
