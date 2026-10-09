"""Current season (live) vs last season (frozen snapshot).

This season 2026/2027 is written from the live Season board when a results
page renders. Last season 2025/2026 is committed JSON and never regraded.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

THIS_SEASON_LABEL = "2026/2027"
LAST_SEASON_LABEL = "2025/2026"
THIS_SEASON_KEY = "2026-27"
LAST_SEASON_KEY = "2025-26"

_ML_MODELS = (
    ("glicko2", "Grinder2"),
    ("trueskill", "Takedown"),
    ("elo", "Edge"),
    ("xgboost", "XSharp"),
    ("ensemble", "Sharp Consensus"),
    ("efficiency", "Efficiency"),
)

_DASHBOARD_SPORTS = (
    "NHL",
    "NBA",
    "MLB",
    "NFL",
    "NCAAB",
    "NCAAW",
    "NCAAF",
    "WNBA",
    "SOCCER",
    "CFL",
    "TENNIS",
    "UFC",
    "GOLF",
)

_ISOLATE_ROOT = Path(__file__).resolve().parent
_SHARED_CURRENT = _ISOLATE_ROOT.parent / "_season_now"
_LOCAL_SNAP = _ISOLATE_ROOT / "data" / "season_snapshots"


def _jsonable_stats(stats: Any) -> dict:
    out: dict[str, Any] = {}
    if not isinstance(stats, dict):
        return out
    for key, val in stats.items():
        if not isinstance(val, dict):
            continue
        try:
            correct = int(val.get("correct") or 0)
            total = int(val.get("total") or 0)
        except (TypeError, ValueError):
            continue
        acc = val.get("accuracy")
        if acc is None and total:
            acc = round(100.0 * correct / total, 1)
        out[str(key)] = {
            "correct": correct,
            "total": total,
            "accuracy": acc,
        }
    return out


def current_path(sport: str) -> Path:
    sport_u = (sport or "").strip().upper()
    return _SHARED_CURRENT / f"{sport_u}_{THIS_SEASON_KEY}_current.json"


def last_season_path(sport: str, snap_dir: str | Path | None = None) -> Path:
    sport_u = (sport or "").strip().upper()
    root = Path(snap_dir) if snap_dir else _LOCAL_SNAP
    return root / f"{sport_u}_{LAST_SEASON_KEY}_regular.json"


def save_current_season(
    sport: str,
    overall_stats: dict | None,
    spread_total_stats: dict | None = None,
) -> Path | None:
    sport_u = (sport or "").strip().upper()
    stats = _jsonable_stats(overall_stats)
    if not sport_u or not stats:
        return None
    payload = {
        "sport": sport_u,
        "season": THIS_SEASON_KEY,
        "season_label": THIS_SEASON_LABEL,
        "source": "live_current",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall_stats": stats,
        "spread_total_stats": spread_total_stats
        if isinstance(spread_total_stats, dict)
        else {},
        "games_in_scope": max(
            int((m or {}).get("total") or 0) for m in stats.values()
        )
        if stats
        else 0,
    }
    try:
        _SHARED_CURRENT.mkdir(parents=True, exist_ok=True)
        path = current_path(sport_u)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        return None


def load_current_snap(sport: str) -> dict | None:
    path = current_path(sport)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("sport") != (sport or "").strip().upper():
        return None
    return data


def load_last_snap(sport: str, snap_dir: str | Path | None = None) -> dict | None:
    path = last_season_path(sport, snap_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("sport") and data.get("sport") != (sport or "").strip().upper():
        return None
    return data


def load_current_snaps() -> list[dict]:
    rows = []
    for sport in _DASHBOARD_SPORTS:
        snap = load_current_snap(sport)
        if snap:
            rows.append(snap)
    return rows


def load_last_snaps(snap_dir: str | Path | None = None) -> list[dict]:
    rows = []
    for sport in _DASHBOARD_SPORTS:
        snap = load_last_snap(sport, snap_dir)
        if snap:
            rows.append(snap)
    return rows


def last_season_model_cards(sport: str, snap_dir: str | Path | None = None) -> list[dict]:
    snap = load_last_snap(sport, snap_dir)
    overall = (snap or {}).get("overall_stats") or {}
    cards = []
    for key, label in _ML_MODELS:
        m = overall.get(key) or {}
        try:
            correct = int(m.get("correct") or 0)
            total = int(m.get("total") or 0)
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        acc = m.get("accuracy")
        if acc is None:
            acc = round(100.0 * correct / total, 1)
        cards.append(
            {
                "key": key,
                "label": label,
                "accuracy": acc,
                "record": f"{correct}-{total - correct}",
                "total": total,
            }
        )
    return cards


def parse_current_ml_from_results(html: str) -> dict | None:
    """Moneyline Accuracy by Model only — never Last Season's Results."""
    if not html:
        return None
    i = html.find("Moneyline Accuracy by Model")
    if i < 0:
        return None
    chunk = html[i : i + 12000]
    stop = re.search(
        r"Last Season(?:'s)? Results|<!-- ── Date Slider|class=\"date-nav\""
        r"|Consensus Based Betting Records",
        chunk,
        flags=re.I,
    )
    if stop:
        chunk = chunk[: stop.start()]
    stats: dict[str, dict] = {}
    for key, label in _ML_MODELS:
        m = re.search(
            rf"{re.escape(label)}</div>\s*"
            rf'<div class="model-acc"[^>]*>\s*([\d.]+)\s*%\s*</div>\s*'
            rf'<div class="model-rec"[^>]*>\s*(\d+)\s*-\s*(\d+)',
            chunk,
            flags=re.I,
        )
        if not m:
            continue
        wins, losses = int(m.group(2)), int(m.group(3))
        total = wins + losses
        if total <= 0:
            continue
        stats[key] = {
            "correct": wins,
            "total": total,
            "accuracy": float(m.group(1)),
        }
    return stats or None


def publish_from_results_html(html: str, sport: str) -> None:
    stats = parse_current_ml_from_results(html)
    if stats:
        save_current_season(sport, stats, None)


def last_season_row_html(sport: str, snap_dir: str | Path | None = None) -> str:
    cards = last_season_model_cards(sport, snap_dir)
    if not cards:
        return ""
    cells = []
    for m in cards:
        cells.append(
            '<div class="model-card">'
            f'<div class="model-label">{m["label"]}</div>'
            f'<div class="model-acc">{m["accuracy"]}%</div>'
            f'<div class="model-rec">{m["record"]}</div>'
            "</div>"
        )
    return (
        f'<h3 class="last-season-heading" style="text-align:center;font-size:1.15em;'
        f'margin:28px 0 8px;color:#0f172a;">Last Season\'s Results — {LAST_SEASON_LABEL}</h3>'
        '<p style="text-align:center;font-size:0.85em;color:#64748b;margin:0 0 12px;">'
        "Final regular season. This record does not change.</p>"
        f'<div class="model-grid" id="last-season-results">{"".join(cells)}</div>'
    )


def inject_last_season_row(html: str, sport: str, snap_dir: str | Path | None = None) -> str:
    if not html or 'id="last-season-results"' in html:
        return html
    block = last_season_row_html(sport, snap_dir)
    if not block:
        return html
    if "<!-- ── Date Slider ── -->" in html:
        return html.replace("<!-- ── Date Slider ── -->", block + "<!-- ── Date Slider ── -->", 1)
    html2, n = re.subn(
        r'(<div class="date-nav">)',
        block + r"\1",
        html,
        count=1,
        flags=re.I,
    )
    if n:
        return html2
    m = re.search(
        r'(<h3[^>]*>\s*Moneyline Accuracy by Model\s*</h3>\s*<div class="model-grid">)',
        html,
        flags=re.I,
    )
    if not m:
        return html
    start = m.end()
    depth = 1
    i = start
    while i < len(html) and depth:
        nxt_open = html.find("<div", i)
        nxt_close = html.find("</div>", i)
        if nxt_close < 0:
            break
        if 0 <= nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
    if depth:
        return html
    return html[:i] + block + html[i:]


def apply_results_season_split(html: str, sport: str, snap_dir: str | Path | None = None) -> str:
    """Write this-year JSON from the live Season board, then pin last year under it."""
    if not html:
        return html
    try:
        publish_from_results_html(html, sport)
    except Exception:
        pass
    try:
        return inject_last_season_row(html, sport, snap_dir)
    except Exception:
        return html
