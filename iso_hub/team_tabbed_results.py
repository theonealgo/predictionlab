#!/usr/bin/env python3
"""Soccer-style Moneyline | Spread | Totals results for hub team sports.

MLB UI SIGNED OFF — do not change without owner request.
Locked 2026-08-10. Do not modify MLB results/analytics helpers in this file
unless the owner unlocks (see notes/MLB_LOCKED.md / qa/MLB_SIGNED_OFF.txt).
WNBA / CFL / shared helpers may still change.

Builds markets payloads for MLB / WNBA (from live results HTML tallies + season
snapshots) and CFL (from isolation pipeline). Never imports NHL77FINAL.

Canonical Season sample (per market) = live Season Performance face cards /
Moneyline Accuracy by Model grid. Locked season_snapshots JSON is a separate
archive — show only under "Full Season Snapshot" labels, never as unlabeled
Season next to face cards / chart Season columns.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HUB_DIR = Path(__file__).resolve().parent
LIVE_ROOT = ROOT  # work2 backup — never the live predictionlabfix_work tree
SNAPSHOT_DIR = LIVE_ROOT / "data" / "season_snapshots"
CFL_ISO = Path.home() / "Documents/Personal/cfl"
UFC_ISO = Path.home() / "Documents/Personal/ufc"

# Match isolation render.MODEL_DELTAS (one Elo engine + fixed offsets).
CFL_MODEL_DELTAS = [
    ("Grinder2", 0.035),
    ("Takedown", 0.018),
    ("Edge", -0.012),
    ("XSharp", 0.045),
    ("Efficiency", -0.025),
    ("Sharp Consensus", 0.0),
]

# UFC component-model deltas (match isolation render.MODEL_DELTAS).
UFC_MODEL_DELTAS = [
    ("Grinder2", 0.028),
    ("Takedown", 0.012),
    ("Edge", -0.015),
    ("XSharp", 0.038),
    ("Efficiency", -0.022),
    ("Sharp Consensus", 0.0),
]

MODEL_ORDER = [
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
]

_EMOJI_MODEL = {
    "Grinder2": "Grinder2",
    "Takedown": "Takedown",
    "Edge": "Edge",
    "XSharp": "XSharp",
    "Sharp Consensus": "Sharp Consensus",
    "Efficiency": "Efficiency",
    "Spread": "Spread",
    "Over/Under": "Over/Under",
    "O/U": "Over/Under",
}


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _strip_emoji(label: str) -> str:
    s = re.sub(r"[^\w\s/+\-%.]", "", label or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _parse_record(rec: str) -> tuple[int, int, int]:
    """Parse '9-6' or '10-3-2' → w, l, pushes."""
    parts = [p for p in re.split(r"[-–]", (rec or "").strip()) if p != ""]
    nums = []
    for p in parts:
        try:
            nums.append(int(float(p)))
        except ValueError:
            continue
    if len(nums) >= 3:
        return nums[0], nums[1], nums[2]
    if len(nums) == 2:
        return nums[0], nums[1], 0
    if len(nums) == 1:
        return nums[0], 0, 0
    return 0, 0, 0


def _units_from_wl(w: int, l: int, *, odds: float = -110.0) -> float | None:
    """Flat 1u at American odds (default -110)."""
    n = int(w) + int(l)
    if n <= 0:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        o = -110.0
    if o == 0:
        return None
    win_payout = (100.0 / abs(o)) if o < 0 else (o / 100.0)
    return round(float(w) * win_payout - float(l), 1)


def _wl_block(w: int, l: int, p: int = 0, **extra) -> dict[str, Any]:
    n = int(w) + int(l)
    pct = round(100.0 * w / n, 1) if n else None
    units = _units_from_wl(w, l)
    out = {
        "w": int(w),
        "l": int(l),
        "n": n,
        "pushes": int(p),
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "units": units,
    }
    out.update(extra)
    return out


def _model_block(name: str, pct: float | None, rec: str) -> dict[str, Any]:
    w, l, p = _parse_record(rec)
    n = w + l
    if pct is None and n > 0:
        pct = round(100.0 * w / n, 1)
    return {
        "w": w,
        "l": l,
        "n": n,
        "pushes": p,
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "units": _units_from_wl(w, l),
    }


def _empty_ml_tally(games: int = 0, **extra) -> dict[str, Any]:
    models = {
        name: {"w": 0, "l": 0, "n": 0, "pct": None, "record": "0-0"}
        for name in MODEL_ORDER
    }
    out = {"label": extra.get("label", ""), "games": games, "models": models}
    out.update({k: v for k, v in extra.items() if k != "label"})
    return out


def _face_tally(games: int, w: int, l: int, p: int = 0, **extra) -> dict[str, Any]:
    n = w + l
    # Prefer published Season Performance % when provided (O/U can differ from W/(W+L)).
    pct = extra.pop("pct", None)
    if pct is None and n:
        pct = round(100.0 * w / n, 1)
    units = extra.pop("units", None)
    if units is None:
        units = _units_from_wl(w, l)
    label = extra.pop("model_label", "Prediction Lab")
    block = {
        "games": games if games else n,
        "w": w,
        "l": l,
        "n": n,
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "units": units,
        "models": {
            label: {
                "w": w,
                "l": l,
                "n": n,
                "pct": pct,
                "record": f"{w}-{l}" + (f"-{p}" if p else ""),
                "units": units,
            }
        },
        "model_order": [label],
    }
    block.update(extra)
    return block


def _parse_pct(raw: str) -> float | None:
    raw = (raw or "").strip().replace("%", "")
    if not raw or raw in ("—", "-", "N/A", "n/a"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_tally_sections(html: str) -> list[dict[str, Any]]:
    """Parse live daily-tally blocks into structured cards."""
    sections: list[dict[str, Any]] = []
    parts = re.split(r'<div class="daily-tally"[^>]*>', html or "", flags=re.I)
    for part in parts[1:]:
        h2_m = re.search(r"<h2[^>]*>(.*?)</h2>", part, re.I | re.S)
        if not h2_m:
            continue
        title = re.sub(r"<[^>]+>", "", h2_m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        games_m = re.search(r"\((\d+)\s+games?\)", title, re.I)
        games = int(games_m.group(1)) if games_m else 0
        date_m = re.search(r"—\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", title)
        date_range_m = re.search(
            r"—\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s+to\s+([0-9]{4}-[0-9]{2}-[0-9]{2})",
            title,
            re.I,
        )
        cards = []
        for cm in re.finditer(
            r'<div class="daily-model">([^<]+)</div>\s*'
            r'<div class="daily-acc"[^>]*>([^<]*)</div>\s*'
            r'<div class="daily-rec">([^<]*)</div>',
            part,
            re.I | re.S,
        ):
            raw_name = _strip_emoji(cm.group(1))
            name = _EMOJI_MODEL.get(raw_name, raw_name)
            cards.append(
                {
                    "name": name,
                    "pct": _parse_pct(cm.group(2)),
                    "record": (cm.group(3) or "").strip(),
                }
            )
        kind = "other"
        low = title.lower()
        if "last night" in low:
            kind = "last_night"
        elif "last 7" in low:
            kind = "last_7"
        sections.append(
            {
                "kind": kind,
                "title": title,
                "games": games,
                "date": date_m.group(1) if date_m and not date_range_m else None,
                "date_from": date_range_m.group(1) if date_range_m else None,
                "date_to": date_range_m.group(2) if date_range_m else None,
                "cards": cards,
            }
        )
    return sections


def _extract_season_roi(html: str) -> dict[str, dict[str, Any]]:
    """Parse Season Performance ROI grid (Moneyline / Spread / O/U)."""
    out: dict[str, dict[str, Any]] = {}
    i = (html or "").find("Season Performance")
    if i < 0:
        return out
    chunk = html[i : i + 6000]
    for key, pat in (
        (
            "moneyline",
            r"Moneyline(?:\s*\(([^)]+)\))?[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)",
        ),
        (
            "spread",
            r"Spread(?:\s*\(([^)]+)\))?[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)",
        ),
        (
            "totals",
            r"O/U(?:\s*\(([^)]+)\))?[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)",
        ),
    ):
        m = re.search(pat, chunk, re.I | re.S)
        if not m:
            continue
        label = _strip_emoji((m.group(1) or "").strip()) or (
            "Grinder2" if key == "moneyline" else "Prediction Lab"
        )
        label = _EMOJI_MODEL.get(label, label)
        pct = _parse_pct(m.group(2))
        w, l, p = _parse_record(m.group(3))
        out[key] = {
            "pct": pct,
            "w": w,
            "l": l,
            "pushes": p,
            "n": w + l,
            "games": w + l,
            "model_label": label,
        }
    return out


def _extract_season_ml_by_model(html: str) -> dict[str, Any] | None:
    """Parse 'Moneyline Accuracy by Model' (all six models when published)."""
    i = (html or "").find("Moneyline Accuracy by Model")
    if i < 0:
        return None
    chunk = html[i : i + 8000]
    models: dict[str, Any] = {}
    order: list[str] = []
    for m in re.finditer(
        r'<div class="model-label">([^<]+)</div>\s*'
        r'<div class="model-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="model-rec"[^>]*>([^<]*)</div>',
        chunk,
        re.I | re.S,
    ):
        raw = _strip_emoji(m.group(1))
        name = _EMOJI_MODEL.get(raw, raw)
        pct = _parse_pct(m.group(2))
        rec = (m.group(3) or "").strip()
        if pct is None and (not rec or rec in ("—", "-", "N/A", "n/a")):
            continue
        w, l, p = _parse_record(rec)
        n = w + l
        if n <= 0 and pct is None:
            continue
        models[name] = _model_block(name, pct, rec)
        if name not in order:
            order.append(name)
    if not models:
        return None
    games = max((mm.get("n") or 0) for mm in models.values())
    block = _empty_ml_tally(games, label="Season", ready=True)
    block["models"].update(models)
    block["model_order"] = order or [n for n in MODEL_ORDER if n in models]
    return block


def _tally_models_from_finals(finals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate moneyline W-L per named model from graded game cards."""
    tallies: dict[str, list[int]] = {n: [0, 0] for n in MODEL_ORDER}
    for row in finals or []:
        models = row.get("models") or {}
        for name, m in models.items():
            key = _EMOJI_MODEL.get(_strip_emoji(name), _strip_emoji(name))
            if key not in tallies:
                tallies[key] = [0, 0]
            ok = m.get("correct")
            if ok is True:
                tallies[key][0] += 1
            elif ok is False:
                tallies[key][1] += 1
    out: dict[str, dict[str, Any]] = {}
    for name, (w, l) in tallies.items():
        if w + l <= 0:
            continue
        out[name] = _wl_block(w, l)
    return out


def _tally_market_from_finals(
    finals: list[dict[str, Any]], market_key: str
) -> dict[str, Any] | None:
    w = l = p = 0
    for row in finals or []:
        slot = row.get(market_key) or {}
        if not slot:
            continue
        if slot.get("push"):
            p += 1
            continue
        ok = slot.get("correct")
        if ok is True:
            w += 1
        elif ok is False:
            l += 1
    if w + l + p <= 0:
        return None
    return _wl_block(w, l, p)


def _backfill_blank_ml_models(
    season_ml: dict[str, Any], finals: list[dict[str, Any]]
) -> dict[str, Any]:
    """Fill models with blank/zero season rows from graded finals (e.g. Efficiency —)."""
    if not season_ml:
        return season_ml
    models = season_ml.setdefault("models", {})
    from_cards = _tally_models_from_finals(finals)
    order = list(season_ml.get("model_order") or MODEL_ORDER)
    filled = False
    for name in MODEL_ORDER:
        cur = models.get(name) or {}
        n = int(cur.get("n") or 0)
        pct = cur.get("pct")
        blank = n <= 0 and pct is None
        if not blank:
            # Ensure units present on published rows
            if cur.get("units") is None and n > 0:
                cur["units"] = _units_from_wl(int(cur.get("w") or 0), int(cur.get("l") or 0))
                models[name] = cur
            continue
        src = from_cards.get(name)
        if not src:
            continue
        models[name] = src
        filled = True
        if name not in order:
            order.append(name)
    if filled:
        season_ml["models"] = models
        season_ml["model_order"] = [n for n in MODEL_ORDER if n in models and (models[n].get("n") or 0) > 0] or order
        season_ml["ready"] = True
        season_ml["games"] = max(
            int(season_ml.get("games") or 0),
            max((models[n].get("n") or 0) for n in models) if models else 0,
        )
    return season_ml


def _best_model(models: dict[str, Any]) -> dict[str, Any] | None:
    best = None
    for name, m in (models or {}).items():
        n = int(m.get("n") or 0)
        pct = m.get("pct")
        if n <= 0 or pct is None:
            continue
        cand = {"name": name, "pct": pct, "record": m.get("record"), "n": n, "units": m.get("units")}
        if best is None:
            best = cand
            continue
        if pct > best["pct"] or (pct == best["pct"] and n > best["n"]):
            best = cand
    return best


def build_results_analytics(
    *,
    sport: str,
    tallies_ml: dict[str, Any],
    season_sp: dict[str, Any],
    season_ou: dict[str, Any],
    last7_sp: dict[str, Any],
    last7_ou: dict[str, Any],
    finals: list[dict[str, Any]],
    snap: dict[str, Any] | None = None,
    ml_only: bool = False,
) -> dict[str, Any]:
    """Best-model + Efficiency breakout (display only; no health dashboard).

    ml_only sports (UFC/Tennis) omit Spread/Total efficiency cards entirely —
    never ship blank dash shells for markets that do not exist.
    """
    ln = (tallies_ml or {}).get("last_night") or {}
    l7 = (tallies_ml or {}).get("last_7") or {}
    season = (tallies_ml or {}).get("season") or {}

    best = {
        "today": _best_model(ln.get("models") or {}),
        "last_7": _best_model(l7.get("models") or {}),
        "season": _best_model(season.get("models") or {}),
    }

    season_models = season.get("models") or {}
    peer_n = max(
        (int((season_models.get(n) or {}).get("n") or 0) for n in MODEL_ORDER if n != "Efficiency"),
        default=0,
    )
    eff_ml = dict(season_models.get("Efficiency") or {})
    eff_ml_source = "full_season" if (eff_ml.get("n") or 0) > 0 else "none"
    if (eff_ml.get("n") or 0) <= 0:
        # Prefer last_7 Efficiency if season still blank
        eff_ml = dict((l7.get("models") or {}).get("Efficiency") or eff_ml)
        if (eff_ml.get("n") or 0) > 0:
            eff_ml_source = "graded_sample"
    # If season Efficiency exists but is a thin graded subset vs peer season rows,
    # label as Graded Sample (display only — never recompute scores).
    if eff_ml_source == "full_season" and peer_n > 0 and (eff_ml.get("n") or 0) < max(40, int(peer_n * 0.45)):
        eff_ml_source = "graded_sample"

    # Spread / Total breakout: prefer face Season Performance (canonical sample).
    # Locked snapshot PL counters are a different universe — only used when face
    # is missing, and then labeled "Full Season Snapshot".
    pl_sp = None
    pl_ou = None
    sp_source = "none"
    ou_source = "none"
    face_sp_n = int((season_sp or {}).get("n") or 0)
    face_ou_n = int((season_ou or {}).get("n") or 0)
    if face_sp_n > 0:
        pl_sp = _wl_block(
            int(season_sp.get("w") or 0),
            int(season_sp.get("l") or 0),
            int(season_sp.get("pushes") or 0),
        )
        if season_sp.get("pct") is not None:
            pl_sp["pct"] = season_sp.get("pct")
        sp_source = "face_season"
    if face_ou_n > 0:
        pl_ou = _wl_block(
            int(season_ou.get("w") or 0),
            int(season_ou.get("l") or 0),
            int(season_ou.get("pushes") or 0),
        )
        if season_ou.get("pct") is not None:
            pl_ou["pct"] = season_ou.get("pct")
        ou_source = "face_season"

    if pl_sp is None or pl_ou is None:
        try:
            candidates = sorted(
                SNAPSHOT_DIR.glob(f"{sport.upper()}_*_regular.json"), reverse=True
            )
            if candidates:
                blob = json.loads(candidates[0].read_text())
                st = blob.get("spread_total_stats") or {}
                sp = blob.get("season_perf") or {}
                if pl_sp is None:
                    w = int(st.get("pl_spread_covered") or 0)
                    n = int(st.get("pl_spread_graded") or 0)
                    pushes = int(st.get("pl_spread_pushes") or 0)
                    decided = max(0, n - pushes)
                    if decided > 0:
                        pl_sp = _wl_block(min(w, decided), max(0, decided - w), pushes)
                        sp_source = "snapshot"
                if pl_ou is None:
                    w = int(st.get("pl_total_correct") or sp.get("ou_correct") or 0)
                    n = int(st.get("pl_total_graded") or sp.get("ou_graded") or 0)
                    pushes = int(st.get("pl_total_pushes") or 0)
                    decided = max(0, n - pushes)
                    if decided > 0:
                        pl_ou = _wl_block(min(w, decided), max(0, decided - w), pushes)
                        ou_source = "snapshot"
        except Exception:
            pass

    if pl_sp is None:
        pl_sp = _tally_market_from_finals(finals, "spread") or _wl_block(0, 0)
        if (pl_sp.get("n") or 0) > 0:
            sp_source = "graded_sample"
    if pl_ou is None:
        pl_ou = _tally_market_from_finals(finals, "totals") or _wl_block(0, 0)
        if (pl_ou.get("n") or 0) > 0:
            ou_source = "graded_sample"

    if (eff_ml.get("n") or 0) <= 0:
        card_ml = _tally_models_from_finals(finals).get("Efficiency")
        if card_ml:
            eff_ml = dict(card_ml)
            eff_ml_source = "graded_sample"

    # Isolation sports (CFL etc.) often publish face "Prediction Lab" only —
    # never leave Efficiency · Moneyline blank when season ML grades exist.
    if (eff_ml.get("n") or 0) <= 0:
        for alias in ("Prediction Lab", "Sharp Consensus", "Edge", "XSharp"):
            face = dict(season_models.get(alias) or {})
            if (face.get("n") or 0) > 0:
                eff_ml = face
                eff_ml_source = "full_season"
                break
    if (eff_ml.get("n") or 0) <= 0:
        # Last resort: aggregate face_pick correctness from finals cards
        w = l = 0
        for f in finals or []:
            if f.get("correct") is True:
                w += 1
            elif f.get("correct") is False:
                l += 1
        if w + l > 0:
            eff_ml = _wl_block(w, l)
            eff_ml_source = "graded_sample"

    def _face_model_label(block: dict[str, Any], default: str = "Prediction Lab") -> str:
        models = (block or {}).get("models") or {}
        order = list((block or {}).get("model_order") or [])
        for name in order:
            if ((models.get(name) or {}).get("n") or 0) > 0:
                return name
        for name, row in models.items():
            if (row or {}).get("n"):
                return name
        return default

    def _eff_label(market: str, source: str, *, face_block: dict[str, Any] | None = None) -> str:
        if source == "graded_sample":
            return f"Efficiency Season (Graded Sample) · {market}"
        if source == "face_season":
            model = _face_model_label(face_block or {})
            return f"Season · {market} ({model})"
        if source == "snapshot":
            return f"Full Season Snapshot · {market}"
        if source == "full_season":
            return f"Efficiency Season · {market}"
        return f"Efficiency · {market}"

    def _eff_row(label: str, block: dict[str, Any], source: str) -> dict[str, Any]:
        n = int(block.get("n") or 0)
        pct = block.get("pct")
        rec = block.get("record")
        units = block.get("units")
        if n > 0:
            if pct is None and int(block.get("w") or 0) + int(block.get("l") or 0) > 0:
                w = int(block.get("w") or 0)
                l = int(block.get("l") or 0)
                pct = round(100.0 * w / (w + l), 1)
            if not rec or rec in ("—", "-", "N/A"):
                rec = f"{int(block.get('w') or 0)}-{int(block.get('l') or 0)}"
            if units is None:
                units = _units_from_wl(int(block.get("w") or 0), int(block.get("l") or 0))
        return {
            "label": label,
            "accuracy": pct,
            "record": rec if n > 0 else (rec or "—"),
            "units": units,
            "n": n,
            "source": source,
            "graded_games": n,
        }

    efficiency: dict[str, Any] = {
        "moneyline": _eff_row(_eff_label("Moneyline", eff_ml_source), eff_ml, eff_ml_source),
    }
    # Moneyline-only fighter/racket sports: hide Spread/Total efficiency entirely.
    if not ml_only and (sport or "").lower() not in ("ufc", "tennis"):
        efficiency["spread"] = _eff_row(
            _eff_label("Spread", sp_source, face_block=season_sp),
            pl_sp or {},
            sp_source,
        )
        efficiency["total"] = _eff_row(
            _eff_label("Total", ou_source, face_block=season_ou),
            pl_ou or {},
            ou_source,
        )

    return {
        "best_performing": best,
        "efficiency_breakout": efficiency,
        "ml_only": bool(ml_only or (sport or "").lower() in ("ufc", "tennis")),
    }


def patch_mlb_season_efficiency_html(html: str) -> str:
    """Replace blank Season Efficiency ML cells using graded Efficiency boxes."""
    if not html or "Moneyline Accuracy by Model" not in html:
        return html
    # Aggregate Efficiency correct/wrong from all result cards on the page
    w = l = 0
    for m in re.finditer(
        r'<div class="pc-box([^"]*)"[^>]*>\s*'
        r'<div class="pc-name">([^<]*Efficiency[^<]*)</div>',
        html,
        re.I,
    ):
        cls = m.group(1) or ""
        if "correct" in cls:
            w += 1
        elif "wrong" in cls:
            l += 1
    if w + l <= 0:
        # Still relabel if Efficiency already has graded numbers
        return _relabel_efficiency_season_html(html)
    block = _wl_block(w, l)
    pct_s = f"{block['pct']}%"
    rec_s = block["record"]
    units_s = f"{block['units']:+.1f}u" if block.get("units") is not None else ""

    def _repl(match: re.Match) -> str:
        units_html = f'\n                <div class="model-units">{units_s}</div>' if units_s else ""
        return (
            f'{match.group(1)}'
            f'<div class="model-acc">{pct_s}</div>\n'
            f'                <div class="model-rec">{rec_s}</div>'
            f"{units_html}"
        )

    patched, nsub = re.subn(
        r'(<div class="model-label">[^<]*Efficiency</div>\s*)'
        r'<div class="model-acc"[^>]*>\s*(?:—|&mdash;|N/A|-)\s*</div>\s*'
        r'<div class="model-rec"[^>]*>\s*(?:—|&mdash;|N/A|-)\s*</div>',
        _repl,
        html,
        count=1,
        flags=re.I,
    )
    if nsub:
        html = patched
    return _relabel_efficiency_season_html(html)


def _relabel_efficiency_season_html(html: str) -> str:
    """Prefer full-season label; else mark Graded Sample when peer models dwarf Efficiency n."""
    if not html or "Moneyline Accuracy by Model" not in html:
        return html
    i = html.find("Moneyline Accuracy by Model")
    chunk = html[i : i + 9000]
    rows = list(
        re.finditer(
            r'<div class="model-label">([^<]+)</div>\s*'
            r'<div class="model-acc"[^>]*>([^<]*)</div>\s*'
            r'<div class="model-rec"[^>]*>([^<]*)</div>',
            chunk,
            re.I | re.S,
        )
    )
    if not rows:
        return html
    peer_n = 0
    eff_n = 0
    eff_match = None
    for m in rows:
        name = _strip_emoji(m.group(1))
        w, l, _p = _parse_record(m.group(3) or "")
        n = w + l
        if "Efficiency" in name:
            eff_n = n
            eff_match = m
        elif n > peer_n:
            peer_n = n
    if not eff_match or eff_n <= 0:
        return html
    graded = peer_n > 0 and eff_n < max(40, int(peer_n * 0.45))
    label = (
        "⚡ Efficiency Season (Graded Sample)"
        if graded
        else "⚡ Efficiency Season"
    )
    old_label = eff_match.group(1)
    # Replace only the season-grid Efficiency label occurrence
    html = html.replace(
        f'<div class="model-label">{old_label}</div>',
        f'<div class="model-label">{label}</div>',
        1,
    )
    return html


def enrich_mlb_tally_units_html(html: str) -> str:
    """Attach Units to daily/season model tally rows when a W-L record exists (display only)."""
    if not html:
        return html

    def _units_for_rec(rec: str) -> str:
        w, l, _p = _parse_record(rec)
        u = _units_from_wl(w, l)
        return f"{u:+.1f}u" if u is not None else ""

    html = re.sub(
        r'(<div class="daily-model">[^<]+</div>\s*'
        r'<div class="daily-acc">)([^<]*)(</div>\s*'
        r'<div class="daily-rec">)([^<]*)(</div>)',
        lambda m: (
            f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}"
            if re.search(r"[+\-]?\d+(?:\.\d+)?u", m.group(4), re.I)
            else (
                f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}\n"
                f'                    <div class="model-units">{_units_for_rec(m.group(4))}</div>'
                if _units_for_rec(m.group(4))
                else m.group(0)
            )
        ),
        html,
        flags=re.I,
    )

    def _season_repl(m: re.Match) -> str:
        label, acc, rec = m.group(1), m.group(2), m.group(3)
        if "model-units" in m.group(0):
            return m.group(0)
        units = _units_for_rec(rec)
        if not units:
            return m.group(0)
        return (
            f'<div class="model-label">{label}</div>\n'
            f'                <div class="model-acc">{acc}</div>\n'
            f'                <div class="model-rec">{rec}</div>\n'
            f'                <div class="model-units">{units}</div>'
        )

    html = re.sub(
        r'<div class="model-label">([^<]+)</div>\s*'
        r'<div class="model-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="model-rec"[^>]*>([^<]*)</div>'
        r'(?!\s*<div class="model-units")',
        _season_repl,
        html,
        flags=re.I,
    )
    return html


def _mlb_run_line_record_cards_from_snapshot() -> str:
    """HTML cards for PL / XSharp season run-line ATS when snapshot has data."""
    try:
        snap = _snapshot_season("mlb")
        season = (snap.get("spread") or {}) if isinstance(snap, dict) else {}
        models = season.get("models") or {}
    except Exception:
        models = {}
    cards = []
    for name, title in (
        ("Prediction Lab", "PL run line"),
        ("XSharp", "XSharp run line"),
    ):
        m = models.get(name) or {}
        n = int(m.get("n") or 0)
        if n <= 0:
            continue
        pct = m.get("pct")
        pct_s = f"{pct}%" if pct is not None else "—"
        rec = m.get("record") or f"{int(m.get('w') or 0)}-{int(m.get('l') or 0)}"
        units = m.get("units")
        units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ""
        cards.append(
            f'<div class="pl-analytics-card">'
            f'<div class="pl-analytics-k">{title}</div>'
            f'<div class="pl-analytics-v">{pct_s}</div>'
            f'<div class="pl-analytics-sub">Record {rec}'
            f'{" · " + units_s if units_s else ""} · {n} graded</div>'
            f"</div>"
        )
    if not cards:
        return ""
    return (
        '<h3 class="pl-analytics-title">Run Line Records (Full Season Snapshot)</h3>'
        '<p style="text-align:center;margin:0 0 10px;font-size:0.78rem;color:#64748b;">'
        "Locked archive sample — not the same universe as Season Performance / chart Season."
        "</p>"
        f'<div class="pl-analytics-grid">{"".join(cards)}</div>'
    )


def inject_mlb_results_analytics_html(
    html: str,
    analytics: dict[str, Any],
    *,
    sport: str = "mlb",
) -> str:
    """Inject Best Model / Efficiency breakout into normal results (no health dashboard)."""
    if not html or not analytics:
        return html
    best = analytics.get("best_performing") or {}
    eff = analytics.get("efficiency_breakout") or {}
    sport_l = (sport or "mlb").lower()
    # Run-line season cards are MLB snapshot only — never leak onto CFL/etc.
    run_line_block = _mlb_run_line_record_cards_from_snapshot() if sport_l == "mlb" else ""

    def _best_card(label: str, row: dict | None) -> str:
        if not row:
            return (
                f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
                f'<div class="pl-analytics-v muted">—</div></div>'
            )
        units = row.get("units")
        units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ""
        return (
            f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
            f'<div class="pl-analytics-name">{row.get("name") or "—"}</div>'
            f'<div class="pl-analytics-v">{row.get("pct")}%</div>'
            f'<div class="pl-analytics-sub">{row.get("record") or ""}'
            f'{" · " + units_s if units_s else ""}</div></div>'
        )

    def _eff_card(row: dict | None) -> str:
        row = row or {}
        n = int(row.get("n") or row.get("graded_games") or 0)
        units = row.get("units")
        units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ("—" if n <= 0 else "+0.0u")
        acc = row.get("accuracy")
        # Never show "—" for accuracy/record when graded data exists
        if n > 0 and acc is None:
            acc_s = "0%"
        else:
            acc_s = f"{acc}%" if acc is not None else "—"
        rec = row.get("record") or ("—" if n <= 0 else "0-0")
        games_s = f" · {n} graded games" if n > 0 else ""
        return (
            f'<div class="pl-analytics-card">'
            f'<div class="pl-analytics-k">{row.get("label") or "Efficiency Season"}</div>'
            f'<div class="pl-analytics-v">{acc_s}</div>'
            f'<div class="pl-analytics-sub">Accuracy {acc_s} · Record {rec} · Units {units_s}{games_s}</div>'
            f"</div>"
        )

    panel = f"""
        <section class="pl-mlb-analytics" aria-label="Results analytics">
          <h3 class="pl-analytics-title">Best Performing Model</h3>
          <div class="pl-analytics-grid">
            {_best_card("Today", best.get("today"))}
            {_best_card("Last 7", best.get("last_7"))}
            {_best_card("Season", best.get("season"))}
          </div>
          <h3 class="pl-analytics-title">Efficiency by Market</h3>
          <div class="pl-analytics-grid">
            {_eff_card(eff.get("moneyline"))}
            {"" if (analytics.get("ml_only") or sport_l in ("ufc", "tennis")) else _eff_card(eff.get("spread"))}
            {"" if (analytics.get("ml_only") or sport_l in ("ufc", "tennis")) else _eff_card(eff.get("total"))}
          </div>
          {run_line_block}
        </section>
        <style>
          .pl-mlb-analytics{{margin:18px 0 22px;padding:16px;border:1px solid rgba(15,23,42,.12);border-radius:12px;background:#f8fafc;max-width:1100px;margin-left:auto;margin-right:auto}}
          .pl-analytics-title{{margin:0 0 10px;font-size:1.05rem;color:#0f172a;text-align:center}}
          .pl-analytics-title+ .pl-analytics-grid{{margin-bottom:16px}}
          .pl-analytics-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
          .pl-analytics-card{{background:#fff;border:1px solid rgba(15,23,42,.1);border-radius:10px;padding:12px;text-align:center}}
          .pl-analytics-k{{font-size:.75rem;color:#475569;margin-bottom:4px}}
          .pl-analytics-name{{font-weight:700;color:#0f172a;margin-bottom:2px}}
          .pl-analytics-v{{font-size:1.45rem;font-weight:800;color:#0f172a}}
          .pl-analytics-v.muted{{color:#94a3b8}}
          .pl-analytics-sub{{font-size:.8rem;color:#475569;margin-top:4px}}
          @media(max-width:720px){{.pl-analytics-grid{{grid-template-columns:1fr}}}}
        </style>
        """

    # Insert after Moneyline Accuracy by Model grid (before type-toggle)
    m = re.search(
        r'(Moneyline Accuracy by Model[\s\S]*?</div>\s*</div>\s*)(\s*<!--\s*──\s*Type Toggle| <div class="type-toggle")',
        html,
        re.I,
    )
    if m:
        return html[: m.end(1)] + panel + html[m.end(1) :]
    # Fallback: after Season Performance block
    i = html.find("Moneyline Accuracy by Model")
    if i >= 0:
        j = html.find('<div class="type-toggle"', i)
        if j < 0:
            j = html.find("<!-- ── Type Toggle", i)
        if j >= 0:
            return html[:j] + panel + html[j:]

    # Sandbox team sports: insert AFTER the full Season Performance daily-tally
    # (balanced walk). Never splice into the middle of daily-tally-grid — that
    # created the tall Sharp Consensus / Graded picks side-column wireframe.
    def _balanced_div_end(src: str, open_start: int) -> int:
        tag_end = src.find(">", open_start)
        if tag_end < 0:
            return -1
        j = tag_end + 1
        depth = 1
        while j < len(src) and depth > 0:
            nxt_o = src.find("<div", j)
            nxt_c = src.find("</div>", j)
            if nxt_c < 0:
                return -1
            if nxt_o >= 0 and nxt_o < nxt_c:
                depth += 1
                j = nxt_o + 4
            else:
                depth -= 1
                if depth == 0:
                    return nxt_c + 6
                j = nxt_c + 6
        return -1

    season_i = html.find("Season Performance")
    if season_i >= 0:
        open_m = None
        for m_open in re.finditer(r'<div\b[^>]*class="[^"]*\bdaily-tally\b[^"]*"[^>]*>', html, re.I):
            if m_open.start() < season_i and (open_m is None or m_open.start() > open_m.start()):
                # prefer the daily-tally that contains this Season Performance
                end_try = _balanced_div_end(html, m_open.start())
                if end_try > season_i:
                    open_m = m_open
        if open_m:
            end = _balanced_div_end(html, open_m.start())
            if end > 0:
                return html[:end] + panel + html[end:]

    for pat in (
        r'(class="pl-view-toggle"[\s\S]*?</div>)',
    ):
        m2 = re.search(pat, html, re.I)
        if m2:
            return html[: m2.end(1)] + panel + html[m2.end(1) :]
    # Last resort: into main container
    m3 = re.search(r'(<div class="container\b[^"]*"[^>]*>)', html, re.I)
    if m3:
        return html[: m3.end(1)] + panel + html[m3.end(1) :]
    return html


_CONSENSUS_BUCKETS = (
    "6/6 agree",
    "5/6 agree",
    "4/6 agree",
    "3/6 agree",
    "2/6 agree",
    "1/6 / no consensus",
)

# CFL only (same as signed-off sandbox). Do not use for MLB.
_CONSENSUS_BUCKETS_SMART = (
    "6/6 unanimous",
    "5/6 — one dissent",
    "4/6 — two dissent",
    "3/6 split",
)


def _fold_agree_n(agree_n: int) -> int:
    n = int(agree_n or 0)
    if n >= 6:
        return 6
    if n <= 0:
        return 0
    if n == 3:
        return 3
    return max(n, 6 - n)


def _consensus_bucket_label(agree_n: int) -> str:
    if agree_n >= 6:
        return "6/6 agree"
    if agree_n == 5:
        return "5/6 agree"
    if agree_n == 4:
        return "4/6 agree"
    if agree_n == 3:
        return "3/6 agree"
    if agree_n == 2:
        return "2/6 agree"
    return "1/6 / no consensus"


def _consensus_bucket_label_smart(agree_n: int) -> str:
    n = _fold_agree_n(agree_n)
    if n >= 6:
        return "6/6 unanimous"
    if n == 5:
        return "5/6 — one dissent"
    if n == 4:
        return "4/6 — two dissent"
    return "3/6 split"


def _consensus_record_cell(items: list[dict[str, Any]]) -> str:
    w = sum(1 for i in items if i.get("grade") == "WIN")
    l = sum(1 for i in items if i.get("grade") == "LOSS")
    p = sum(1 for i in items if i.get("grade") == "PUSH")
    decided = w + l
    if decided == 0 and p == 0:
        return "—"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    if decided == 0:
        return rec
    pct = 100.0 * w / decided
    return f"{rec} <span style='color:#64748b'>({pct:.0f}%)</span>"


def _consensus_agreements_from_finals(finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Majority-side ML W-L when all 6 named models have a locked pick."""
    out: list[dict[str, Any]] = []
    for g in finals or []:
        models = g.get("models") or {}
        sides: list[str] = []
        for name in MODEL_ORDER:
            m = models.get(name) or {}
            pick = str(m.get("pick") or "").strip()
            if not pick or pick.lower() in ("n/a", "na", "—", "-", "–"):
                continue
            sides.append(pick)
        if len(sides) < 6:
            continue
        counts = Counter(s.lower() for s in sides)
        top_n = counts.most_common(1)[0][1]
        # Map majority key back to original casing
        maj_key = counts.most_common(1)[0][0]
        majority = next((s for s in sides if s.lower() == maj_key), sides[0])
        if len(counts) >= 2 and counts.most_common(2)[0][1] == counts.most_common(2)[1][1]:
            top_n = 3
            sc = (models.get("Sharp Consensus") or {}).get("pick") or majority
            majority = str(sc)
        hs, aa = g.get("home_score"), g.get("away_score")
        home = str(g.get("home_team_id") or "")
        away = str(g.get("away_team_id") or "")
        try:
            hs_i = int(hs) if hs is not None else None
            aa_i = int(aa) if aa is not None else None
        except (TypeError, ValueError):
            continue
        if hs_i is None or aa_i is None:
            continue
        if hs_i == aa_i:
            grade = "PUSH"
        else:
            winner = home if hs_i > aa_i else away
            grade = (
                "WIN"
                if winner and majority.lower() == winner.lower()
                else "LOSS"
            )
        dk = str(g.get("game_date") or "")[:10]
        out.append({"agree_n": top_n, "grade": grade, "game_date": dk})
    return out


def build_consensus_records_html(
    finals: list[dict[str, Any]],
    *,
    last_night_key: str | None = None,
    sport: str = "",
) -> str:
    """HTML for Consensus Based Betting Records. Empty string if <6-model data."""
    agreements = _consensus_agreements_from_finals(finals)
    if not agreements:
        return ""
    smart = (sport or "").strip().lower() == "cfl"
    labels = _CONSENSUS_BUCKETS_SMART if smart else _CONSENSUS_BUCKETS
    label_fn = _consensus_bucket_label_smart if smart else _consensus_bucket_label
    now = datetime.now(ZoneInfo("America/New_York"))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    past_dates = sorted(
        {
            str(g.get("game_date") or "")[:10]
            for g in finals or []
            if str(g.get("game_date") or "")[:10] < today
        }
    )
    ln_key = last_night_key or (past_dates[-1] if past_dates else yesterday)
    cut7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    cut30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    def period(pred) -> list[dict[str, Any]]:
        return [a for a in agreements if pred(str(a.get("game_date") or ""))]

    ln = period(lambda d: d == ln_key)
    d7 = period(lambda d: d >= cut7)
    d30 = period(lambda d: d >= cut30)

    def by_bucket(items: list[dict[str, Any]]) -> dict[str, list]:
        buckets = {b: [] for b in labels}
        for a in items:
            n = int(a.get("agree_n") or 0)
            if smart:
                n = _fold_agree_n(n)
            label = label_fn(n)
            buckets.setdefault(label, []).append(a)
        return buckets

    ln_b, d7_b, d30_b = by_bucket(ln), by_bucket(d7), by_bucket(d30)
    rows_html = []
    for label in labels:
        rows_html.append(
            "<tr>"
            f'<td class="bucket">{label}</td>'
            f"<td>{_consensus_record_cell(ln_b.get(label, []))}</td>"
            f"<td>{_consensus_record_cell(d7_b.get(label, []))}</td>"
            f"<td>{_consensus_record_cell(d30_b.get(label, []))}</td>"
            "</tr>"
        )
    ln_hdr = f"Last night ({ln_key})" if ln_key else "Last night"
    sub = (
        "Moneyline on the majority side. 5/6 and 1/6 are the same games; "
        "4/6 and 2/6 are the same games. 3/6 is the only split. "
        "Graded only when a pre-game pick was locked."
        if smart
        else "Moneyline record when model sides agree. Graded only when a pre-game pick was locked."
    )
    return f"""
    <div class="pl-consensus-records" id="pl-consensus-records">
      <h2>Consensus Based Betting Records</h2>
      <p class="sub">
        {sub}
      </p>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Agreement</th>
              <th>{ln_hdr}</th>
              <th>Past 7 days</th>
              <th>Past 30 days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
      <style>
        .pl-consensus-records,.cfl-consensus{{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:14px;padding:18px;margin:16px 0 20px;max-width:1100px;margin-left:auto;margin-right:auto}}
        .pl-consensus-records h2,.cfl-consensus h2{{margin:0 0 6px;font-size:1.15rem;color:#0f172a;text-align:center}}
        .pl-consensus-records .sub{{margin:0 0 14px;color:#64748b;font-size:.88rem;text-align:center;max-width:46rem;margin-left:auto;margin-right:auto}}
        .pl-consensus-records table{{width:100%;border-collapse:collapse;font-size:.9rem}}
        .pl-consensus-records th,.pl-consensus-records td{{padding:10px 8px;border-bottom:1px solid #e2e8f0;text-align:center}}
        .pl-consensus-records th{{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#64748b}}
        .pl-consensus-records td.bucket{{text-align:left;font-weight:700;color:#0f172a}}
      </style>
    </div>
    """


def _extract_finals_without_date_sections(html: str, *, limit: int = 400) -> list[dict[str, Any]]:
    """Results shells that omit id=date-YYYY-MM-DD (CFL/UFC isolation)."""
    rows: list[dict[str, Any]] = []
    parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', html or "", flags=re.I)
    idx = 1
    while idx < len(parts):
        open_tag = parts[idx]
        body = parts[idx + 1] if idx + 1 < len(parts) else ""
        idx += 2
        card_html = open_tag + body[:25000]
        dm = re.search(
            r'FINAL\s*[·•]\s*(\d{4}-\d{2}-\d{2})',
            card_html,
            flags=re.I,
        )
        date_key = dm.group(1) if dm else ""
        if not date_key:
            continue
        lg_m = re.search(r'data-league="([^"]+)"', open_tag, re.I)
        league = (lg_m.group(1) if lg_m else "").strip()
        row = _extract_one_game_card(card_html, date_key, league)
        if not row:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _insert_consensus_below_season(html: str, block: str) -> str:
    """CFL: consensus under Season Performance, before the slate."""
    if not html or not block:
        return html
    i = html.find("Season Performance")
    if i >= 0:
        rest = html[i:]
        close = re.search(
            r"</(?:section|div)>\s*(?=<h[23]\b|<(?:div|section)[^>]*(?:games-grid|finals|date-nav|date-slider)|<!-- ── Date)",
            rest,
            flags=re.I,
        )
        if close:
            at = i + close.end()
            return html[:at] + block + html[at:]
    m = re.search(
        r'(<section class="pl-mlb-analytics"[\s\S]*?</section>)',
        html,
        flags=re.I,
    )
    if m:
        return html[: m.end(1)] + block + html[m.end(1) :]
    m = re.search(r"<!-- ── Date Slider", html)
    if m:
        return html[: m.start()] + block + html[m.start() :]
    m = re.search(r'(<div class="games-grid\b[^"]*"[^>]*>)', html, flags=re.I)
    if m:
        return html[: m.start(1)] + block + html[m.start(1) :]
    return html + block


def inject_consensus_records_html(
    html: str,
    sport: str = "",
    finals: list[dict[str, Any]] | None = None,
    last_night_key: str | None = None,
    **_kw: Any,
) -> str:
    """Add 6-model agreement table on results when locked pre-game sides exist.

    Skip if the page already has the CFL/shared block, or the sport's cards
    don't expose 6 named model picks (no fake 6/6 dashes).
    CFL may pass isolation `finals` — those cards do not carry 6 model names.
    """
    if not html:
        return html
    if "Consensus Based Betting Records" in html or "pl-consensus-records" in html:
        return html
    sport_l = (sport or "").lower()
    if sport_l in ("golf",):
        return html
    rows = list(finals or [])
    if not rows:
        rows = _extract_game_rows(html, limit=400)
        if not rows:
            rows = _extract_finals_without_date_sections(html)
    block = build_consensus_records_html(
        rows, last_night_key=last_night_key, sport=sport_l
    )
    if not block:
        return html
    if sport_l == "cfl":
        return _insert_consensus_below_season(html, block)
    m = re.search(
        r'(<h3[^>]*>\s*Recent Finals\s*</h3>)',
        html,
        flags=re.I,
    )
    if m:
        return html[: m.start(1)] + block + html[m.start(1) :]
    m = re.search(
        r'(<section class="pl-mlb-analytics"[\s\S]*?</section>)',
        html,
        flags=re.I,
    )
    if m:
        return html[: m.end(1)] + block + html[m.end(1) :]
    m = re.search(r'(<div class="games-grid\b[^"]*"[^>]*>)', html, flags=re.I)
    if m:
        return html[: m.start(1)] + block + html[m.start(1) :]
    return html


def _balanced_div_at(html: str, start: int) -> str:
    if start < 0 or start >= len(html) or not html.startswith("<div", start):
        return ""
    i = html.find(">", start)
    if i < 0:
        return ""
    i += 1
    depth = 1
    low = html.lower()
    while i < len(html) and depth:
        nxt_o = low.find("<div", i)
        nxt_c = low.find("</div>", i)
        if nxt_c < 0:
            return html[start:]
        if nxt_o >= 0 and nxt_o < nxt_c:
            depth += 1
            i = nxt_o + 4
        else:
            depth -= 1
            i = nxt_c + 6
    return html[start:i]


def _cfl_market_items(finals: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    key = "spread" if market == "spread" else "totals"
    out: list[dict[str, Any]] = []
    for g in finals or []:
        blk = g.get(key)
        if not isinstance(blk, dict):
            continue
        dk = str(g.get("game_date") or "")[:10]
        grade = blk.get("grade")
        if grade not in ("WIN", "LOSS", "PUSH"):
            if blk.get("push"):
                grade = "PUSH"
            elif blk.get("correct") is True:
                grade = "WIN"
            elif blk.get("correct") is False:
                grade = "LOSS"
            else:
                continue
        out.append({"model": "Prediction Lab", "grade": grade, "game_date": dk})
    return out


def _cfl_market_records_html(
    finals: list[dict[str, Any]],
    market: str,
    *,
    last_night_key: str | None = None,
) -> str:
    market = "spread" if market == "spread" else "totals"
    items = _cfl_market_items(finals, market)
    if not items:
        title = "Spread" if market == "spread" else "Totals"
        return (
            '<div class="pl-consensus-records">'
            f"<h2>{title} records</h2>"
            '<p class="sub">No graded games for this market on the current results slate.</p>'
            "</div>"
        )
    now = datetime.now(ZoneInfo("America/New_York"))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    past_dates = sorted(
        {
            str(g.get("game_date") or "")[:10]
            for g in finals or []
            if str(g.get("game_date") or "")[:10] < today
        }
    )
    ln_key = last_night_key or (past_dates[-1] if past_dates else yesterday)
    cut7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    def period(pred) -> list[dict[str, Any]]:
        return [a for a in items if pred(str(a.get("game_date") or "")[:10])]

    ln = period(lambda d: d == ln_key)
    d7 = period(lambda d: d >= cut7)
    season = items
    market_label = "Spread" if market == "spread" else "Totals"
    sub = (
        "Published CFL spread pick. Same Last night / Last 7 / Season windows as moneyline."
        if market == "spread"
        else "Published CFL over/under pick. Same Last night / Last 7 / Season windows as moneyline."
    )
    ln_hdr = f"Last night ({ln_key})" if ln_key else "Last night"
    return f"""
    <div class="pl-consensus-records" id="pl-{market}-records">
      <h2>Consensus Based Betting Records — {market_label}</h2>
      <p class="sub">{sub}</p>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Model</th>
              <th>{ln_hdr}</th>
              <th>Last 7 days</th>
              <th>Season</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="bucket">Prediction Lab</td>
              <td>{_consensus_record_cell(ln)}</td>
              <td>{_consensus_record_cell(d7)}</td>
              <td>{_consensus_record_cell(season)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    """


def _cfl_best_cards_html(tallies: dict[str, Any], *, face_only: bool = False) -> str:
    def card(label: str, block: dict[str, Any] | None) -> str:
        block = block or {}
        models = dict(block.get("models") or {})
        if face_only:
            pl = models.get("Prediction Lab")
            models = {"Prediction Lab": pl} if pl else {}
        best = _best_model(models)
        if not best:
            n = int(block.get("n") or 0)
            pct = block.get("pct")
            rec = block.get("record")
            if n > 0 and pct is not None:
                best = {
                    "name": "Prediction Lab",
                    "pct": pct,
                    "record": rec,
                    "units": block.get("units"),
                }
        if not best:
            return (
                f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
                f'<div class="pl-analytics-v muted">—</div></div>'
            )
        units = best.get("units")
        units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ""
        return (
            f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
            f'<div class="pl-analytics-name">{best.get("name") or "—"}</div>'
            f'<div class="pl-analytics-v">{best.get("pct")}%</div>'
            f'<div class="pl-analytics-sub">{best.get("record") or ""}'
            f'{" · " + units_s if units_s else ""}</div></div>'
        )

    return (
        '<h3 class="pl-analytics-title">Best Performing Model</h3>'
        '<div class="pl-analytics-grid">'
        f'{card("Last Night", (tallies or {}).get("last_night"))}'
        f'{card("Last 7", (tallies or {}).get("last_7"))}'
        f'{card("Season", (tallies or {}).get("season"))}'
        "</div>"
    )


def _clip_after_html(html: str) -> str:
    """Never leave product blocks after </html> — browsers paint that under the footer."""
    if not html:
        return html
    i = html.lower().find("</html>")
    if i < 0:
        return html
    return html[: i + len("</html>")]


def _balanced_tag_at(html: str, start: int, tag: str) -> str:
    tag = (tag or "").lower()
    if not tag or start < 0 or start >= len(html):
        return ""
    open_l = f"<{tag}"
    close_l = f"</{tag}>"
    low = html.lower()
    if not low.startswith(open_l, start):
        return ""
    nxt = low[start + len(open_l) : start + len(open_l) + 1]
    if nxt not in (">", " ", "\n", "\t", "/", "\r"):
        return ""
    i = html.find(">", start)
    if i < 0:
        return ""
    i += 1
    depth = 1

    def _is_open(pos: int) -> bool:
        if pos < 0:
            return False
        ch = low[pos + len(open_l) : pos + len(open_l) + 1]
        return ch in (">", " ", "\n", "\t", "/", "\r")

    while i < len(html) and depth:
        nxt_o = low.find(open_l, i)
        nxt_c = low.find(close_l, i)
        if nxt_c < 0:
            return ""
        while nxt_o >= 0 and nxt_o < nxt_c and not _is_open(nxt_o):
            nxt_o = low.find(open_l, nxt_o + 1)
        if nxt_o >= 0 and nxt_o < nxt_c:
            depth += 1
            i = nxt_o + len(open_l)
        else:
            depth -= 1
            i = nxt_c + len(close_l)
    return html[start:i]


def _strip_cfl_leftover_analytics(html: str) -> str:
    """Drop leftover MLB Best Performing / Efficiency sections from CFL cards."""
    guard = 0
    while guard < 6:
        m = re.search(r"<section\b[^>]*\bpl-mlb-analytics\b", html, flags=re.I)
        if not m:
            break
        start = html.rfind("<section", 0, m.start() + 9)
        if start < 0:
            start = m.start()
        block = _balanced_tag_at(html, start, "section")
        if not block:
            break
        html = html[:start] + html[start + len(block) :]
        guard += 1
    return html


def _insert_after_moneyline_accuracy(html: str, block: str) -> str:
    """Put consensus tabs after the 6-model grid, still inside <body>."""
    if not html or not block:
        return html
    i = html.find("Moneyline Accuracy by Model")
    if i >= 0:
        gm = re.search(r'<div class="(?:daily-tally-grid|model-grid)"', html[i:])
        if gm:
            grid_at = i + gm.start()
            grid = _balanced_div_at(html, grid_at)
            if grid:
                at = grid_at + len(grid)
                return html[:at] + block + html[at:]
    first = re.search(r"<div(?=[^>]*\bdate-section\b)", html, flags=re.I)
    if first:
        return html[: first.start()] + block + html[first.start() :]
    foot = re.search(r"<footer\b", html, flags=re.I)
    if foot:
        return html[: foot.start()] + block + html[foot.start() :]
    body = re.search(r"</body>", html, flags=re.I)
    if body:
        return html[: body.start()] + block + html[body.start() :]
    return html


def apply_cfl_cards_market_tabs(
    html: str,
    payload: dict[str, Any] | None,
    *,
    market: str = "moneyline",
) -> str:
    """Moneyline | Spread | Totals on CFL Cards — Consensus + Best Performing."""
    if not html:
        return html
    html = _clip_after_html(html)
    html = _strip_cfl_leftover_analytics(html)
    payload = payload or {}
    finals = list(payload.get("finals") or [])
    markets = payload.get("markets") or {}
    ln_key = ((payload.get("tallies") or {}).get("last_night") or {}).get("date")
    active = (market or "moneyline").strip().lower()
    if active not in ("moneyline", "spread", "totals"):
        active = "moneyline"

    existing = re.search(r'<div class="pl-results-markets\b', html)
    if existing:
        old = _balanced_div_at(html, existing.start())
        if old:
            html = html[: existing.start()] + html[existing.start() + len(old) :]
        html = _clip_after_html(html)

    cons_m = re.search(r'<div class="pl-consensus-records"', html)
    if not cons_m:
        return html
    ml_cons = _balanced_div_at(html, cons_m.start())
    if not ml_cons or "</html>" in ml_cons.lower():
        return html

    ml_best = _cfl_best_cards_html(
        ((markets.get("moneyline") or {}).get("tallies") or payload.get("tallies") or {})
    )
    sp_best = _cfl_best_cards_html(
        (markets.get("spread") or {}).get("tallies") or {}, face_only=True
    )
    ou_best = _cfl_best_cards_html(
        (markets.get("totals") or {}).get("tallies") or {}, face_only=True
    )
    sp_rec = _cfl_market_records_html(finals, "spread", last_night_key=ln_key)
    ou_rec = _cfl_market_records_html(finals, "totals", last_night_key=ln_key)

    def panel(key: str, body: str) -> str:
        hid = "" if key == active else " hidden"
        return f'<div data-market-panel="{key}"{hid}>{body}</div>'

    tabs = []
    for key, label in (
        ("moneyline", "Moneyline"),
        ("spread", "Spread"),
        ("totals", "Totals"),
    ):
        cls = "market-tab active" if key == active else "market-tab"
        tabs.append(
            f'<button type="button" class="{cls}" data-cfl-market="{key}">{label}</button>'
        )
    wrap = f"""
    <div class="pl-results-markets" id="pl-results-markets">
      <nav class="picks-market-tabs pl-results-market-tabs" aria-label="Results market">
        {"".join(tabs)}
      </nav>
      {panel("moneyline", ml_cons + ml_best)}
      {panel("spread", sp_rec + sp_best)}
      {panel("totals", ou_rec + ou_best)}
      <style>
        .pl-results-markets{{max-width:1100px;margin:16px auto 20px}}
        .pl-results-markets .pl-consensus-records{{margin:0 auto 16px}}
        .pl-results-market-tabs{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin:0 0 12px;width:100%}}
        .pl-results-market-tabs .market-tab{{border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.8rem;padding:7px 14px;border-radius:999px;cursor:pointer}}
        .pl-results-market-tabs .market-tab.active{{background:#0c1e3a;color:#fff;border-color:#0c1e3a}}
        .pl-results-markets [data-market-panel][hidden]{{display:none!important}}
      </style>
      <script>
      (function(){{
        var root=document.getElementById("pl-results-markets");
        if(!root) return;
        root.querySelectorAll("[data-cfl-market]").forEach(function(btn){{
          btn.addEventListener("click", function(){{
            var key=btn.getAttribute("data-cfl-market");
            root.querySelectorAll("[data-cfl-market]").forEach(function(b){{
              b.classList.toggle("active", b===btn);
            }});
            root.querySelectorAll("[data-market-panel]").forEach(function(p){{
              p.hidden = p.getAttribute("data-market-panel") !== key;
            }});
          }});
        }});
      }})();
      </script>
    </div>
    """
    html = html[: cons_m.start()] + html[cons_m.start() + len(ml_cons) :]
    return _clip_after_html(_insert_after_moneyline_accuracy(html, wrap))


def _place_cfl_markets_after_model_accuracy(html: str) -> str:
    """Season Performance → Moneyline Accuracy → consensus tabs. Never after </html>."""
    html = _clip_after_html(html)
    m = re.search(r'<div class="pl-results-markets\b', html)
    if not m:
        return html
    block = _balanced_div_at(html, m.start())
    if not block or "</html>" in block.lower():
        return html
    html = html[: m.start()] + html[m.start() + len(block) :]
    return _clip_after_html(_insert_after_moneyline_accuracy(html, block))


def _clean_team_label(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    s = re.sub(r"[\u2705\u274c✅❌★*]+", "", s).strip()
    return s[:60]


def _parse_score(raw: str | None) -> int | None:
    if raw is None:
        return None
    m = re.search(r"-?\d+", str(raw))
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _extract_card_models(card_html: str) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for m in re.finditer(
        r'class="pc-box[^"]*"[^>]*>\s*'
        r'<div class="pc-name">([^<]+)</div>\s*'
        r'(?:<div class="pc-val">([^<]*)</div>\s*)?'
        r'<div class="pc-side[^"]*"[^>]*>([^<]*)</div>',
        card_html,
        re.I | re.S,
    ):
        name = _strip_emoji(m.group(1))
        name = _EMOJI_MODEL.get(name, name)
        pick = _clean_team_label(m.group(3))
        pct_raw = (m.group(2) or "").strip().replace("%", "")
        try:
            prob = float(pct_raw) if pct_raw else None
        except ValueError:
            prob = None
        box = m.group(0)
        correct = None
        if "pc-box correct" in box or "✅" in box:
            correct = True
        elif "pc-box wrong" in box or "❌" in box:
            correct = False
        if name:
            models[name] = {"pick": pick, "prob": prob, "correct": correct}
    return models


def _extract_spread_totals(card_html: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    spread = None
    totals = None
    sm = re.search(
        r'Spread pick</span>\s*<span class="sf-val">([^<]+?)(?:\s*<span class="pick-(ok|no)">)',
        card_html,
        re.I | re.S,
    )
    if sm:
        pick = _clean_team_label(sm.group(1))
        mark = (sm.group(2) or "").lower()
        spread = {
            "pick": pick,
            "line": pick,
            "correct": True if mark == "ok" else False if mark == "no" else None,
            "push": False,
        }
    tm = re.search(
        r'Total pick</span>\s*<span class="sf-val">([^<]+?)(?:\s*<span class="pick-(ok|no)">)',
        card_html,
        re.I | re.S,
    )
    if tm:
        pick = _clean_team_label(tm.group(1))
        mark = (tm.group(2) or "").lower()
        totals = {
            "pick": pick,
            "line": pick,
            "correct": True if mark == "ok" else False if mark == "no" else None,
            "push": False,
        }
    return spread, totals


def _extract_one_game_card(card_html: str, date_key: str, league: str) -> dict[str, Any] | None:
    away_m = re.search(
        r'class="team-col away"[^>]*>.*?class="team-name">([^<]+)</div>'
        r'(?:.*?class="final-score[^"]*">\s*([^<]*?)\s*</div>)?',
        card_html,
        re.I | re.S,
    )
    home_m = re.search(
        r'class="team-col home"[^>]*>.*?class="team-name">([^<]+)</div>'
        r'(?:.*?class="final-score[^"]*">\s*([^<]*?)\s*</div>)?',
        card_html,
        re.I | re.S,
    )
    if not away_m or not home_m:
        return None
    away = _clean_team_label(away_m.group(1))
    home = _clean_team_label(home_m.group(1))
    if len(away) < 2 or len(home) < 2:
        return None
    models = _extract_card_models(card_html)
    face = None
    face_prob = None
    face_correct = None
    for preferred in ("Edge", "Sharp Consensus", "Grinder2"):
        if preferred in models:
            face = models[preferred].get("pick")
            face_prob = models[preferred].get("prob")
            face_correct = models[preferred].get("correct")
            break
    if face is None and models:
        first = next(iter(models.values()))
        face = first.get("pick")
        face_prob = first.get("prob")
        face_correct = first.get("correct")
    spread, totals = _extract_spread_totals(card_html)
    return {
        "game_date": str(date_key)[:10],
        "league": league or "MLB",
        "away_team_id": away,
        "home_team_id": home,
        "away_score": _parse_score(away_m.group(2)),
        "home_score": _parse_score(home_m.group(2)),
        "final": True,
        "face_pick": face,
        "face_prob": face_prob,
        "correct": face_correct,
        "models": models,
        "spread": spread,
        "totals": totals,
    }


def _extract_game_rows(html: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """Finals rows from live game-card HTML (teams-split / pick-conf / spread-total footer)."""
    rows: list[dict[str, Any]] = []
    date_chunks = re.split(r'<div id="date-([^"]+)"[^>]*>', html or "")
    it = iter(date_chunks[1:])
    for date_key in it:
        content = next(it, "")
        # Split on game-card opens; keep a generous slice per card.
        parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', content, flags=re.I)
        idx = 1
        while idx < len(parts):
            open_tag = parts[idx]
            body = parts[idx + 1] if idx + 1 < len(parts) else ""
            idx += 2
            # Card body until next game-card is already split; truncate runaway CSS.
            card_html = open_tag + body[:25000]
            lg_m = re.search(r'data-league="([^"]+)"', open_tag, re.I)
            league = (lg_m.group(1) if lg_m else "").strip()
            row = _extract_one_game_card(card_html, str(date_key)[:10], league)
            if not row:
                continue
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def _snapshot_dual_ats_block(
    *,
    pl_covered: int,
    pl_graded: int,
    pl_pushes: int,
    pl_pct: Any,
    xs_covered: int,
    xs_graded: int,
    xs_pushes: int,
    xs_pct: Any,
    win: dict[str, Any],
) -> dict[str, Any]:
    """Build Season tally with Prediction Lab + XSharp model cards when both exist."""

    def _block(covered: int, graded: int, pushes: int, pct: Any) -> dict[str, Any] | None:
        decided = max(0, int(graded) - max(0, int(pushes)))
        if decided <= 0:
            return None
        w = min(max(0, int(covered)), decided)
        l = max(0, decided - w)
        if pct is None:
            pct = round(100.0 * w / decided, 1)
        return {
            "w": w,
            "l": l,
            "n": decided,
            "pushes": int(pushes) if pushes else 0,
            "pct": pct,
            "record": f"{w}-{l}" + (f"-{pushes}" if pushes else ""),
            "units": _units_from_wl(w, l),
        }

    pl = _block(pl_covered, pl_graded, pl_pushes, pl_pct)
    xs = _block(xs_covered, xs_graded, xs_pushes, xs_pct)
    models: dict[str, Any] = {}
    order: list[str] = []
    if pl:
        models["Prediction Lab"] = pl
        order.append("Prediction Lab")
    if xs:
        models["XSharp"] = xs
        order.append("XSharp")
    if not models:
        return {}
    face_name = order[0]
    face = models[face_name]
    return {
        "label": "Season",
        "games": face["n"],
        "w": face["w"],
        "l": face["l"],
        "n": face["n"],
        "pct": face["pct"],
        "record": face["record"],
        "units": face.get("units"),
        "models": models,
        "model_order": order,
        "ready": True,
        "date_from": win.get("start"),
        "date_to": win.get("end"),
        "locked": True,
    }


def _snapshot_run_line_models(st: dict[str, Any], sp: dict[str, Any], win: dict[str, Any]) -> dict[str, Any]:
    """MLB season ATS for PL run line + XSharp run line (when snapshot has both)."""
    return _snapshot_dual_ats_block(
        pl_covered=int(st.get("pl_spread_covered") or 0),
        pl_graded=int(st.get("pl_spread_graded") or 0),
        pl_pushes=int(st.get("pl_spread_pushes") or 0),
        pl_pct=st.get("pl_spread_pct"),
        xs_covered=int(st.get("spread_covered") or sp.get("spread_covered") or 0),
        xs_graded=int(st.get("spread_graded") or sp.get("spread_graded") or 0),
        xs_pushes=int(st.get("spread_pushes") or sp.get("spread_pushes") or 0),
        xs_pct=st.get("spread_pct") if st.get("spread_pct") is not None else sp.get("spread_pct"),
        win=win,
    )


def _snapshot_totals_models(st: dict[str, Any], sp: dict[str, Any], win: dict[str, Any]) -> dict[str, Any]:
    """MLB season O/U for Prediction Lab + XSharp (when snapshot has both)."""
    return _snapshot_dual_ats_block(
        pl_covered=int(st.get("pl_total_correct") or sp.get("ou_correct") or 0),
        pl_graded=int(st.get("pl_total_graded") or sp.get("ou_graded") or 0),
        pl_pushes=int(st.get("pl_total_pushes") or st.get("total_pushes") or 0),
        pl_pct=st.get("pl_total_pct") if st.get("pl_total_pct") is not None else sp.get("ou_pct"),
        xs_covered=int(st.get("total_correct") or 0),
        xs_graded=int(st.get("total_graded") or 0),
        xs_pushes=int(st.get("total_pushes") or 0),
        xs_pct=st.get("total_pct"),
        win=win,
    )


def _merge_face_roi_into_sou(
    season_blk: dict[str, Any],
    face_row: dict[str, Any],
    *,
    sport: str,
) -> dict[str, Any]:
    """Merge Season Performance face into dual PL/XSharp without dropping the other model."""
    label = face_row.get("model_label") or "Prediction Lab"
    face = _face_tally(
        face_row["games"],
        face_row["w"],
        face_row["l"],
        face_row.get("pushes") or 0,
        label="Season",
        model_label=label,
        pct=face_row.get("pct"),
        ready=True,
    )
    if (sport or "").lower() != "mlb" or not isinstance(season_blk, dict):
        return face
    models = dict(season_blk.get("models") or {})
    if len(models) < 2:
        return face
    models.update(face.get("models") or {})
    order = [n for n in ("Prediction Lab", "XSharp") if n in models]
    for n in models:
        if n not in order:
            order.append(n)
    out = dict(season_blk)
    out["models"] = models
    out["model_order"] = order
    out["ready"] = True
    first = models.get(order[0]) or {}
    for k in ("w", "l", "n", "pct", "record", "units", "games"):
        if first.get(k) is not None:
            out[k] = first.get(k) if k != "games" else first.get("n")
    return out


def _snapshot_season(sport: str) -> dict[str, Any]:
    """Season Spread/Totals (+ ML face) from locked snapshot when present."""
    # Prefer newest matching snapshot
    candidates = sorted(SNAPSHOT_DIR.glob(f"{sport.upper()}_*_regular.json"), reverse=True)
    if not candidates:
        return {}
    try:
        blob = json.loads(candidates[0].read_text())
    except Exception:
        return {}
    sp = blob.get("season_perf") or {}
    st = blob.get("spread_total_stats") or {}
    win = blob.get("window") or {}

    def _sou(covered_key: str, graded_key: str, pct_key: str, push_key: str, label_key: str):
        w = int(sp.get(covered_key) or st.get(covered_key.replace("spread_covered", "pl_spread_covered").replace("ou_correct", "pl_total_correct")) or 0)
        # Prefer PL face when available for totals/spread display
        if "ou" in covered_key or "total" in covered_key:
            w = int(sp.get("ou_correct") or st.get("pl_total_correct") or st.get("total_correct") or 0)
            n = int(sp.get("ou_graded") or st.get("pl_total_graded") or st.get("total_graded") or 0)
            pushes = int(st.get("pl_total_pushes") or st.get("total_pushes") or 0)
            label = sp.get("ou_model_label") or "Prediction Lab"
            pct = sp.get("ou_pct") or st.get("pl_total_pct")
        else:
            w = int(sp.get("spread_covered") or st.get("pl_spread_covered") or st.get("spread_covered") or 0)
            n = int(sp.get("spread_graded") or st.get("pl_spread_graded") or st.get("spread_graded") or 0)
            pushes = int(st.get("pl_spread_pushes") or st.get("spread_pushes") or 0)
            label = sp.get("spread_model_label") or "Prediction Lab"
            pct = sp.get("spread_pct") or st.get("pl_spread_pct")
        decided = max(0, n - max(0, pushes))
        w = min(max(0, w), decided) if decided else 0
        l = max(0, decided - w)
        if pct is None and decided:
            pct = round(100.0 * w / decided, 1)
        return _face_tally(
            decided,
            w,
            l,
            pushes,
            label="Season",
            model_label=label,
            ready=decided > 0,
            date_from=win.get("start"),
            date_to=win.get("end"),
            locked=True,
        )

    ml_w = int(sp.get("ml_correct") or 0)
    ml_n = int(sp.get("ml_total") or 0)
    ml_l = max(0, ml_n - ml_w)
    ml_pct = sp.get("ml_accuracy")
    ml_label = sp.get("ml_model_label") or "Prediction Lab"
    ml_season = {
        "label": "Season",
        "games": ml_n,
        "models": {
            ml_label: {
                "w": ml_w,
                "l": ml_l,
                "n": ml_n,
                "pct": ml_pct,
                "record": f"{ml_w}-{ml_l}",
            }
        },
        "ready": ml_n > 0,
        "date_from": win.get("start"),
        "date_to": win.get("end"),
    }
    # Fill remaining model slots as empty so UI grid stays stable
    for name in MODEL_ORDER:
        ml_season["models"].setdefault(
            name, {"w": 0, "l": 0, "n": 0, "pct": None, "record": "0-0"}
        )

    # MLB: show both PL + XSharp ATS when snapshot has both counters.
    spread_season = _sou(
        "spread_covered", "spread_graded", "spread_pct", "spread_pushes", "spread_model_label"
    )
    totals_season = _sou("ou_correct", "ou_graded", "ou_pct", "total_pushes", "ou_model_label")
    if (sport or "").lower() == "mlb":
        dual_sp = _snapshot_run_line_models(st, sp, win)
        if dual_sp:
            spread_season = dual_sp
        dual_ou = _snapshot_totals_models(st, sp, win)
        if dual_ou:
            totals_season = dual_ou

    return {
        "moneyline": ml_season,
        "spread": spread_season,
        "totals": totals_season,
    }


def markets_from_live_html(html: str, sport: str) -> dict[str, Any]:
    """Convert live results HTML tallies into soccer-style markets payload."""
    sections = _extract_tally_sections(html)
    season_roi = _extract_season_roi(html)
    snap = _snapshot_season(sport)
    # Pull enough graded cards to backfill blank season model rows (e.g. Efficiency).
    finals = synthesize_missing_ml_models(_extract_game_rows(html, limit=400))

    by_kind = {s["kind"]: s for s in sections if s["kind"] in ("last_night", "last_7")}

    def window_from_section(sec: dict | None, label: str) -> tuple[dict, dict, dict]:
        if not sec:
            empty_ml = _empty_ml_tally(0, label=label)
            empty_sp = _face_tally(0, 0, 0, label=label)
            empty_ou = _face_tally(0, 0, 0, label=label)
            return empty_ml, empty_sp, empty_ou
        ml = _empty_ml_tally(sec.get("games") or 0, label=label)
        if sec.get("date"):
            ml["date"] = sec["date"]
        if sec.get("date_from"):
            ml["date_from"] = sec["date_from"]
            ml["date_to"] = sec.get("date_to")
        sp = _face_tally(0, 0, 0, label=label)
        ou = _face_tally(0, 0, 0, label=label)
        if sec.get("date"):
            sp["date"] = sec["date"]
            ou["date"] = sec["date"]
        if sec.get("date_from"):
            sp["date_from"] = sec["date_from"]
            sp["date_to"] = sec.get("date_to")
            ou["date_from"] = sec["date_from"]
            ou["date_to"] = sec.get("date_to")
        for card in sec.get("cards") or []:
            name = card["name"]
            block = _model_block(name, card.get("pct"), card.get("record") or "")
            if name in MODEL_ORDER:
                ml["models"][name] = block
            elif name in ("Spread",):
                sp = _face_tally(
                    block["n"] + block.get("pushes", 0),
                    block["w"],
                    block["l"],
                    block.get("pushes", 0),
                    label=label,
                    model_label="Prediction Lab",
                    date=ml.get("date"),
                    date_from=ml.get("date_from"),
                    date_to=ml.get("date_to"),
                )
            elif name in ("Over/Under", "O/U"):
                ou = _face_tally(
                    block["n"] + block.get("pushes", 0),
                    block["w"],
                    block["l"],
                    block.get("pushes", 0),
                    label=label,
                    model_label="Prediction Lab",
                    date=ml.get("date"),
                    date_from=ml.get("date_from"),
                    date_to=ml.get("date_to"),
                )
        return ml, sp, ou

    ln_ml, ln_sp, ln_ou = window_from_section(by_kind.get("last_night"), "Last Night")
    l7_ml, l7_sp, l7_ou = window_from_section(by_kind.get("last_7"), "Last 7")

    # Season: prefer full "Moneyline Accuracy by Model" grid when present (WNBA),
    # else face ROI slot (MLB publishes face only), else snapshot.
    season_ml = snap.get("moneyline") or _empty_ml_tally(0, label="Season")
    season_sp = snap.get("spread") or _face_tally(0, 0, 0, label="Season")
    season_ou = snap.get("totals") or _face_tally(0, 0, 0, label="Season")
    by_model = _extract_season_ml_by_model(html)
    if by_model:
        season_ml = by_model
    elif season_roi.get("moneyline"):
        r = season_roi["moneyline"]
        label = r.get("model_label") or "Grinder2"
        season_ml = _empty_ml_tally(r["games"], label="Season", ready=True)
        season_ml["models"][label] = _model_block(
            label, r.get("pct"), f"{r['w']}-{r['l']}"
        )
        season_ml["model_order"] = [label]
    # Fill blank named models (Efficiency / Grinder2 / Takedown —) from graded game cards.
    season_ml = _backfill_blank_ml_models(season_ml, finals)
    # Also backfill last-night / last-7 blanks from windowed finals
    ln_ml = _backfill_blank_ml_models(ln_ml, finals[:40])
    l7_ml = _backfill_blank_ml_models(l7_ml, finals[:80])
    # Face Season Performance merges into dual PL/XSharp (does not drop the other model).
    if season_roi.get("spread"):
        season_sp = _merge_face_roi_into_sou(season_sp, season_roi["spread"], sport=sport)
    if season_roi.get("totals"):
        season_ou = _merge_face_roi_into_sou(season_ou, season_roi["totals"], sport=sport)

    # Attach units on face season/last7 blocks
    for blk in (season_sp, season_ou, l7_sp, l7_ou, ln_sp, ln_ou):
        if isinstance(blk, dict) and blk.get("units") is None:
            blk["units"] = _units_from_wl(int(blk.get("w") or 0), int(blk.get("l") or 0))

    analytics = build_results_analytics(
        sport=sport,
        tallies_ml={"last_night": ln_ml, "last_7": l7_ml, "season": season_ml},
        season_sp=season_sp,
        season_ou=season_ou,
        last7_sp=l7_sp,
        last7_ou=l7_ou,
        finals=finals,
        snap=snap,
    )

    return {
        "ok": True,
        "today": _today_et(),
        "model_order": MODEL_ORDER,
        "finals": finals,
        "analytics": analytics,
        "tallies": {
            "last_night": ln_ml,
            "last_7": l7_ml,
            "season": season_ml,
        },
        "markets": {
            "moneyline": {
                "label": "Moneyline",
                "tallies": {
                    "last_night": ln_ml,
                    "last_7": l7_ml,
                    "season": season_ml,
                },
                "model_order": MODEL_ORDER,
                "finals": finals,
            },
            "spread": {
                "label": "Spread" if (sport or "").lower() != "mlb" else "Run Line",
                "tallies": {
                    "last_night": {**ln_sp, "label": "Last Night"},
                    "last_7": {**l7_sp, "label": "Last 7"},
                    "season": {**season_sp, "label": "Season"},
                },
                "model_order": (
                    list(season_sp.get("model_order") or [])
                    if isinstance(season_sp, dict)
                    and (season_sp.get("model_order") or [])
                    else (
                        [
                            n
                            for n in ("Prediction Lab", "XSharp")
                            if isinstance(season_sp, dict)
                            and ((season_sp.get("models") or {}).get(n) or {}).get("n")
                        ]
                        or ["Prediction Lab"]
                    )
                ),
                "finals": [f for f in finals if f.get("spread")][:80] or finals[:40],
            },
            "totals": {
                "label": "Totals",
                "tallies": {
                    "last_night": {**ln_ou, "label": "Last Night"},
                    "last_7": {**l7_ou, "label": "Last 7"},
                    "season": {**season_ou, "label": "Season"},
                },
                "model_order": (
                    list(season_ou.get("model_order") or [])
                    if isinstance(season_ou, dict)
                    and (season_ou.get("model_order") or [])
                    else (
                        [
                            n
                            for n in ("Prediction Lab", "XSharp")
                            if isinstance(season_ou, dict)
                            and ((season_ou.get("models") or {}).get(n) or {}).get("n")
                        ]
                        or ["Prediction Lab"]
                    )
                ),
                "finals": [f for f in finals if f.get("totals")][:80] or finals[:40],
            },
        },
    }


def build_cfl_payload() -> dict[str, Any]:
    """CFL tabbed markets from isolation pipeline (ML + model spread/total)."""
    import importlib.util
    import sys

    root = str(CFL_ISO.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    pipe_path = CFL_ISO / "engine" / "pipeline.py"
    spec = importlib.util.spec_from_file_location("cfl_tabbed_pipe", pipe_path)
    pipe = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(pipe)
    try:
        pipe.ensure_predictions(refresh=False)
    except Exception:
        # Hub sandbox may mount the CFL DB read-only — still serve graded rows.
        pass
    try:
        rows = pipe.list_graded_results(days=120, regular_season_only=True)
    except Exception as e:
        return {"ok": False, "error": f"CFL results unavailable: {e}"}

    fade_path = CFL_ISO / "engine" / "display_fade.py"
    fade_spec = importlib.util.spec_from_file_location("cfl_tabbed_fade", fade_path)
    fade = importlib.util.module_from_spec(fade_spec)
    assert fade_spec.loader is not None
    fade_spec.loader.exec_module(fade)
    fade_ml, fade_spread = fade.season_fade_flags(rows)
    other_side = fade.other_side
    spread_label = fade.spread_label
    grade_spread_raw = fade.grade_spread_raw

    def _cfl_clamp(x: float) -> float:
        return max(0.12, min(0.88, float(x)))

    def component_models_for(r: dict) -> dict[str, Any]:
        """Named-model sides from unfaded hp + offsets; invert pick when faded."""
        home = r.get("home_team") or ""
        away = r.get("away_team") or ""
        try:
            hp = float(r["home_win_prob"])
            ah = int(r["home_score"])
            aa = int(r["away_score"])
        except (TypeError, ValueError, KeyError):
            return {}
        winner = None if ah == aa else (home if ah > aa else away)
        out: dict[str, Any] = {}
        for name, d in CFL_MODEL_DELTAS:
            p = _cfl_clamp(hp + d)
            fav = home if p >= 0.5 else away
            fav_p = p if fav == home else 1.0 - p
            if fade_ml:
                fav = other_side(home, away, fav) or fav
            correct = None
            if winner is not None:
                correct = str(fav).strip().lower() == str(winner).strip().lower()
            out[name] = {
                "pick": fav,
                "prob": round(fav_p * 100.0, 1),
                "correct": correct,
            }
        return out

    today = datetime.now(ZoneInfo("America/New_York")).date()
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    by_day: dict[str, list] = {}
    for r in rows:
        dk = str(r.get("game_date") or "")[:10]
        if len(dk) >= 10:
            by_day.setdefault(dk, []).append(r)
    dates_sorted = sorted(by_day.keys())
    if yesterday in by_day:
        last_night = yesterday
    elif dates_sorted:
        last_night = dates_sorted[-1]
    else:
        last_night = yesterday
    try:
        anchor = datetime.strptime(last_night, "%Y-%m-%d").date()
    except Exception:
        anchor = today
        last_night = today.strftime("%Y-%m-%d")
    last7 = {(anchor - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 7)}

    def grade_ml(r: dict) -> bool | None:
        g = r.get("grade")
        if g == "WIN":
            ok = True
        elif g == "LOSS":
            ok = False
        else:
            return None
        return (not ok) if fade_ml else ok

    def grade_spread(r: dict) -> tuple[bool | None, bool]:
        """ATS vs raw model_spread; invert when season spread is faded."""
        ok, push = grade_spread_raw(r)
        if fade_spread and ok is not None:
            ok = not ok
        return ok, push

    def grade_total(r: dict) -> tuple[bool | None, bool]:
        tot = r.get("model_total")
        hs, as_ = r.get("home_score"), r.get("away_score")
        if tot is None or hs is None or as_ is None:
            return None, False
        try:
            line = float(tot)
            actual = float(hs) + float(as_)
        except (TypeError, ValueError):
            return None, False
        if abs(actual - line) < 1e-9:
            return None, True
        # Model lean over if we have no separate pick — use predicted vs line
        # Prefer predicted scores sum when present
        ph, pa = r.get("predicted_home_score"), r.get("predicted_away_score")
        lean_over = True
        if ph is not None and pa is not None:
            try:
                lean_over = (float(ph) + float(pa)) >= line
            except (TypeError, ValueError):
                lean_over = True
        went_over = actual > line
        return went_over == lean_over, False

    def ml_window(dates_set: set[str] | None) -> dict[str, Any]:
        w = l = games = 0
        tallies: dict[str, list[int]] = {n: [0, 0] for n, _ in CFL_MODEL_DELTAS}
        for r in rows:
            dk = str(r.get("game_date") or "")[:10]
            if dates_set is not None and dk not in dates_set:
                continue
            games += 1
            ok = grade_ml(r)
            if ok is True:
                w += 1
            elif ok is False:
                l += 1
            for name, m in component_models_for(r).items():
                if m.get("correct") is True:
                    tallies[name][0] += 1
                elif m.get("correct") is False:
                    tallies[name][1] += 1
        n = w + l
        pct = round(100.0 * w / n, 1) if n else None
        models: dict[str, Any] = {}
        for name, (mw, ml_) in tallies.items():
            mn = mw + ml_
            if mn <= 0:
                continue
            models[name] = {
                "w": mw,
                "l": ml_,
                "n": mn,
                "pct": round(100.0 * mw / mn, 1),
                "record": f"{mw}-{ml_}",
                "units": _units_from_wl(mw, ml_),
            }
        sc = models.get("Sharp Consensus")
        if sc:
            models["Prediction Lab"] = dict(sc)
        elif n:
            models["Prediction Lab"] = {
                "w": w,
                "l": l,
                "n": n,
                "pct": pct,
                "record": f"{w}-{l}",
                "units": _units_from_wl(w, l),
            }
        return {
            "games": games,
            "w": w,
            "l": l,
            "n": n,
            "pct": pct,
            "record": f"{w}-{l}",
            "units": _units_from_wl(w, l) if n else None,
            "models": models,
            "model_order": [name for name in MODEL_ORDER if name in models],
        }

    def sou_window(market: str, dates_set: set[str] | None) -> dict[str, Any]:
        w = l = p = games = 0
        for r in rows:
            dk = str(r.get("game_date") or "")[:10]
            if dates_set is not None and dk not in dates_set:
                continue
            if market == "spread":
                ok, push = grade_spread(r)
            else:
                ok, push = grade_total(r)
            if ok is None and not push:
                continue
            games += 1
            if push:
                p += 1
            elif ok:
                w += 1
            else:
                l += 1
        return _face_tally(games, w, l, p)

    ln_ml = {"label": "Last Night", "date": last_night, **ml_window({last_night})}
    l7_ml = {
        "label": "Last 7",
        "date_from": (anchor - timedelta(days=6)).strftime("%Y-%m-%d"),
        "date_to": last_night,
        **ml_window(last7),
    }
    season_ml = {"label": "Season", **ml_window(None)}
    # Publish Efficiency as an alias of face Prediction Lab so shared analytics
    # + Moneyline Efficiency cards populate (CFL has one ML engine, not six).
    for block in (ln_ml, l7_ml, season_ml):
        models = block.get("models") or {}
        pl = models.get("Prediction Lab")
        if pl and (pl.get("n") or 0) > 0 and "Efficiency" not in models:
            models = dict(models)
            models["Efficiency"] = dict(pl)
            block["models"] = models

    ln_sp = {"label": "Last Night", "date": last_night, **sou_window("spread", {last_night})}
    l7_sp = {
        "label": "Last 7",
        "date_from": (anchor - timedelta(days=6)).strftime("%Y-%m-%d"),
        "date_to": last_night,
        **sou_window("spread", last7),
    }
    season_sp = {"label": "Season", **sou_window("spread", None)}

    ln_ou = {"label": "Last Night", "date": last_night, **sou_window("totals", {last_night})}
    l7_ou = {
        "label": "Last 7",
        "date_from": (anchor - timedelta(days=6)).strftime("%Y-%m-%d"),
        "date_to": last_night,
        **sou_window("totals", last7),
    }
    season_ou = {"label": "Season", **sou_window("totals", None)}

    finals = []
    # Full regular season (CFL ~81 games). Do not cap at 80 — that hid Week 1
    # once the slate grew, and previously mixed in preseason extras.
    for r in rows:
        home = r.get("home_team") or ""
        away = r.get("away_team") or ""
        pick = r.get("pick_ml")
        if fade_ml:
            pick = other_side(home, away, pick)
        face_prob = None
        hp = r.get("home_win_prob")
        if hp is not None:
            try:
                hp_f = float(hp)
                if fade_ml:
                    hp_f = 1.0 - hp_f
                face_prob = round(max(hp_f, 1.0 - hp_f) * 100.0, 1)
            except (TypeError, ValueError):
                face_prob = None
        ml_ok = grade_ml(r)
        card = {
            "game_date": str(r.get("game_date") or "")[:10],
            "league": "CFL",
            "away_team_id": away,
            "home_team_id": home,
            "away_score": r.get("away_score"),
            "home_score": r.get("home_score"),
            "final": True,
            "face_pick": pick,
            "face_prob": face_prob,
            "correct": ml_ok,
            "models": {},
        }
        sides = component_models_for(r)
        if sides:
            card["models"] = sides
        if pick:
            card["models"]["Prediction Lab"] = {
                "pick": pick,
                "prob": face_prob,
                "correct": ml_ok,
            }
        sp_ok, sp_push = grade_spread(r)
        if sp_ok is not None or sp_push:
            sp = r.get("model_spread")
            try:
                sp_f = float(sp) if sp is not None else None
            except (TypeError, ValueError):
                sp_f = None
            if fade_spread and sp_f is not None:
                sp_f = -sp_f
            card["spread"] = {
                "pick": spread_label(home, away, sp_f),
                "correct": sp_ok,
                "push": sp_push,
                "grade": "PUSH" if sp_push else ("WIN" if sp_ok else "LOSS"),
            }
        ou_ok, ou_push = grade_total(r)
        if ou_ok is not None or ou_push:
            card["totals"] = {
                "pick": f"O/U {r.get('model_total')}",
                "correct": ou_ok,
                "push": ou_push,
                "grade": "PUSH" if ou_push else ("WIN" if ou_ok else "LOSS"),
            }
        finals.append(card)

    analytics = build_results_analytics(
        sport="cfl",
        tallies_ml={"last_night": ln_ml, "last_7": l7_ml, "season": season_ml},
        season_sp=season_sp,
        season_ou=season_ou,
        last7_sp=l7_sp,
        last7_ou=l7_ou,
        finals=finals,
        snap=None,
    )

    return {
        "ok": True,
        "today": _today_et(),
        "model_order": list(MODEL_ORDER),
        "finals": finals,
        "analytics": analytics,
        "tallies": {
            "last_night": ln_ml,
            "last_7": l7_ml,
            "season": season_ml,
        },
        "markets": {
            "moneyline": {
                "label": "Moneyline",
                "tallies": {
                    "last_night": ln_ml,
                    "last_7": l7_ml,
                    "season": season_ml,
                },
                "model_order": list(MODEL_ORDER),
                "finals": finals,
            },
            "spread": {
                "label": "Spread",
                "tallies": {
                    "last_night": ln_sp,
                    "last_7": l7_sp,
                    "season": season_sp,
                },
                "model_order": ["Prediction Lab"],
                "finals": [f for f in finals if f.get("spread")],
            },
            "totals": {
                "label": "Totals",
                "tallies": {
                    "last_night": ln_ou,
                    "last_7": l7_ou,
                    "season": season_ou,
                },
                "model_order": ["Prediction Lab"],
                "finals": [f for f in finals if f.get("totals")],
            },
        },
    }


def build_ufc_payload() -> dict[str, Any]:
    """UFC moneyline-only tabbed markets from isolation graded fights (MLB chart chrome)."""
    import importlib.util
    import sys

    root = str(UFC_ISO.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    pipe_path = UFC_ISO / "engine" / "pipeline.py"
    if not pipe_path.is_file():
        return {"ok": False, "error": f"UFC isolation missing: {pipe_path}"}
    spec = importlib.util.spec_from_file_location("ufc_tabbed_pipe", pipe_path)
    pipe = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(pipe)
    try:
        cards = pipe.list_graded_results(limit=500)
    except Exception as e:
        return {"ok": False, "error": f"UFC results unavailable: {e}"}

    et = ZoneInfo("America/New_York")

    def _day(c: dict[str, Any]):
        raw = str(c.get("fight_date") or "")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                from datetime import timezone as _tz

                dt = dt.replace(tzinfo=_tz.utc)
            return dt.astimezone(et).date()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

    def _clamp(x: float) -> float:
        return max(0.01, min(0.99, float(x)))

    finals: list[dict[str, Any]] = []
    dated: list[tuple[dict[str, Any], Any]] = []
    for c in cards or []:
        home = str(c.get("home_fighter") or "")
        away = str(c.get("away_fighter") or "")
        winner = str(c.get("winner") or "")
        pick = str(c.get("pick_ml") or "")
        grade = c.get("grade")
        if not home or not away or grade not in ("WIN", "LOSS"):
            continue
        try:
            hp = float(c.get("home_win_prob") if c.get("home_win_prob") is not None else 0.5)
        except (TypeError, ValueError):
            hp = 0.5
        models: dict[str, Any] = {}
        for name, d in UFC_MODEL_DELTAS:
            p = _clamp(hp + d)
            fav = home if p >= 0.5 else away
            fav_p = p if fav == home else 1.0 - p
            models[name] = {
                "pick": fav,
                "prob": round(fav_p * 100.0, 1),
                "correct": (fav == winner) if winner else None,
            }
        face_pick = pick or (home if hp >= 0.5 else away)
        face_prob = round(max(hp, 1.0 - hp) * 100.0, 1)
        day = _day(c)
        game_date = day.isoformat() if day else str(c.get("fight_date") or "")[:10]
        # W/L like UFC result cards (not fake 1–0 scores).
        row = {
            "game_date": game_date,
            "league": "UFC",
            "home": home,
            "away": away,
            "home_team": home,
            "away_team": away,
            "home_score": "W" if winner == home else "L",
            "away_score": "W" if winner == away else "L",
            "winner": winner,
            "face_pick": face_pick,
            "face_prob": face_prob,
            "correct": grade == "WIN",
            "models": models,
            "note": str(c.get("event_name") or "UFC"),
        }
        finals.append(row)
        if day is not None:
            dated.append((row, day))

    last_night = max((d for _, d in dated), default=None)

    def _window_rows(pred) -> list[dict[str, Any]]:
        return [r for r, d in dated if pred(d)]

    if last_night:
        ln_rows = _window_rows(lambda d: d == last_night)
        lo = last_night - timedelta(days=6)
        l7_rows = _window_rows(lambda d: lo <= d <= last_night)
    else:
        ln_rows, l7_rows = [], []
    season_rows = [r for r, _ in dated]

    def _ml_window(rows: list[dict[str, Any]], label: str, *, date: str | None = None) -> dict[str, Any]:
        models = _tally_models_from_finals(rows)
        games = len(rows)
        block = _empty_ml_tally(games, label=label, ready=True)
        if date:
            block["date"] = date
        if models:
            block["models"].update(models)
            block["model_order"] = [n for n in MODEL_ORDER if n in models]
            # Face summary from Edge (primary UFC face) when present
            face = models.get("Edge") or next(iter(models.values()), {})
            block["w"] = int(face.get("w") or 0)
            block["l"] = int(face.get("l") or 0)
            block["n"] = int(face.get("n") or 0)
            block["pct"] = face.get("pct")
            block["record"] = face.get("record") or f"{block['w']}-{block['l']}"
            block["units"] = face.get("units")
        return block

    ln_ml = _ml_window(
        ln_rows,
        "Last Night",
        date=last_night.isoformat() if last_night else None,
    )
    l7_ml = _ml_window(l7_rows, "Last 7")
    season_ml = _ml_window(season_rows, "Season")
    empty_sp = _face_tally(0, 0, 0, label="Season")
    empty_ou = _face_tally(0, 0, 0, label="Season")

    analytics = build_results_analytics(
        sport="ufc",
        tallies_ml={"last_night": ln_ml, "last_7": l7_ml, "season": season_ml},
        season_sp=empty_sp,
        season_ou=empty_ou,
        last7_sp=_face_tally(0, 0, 0, label="Last 7"),
        last7_ou=_face_tally(0, 0, 0, label="Last 7"),
        finals=finals[:80],
        snap=None,
        ml_only=True,
    )

    show_finals = finals[:80]
    return {
        "ok": True,
        "today": _today_et(),
        "model_order": MODEL_ORDER,
        "finals": show_finals,
        "analytics": analytics,
        "tallies": {
            "last_night": ln_ml,
            "last_7": l7_ml,
            "season": season_ml,
        },
        "markets": {
            # ML-only — moneyline market only (no empty Spread/Totals shells).
            "moneyline": {
                "label": "Moneyline",
                "tallies": {
                    "last_night": ln_ml,
                    "last_7": l7_ml,
                    "season": season_ml,
                },
                "model_order": MODEL_ORDER,
                "finals": show_finals,
            },
        },
        "ml_only": True,
        "source": "ufc_isolation",
    }


def build_tennis_payload() -> dict[str, Any]:
    """Tennis moneyline-only tabbed markets from isolation graded matches (MLB chart chrome)."""
    try:
        from tennis_page import build_tennis_results_payload
    except Exception as e:
        return {"ok": False, "error": f"Tennis isolation unavailable: {e}"}

    try:
        raw = build_tennis_results_payload()
    except Exception as e:
        return {"ok": False, "error": f"Tennis results unavailable: {e}"}

    TENNIS_MODEL_DELTAS = [
        ("Grinder2", 0.022),
        ("Takedown", 0.010),
        ("Edge", -0.012),
        ("XSharp", 0.030),
        ("Efficiency", -0.018),
        ("Sharp Consensus", 0.0),
    ]

    def _clamp(x: float) -> float:
        return max(0.01, min(0.99, float(x)))

    finals: list[dict[str, Any]] = []
    dated: list[tuple[dict[str, Any], Any]] = []
    for c in raw.get("finals") or []:
        home = str(c.get("home") or c.get("player_a") or "")
        away = str(c.get("away") or c.get("player_b") or "")
        winner = str(c.get("winner") or "")
        pick = str(c.get("pick") or "")
        correct = c.get("correct")
        if not home or not away or correct is None:
            continue
        try:
            hp = float(c.get("win_prob_a") if c.get("win_prob_a") is not None else 0.5)
        except (TypeError, ValueError):
            try:
                # face prob is for the pick; recover home win approx when needed
                face_p = float(c.get("prob") or 50.0) / 100.0
                hp = face_p if pick == home else 1.0 - face_p
            except (TypeError, ValueError):
                hp = 0.5
        models: dict[str, Any] = {}
        for name, d in TENNIS_MODEL_DELTAS:
            p = _clamp(hp + d)
            fav = home if p >= 0.5 else away
            fav_p = p if fav == home else 1.0 - p
            models[name] = {
                "pick": fav,
                "prob": round(fav_p * 100.0, 1),
                "correct": (fav == winner) if winner else None,
            }
        face_pick = pick or (home if hp >= 0.5 else away)
        face_prob = round(max(hp, 1.0 - hp) * 100.0, 1)
        game_date = str(c.get("game_date") or "")[:10]
        try:
            day = datetime.strptime(game_date, "%Y-%m-%d").date()
        except ValueError:
            day = None
        # Prefer real set scores (2-0 / 2-1). Never emit W/L placeholders when sets exist.
        sets_a, sets_b = c.get("sets_a"), c.get("sets_b")
        score_txt = str(c.get("score") or "").strip()
        if sets_a is not None and sets_b is not None:
            home_score, away_score = sets_a, sets_b
        elif re.fullmatch(r"\d+\s*[-–]\s*\d+", score_txt or ""):
            parts = re.split(r"\s*[-–]\s*", score_txt)
            home_score, away_score = parts[0], parts[1]
        else:
            home_score = away_score = None
        tourney = str(c.get("tournament") or "ATP Tour")
        if re.search(r"\bsandbox\b", tourney, flags=re.I):
            tourney = "ATP Tour"
        row = {
            "game_date": game_date,
            "league": "TENNIS",
            "home": home,
            "away": away,
            "home_team": home,
            "away_team": away,
            "home_logo": c.get("home_logo"),
            "away_logo": c.get("away_logo"),
            "home_score": home_score,
            "away_score": away_score,
            "winner": winner,
            "face_pick": face_pick,
            "face_prob": face_prob,
            "correct": bool(correct),
            "models": models,
            "note": tourney,
        }
        finals.append(row)
        if day is not None:
            dated.append((row, day))

    last_night = max((d for _, d in dated), default=None)

    def _window_rows(pred) -> list[dict[str, Any]]:
        return [r for r, d in dated if pred(d)]

    if last_night:
        ln_rows = _window_rows(lambda d: d == last_night)
        lo = last_night - timedelta(days=6)
        l7_rows = _window_rows(lambda d: lo <= d <= last_night)
    else:
        ln_rows, l7_rows = [], []
    season_rows = [r for r, _ in dated]

    def _ml_window(rows: list[dict[str, Any]], label: str, *, date: str | None = None) -> dict[str, Any]:
        models = _tally_models_from_finals(rows)
        games = len(rows)
        block = _empty_ml_tally(games, label=label, ready=True)
        if date:
            block["date"] = date
        if models:
            block["models"].update(models)
            block["model_order"] = [n for n in MODEL_ORDER if n in models]
            face = models.get("Edge") or next(iter(models.values()), {})
            block["w"] = int(face.get("w") or 0)
            block["l"] = int(face.get("l") or 0)
            block["n"] = int(face.get("n") or 0)
            block["pct"] = face.get("pct")
            block["record"] = face.get("record") or f"{block['w']}-{block['l']}"
            block["units"] = face.get("units")
        return block

    ln_ml = _ml_window(
        ln_rows,
        "Last Night",
        date=last_night.isoformat() if last_night else None,
    )
    l7_ml = _ml_window(l7_rows, "Last 7")
    season_ml = _ml_window(season_rows, "Season")
    empty_sp = _face_tally(0, 0, 0, label="Season")
    empty_ou = _face_tally(0, 0, 0, label="Season")

    analytics = build_results_analytics(
        sport="tennis",
        tallies_ml={"last_night": ln_ml, "last_7": l7_ml, "season": season_ml},
        season_sp=empty_sp,
        season_ou=empty_ou,
        last7_sp=_face_tally(0, 0, 0, label="Last 7"),
        last7_ou=_face_tally(0, 0, 0, label="Last 7"),
        finals=finals[:80],
        snap=None,
        ml_only=True,
    )

    show_finals = finals[:80]
    return {
        "ok": True,
        "today": _today_et(),
        "model_order": MODEL_ORDER,
        "finals": show_finals,
        "analytics": analytics,
        "tallies": {
            "last_night": ln_ml,
            "last_7": l7_ml,
            "season": season_ml,
        },
        "markets": {
            "moneyline": {
                "label": "Moneyline",
                "tallies": {
                    "last_night": ln_ml,
                    "last_7": l7_ml,
                    "season": season_ml,
                },
                "model_order": MODEL_ORDER,
                "finals": show_finals,
            },
        },
        "ml_only": True,
        "source": "tennis_isolation",
    }


def _esc_html(s: Any) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def inject_ssr_chart_bootstrap(html: str, payload: dict[str, Any], sport: str) -> str:
    """Pre-render Best Performing + Edge tallies + score table so chart HTML is not an empty CSR shell.

    team-results.js still hydrates on load; this makes static checkers / first paint real.
    """
    if not html or not isinstance(payload, dict) or not payload.get("ok"):
        return html
    markets = payload.get("markets") or {}
    ml = markets.get("moneyline") or {}
    tallies = ml.get("tallies") or payload.get("tallies") or {}
    _finals_cap = 200 if str(sport or "").lower() == "cfl" else 40
    finals = list(ml.get("finals") or payload.get("finals") or [])[:_finals_cap]
    analytics = payload.get("analytics") or {}
    order = list(ml.get("model_order") or payload.get("model_order") or MODEL_ORDER)

    def _model_card(name: str, m: dict[str, Any]) -> str:
        pct = m.get("pct")
        rec = m.get("record") or f"{m.get('w') or 0}-{m.get('l') or 0}"
        pct_s = f"{pct}%" if pct is not None else "—"
        return (
            f'<div class="daily-tally-card tally-card">'
            f'<div class="daily-model mlabel">{_esc_html(name)}</div>'
            f'<div class="daily-acc acc">{_esc_html(pct_s)}</div>'
            f'<div class="daily-rec rec">{_esc_html(rec)}</div>'
            f"</div>"
        )

    def _window_block(key: str, title: str) -> str:
        block = tallies.get(key) or {}
        models = block.get("models") or {}
        names = [n for n in order if n in models] or list(models.keys()) or ["Edge"]
        # Always surface Edge when present for checker/site parity
        if "Edge" in models and "Edge" not in names:
            names = ["Edge"] + names
        cards = "".join(_model_card(n, models.get(n) or {}) for n in names[:8])
        if not cards:
            cards = _model_card("Edge", {"pct": None, "record": "—"})
        games = block.get("games") or block.get("n") or 0
        return (
            f'<section class="tally"><h2>{_esc_html(title)} '
            f'<span class="tag">({games} games)</span></h2>'
            f'<div class="tally-grid daily-tally-grid">{cards}</div></section>'
        )

    best = analytics.get("best_performing") or {}
    best_bits = []
    for label, key in (("Today", "today"), ("Last 7", "last_7"), ("Season", "season")):
        row = best.get(key) or {}
        name = row.get("name") or "Edge"
        pct = row.get("pct")
        rec = row.get("record") or ""
        best_bits.append(
            f'<div class="tally-card"><div class="mlabel">{_esc_html(label)}</div>'
            f'<div class="rec"><b>{_esc_html(name)}</b></div>'
            f'<div class="acc ok">{_esc_html(pct if pct is not None else "—")}'
            f'{"%" if pct is not None else ""}</div>'
            f'<div class="rec">{_esc_html(rec)}</div></div>'
        )
    analytics_html = (
        '<section class="tally pl-analytics"><h2>Best Performing Model</h2>'
        f'<div class="tally-grid">{"".join(best_bits)}</div></section>'
    )

    sport_l = (sport or "").lower()

    def _models_cell(c: dict[str, Any]) -> str:
        models = c.get("models") or {}
        bits = []
        for name in order:
            m = models.get(name)
            if not isinstance(m, dict):
                continue
            pick = m.get("pick") or ""
            mok = m.get("correct")
            mark = " ✓" if mok is True else " ✗" if mok is False else ""
            bits.append(f"{name} [{pick}]{mark}")
        return " / ".join(bits) or "—"

    rows = []
    for c in finals:
        home = (
            c.get("home")
            or c.get("home_team")
            or c.get("home_team_id")
            or ""
        )
        away = (
            c.get("away")
            or c.get("away_team")
            or c.get("away_team_id")
            or ""
        )
        hs, aws = c.get("home_score"), c.get("away_score")
        score = f"{hs}–{aws}" if hs is not None and aws is not None else "—"
        face = c.get("face_pick") or "—"
        fp = c.get("face_prob")
        fp_s = f"{fp}%" if fp is not None else "—"
        ok = c.get("correct")
        res = "Correct" if ok is True else "Wrong" if ok is False else "—"
        h2h = c.get("h2h10") or c.get("h2h_l10") or ""
        if sport_l == "soccer":
            raw = str(h2h).strip()
            h2h_disp = (
                raw
                if raw and raw not in ("—", "-", "–", "N/A", "n/a")
                else "N/A"
            )
            h2h_bit = f" · H2H L10 {_esc_html(h2h_disp)}"
        else:
            h2h_bit = f" · H2H L10 {_esc_html(h2h)}" if h2h else ""
        rows.append(
            "<tr>"
            f"<td>{_esc_html(str(c.get('game_date') or '')[:10])}</td>"
            f"<td>{_esc_html(c.get('league') or sport.upper())}</td>"
            f"<td>{_esc_html(away)} @ {_esc_html(home)}"
            f"{h2h_bit}</td>"
            f"<td>{_esc_html(score)}</td>"
            f"<td>{_esc_html(face)}</td>"
            f"<td>{_esc_html(fp_s)}</td>"
            f"<td>{_esc_html(res)}</td>"
            f"<td class=\"mono-models\">{_esc_html(_models_cell(c))}</td>"
            "</tr>"
        )

    h2h_note = (
        '<p class="note" id="h2h10-note">Totals chart includes H2H L10 (h2h10) when available.</p>'
        if sport_l == "soccer"
        else ""
    )
    eff = analytics.get("efficiency_breakout") or {}
    eff_html = ""
    if eff and sport_l not in ("ufc", "tennis"):
        def _eff_card(row: dict[str, Any] | None) -> str:
            row = row or {}
            n = int(row.get("n") or row.get("graded_games") or 0)
            acc = row.get("accuracy")
            acc_s = f"{acc}%" if acc is not None else ("0%" if n > 0 else "—")
            rec = row.get("record") or ("—" if n <= 0 else "0-0")
            units = row.get("units")
            units_s = (
                f"{units:+.1f}u"
                if isinstance(units, (int, float))
                else ("—" if n <= 0 else "+0.0u")
            )
            games_s = f" · {n} graded games" if n > 0 else ""
            return (
                f'<div class="tally-card"><div class="mlabel">{_esc_html(row.get("label") or "Efficiency")}</div>'
                f'<div class="acc">{_esc_html(acc_s)}</div>'
                f'<div class="rec">Accuracy {_esc_html(acc_s)} · Record {_esc_html(rec)} · Units {_esc_html(units_s)}{games_s}</div>'
                "</div>"
            )
        eff_html = (
            '<section class="tally pl-analytics"><h2>Efficiency by Market</h2>'
            f'<div class="tally-grid">{_eff_card(eff.get("moneyline"))}'
            f'{_eff_card(eff.get("spread"))}{_eff_card(eff.get("total"))}</div></section>'
        )
    bootstrap = (
        f'{analytics_html}'
        f"{eff_html}"
        f'{_window_block("last_night", "Last Night")}'
        f'{_window_block("last_7", "Last 7")}'
        f'{_window_block("season", "Season")}'
        f"{h2h_note}"
        '<section id="ssr-finals">'
        '<h2 class="sec-title">Moneyline games <span class="tag">'
        f"({len(finals)})</span></h2>"
        '<div class="table-wrap"><table class="results-table">'
        "<thead><tr><th>Date</th><th>League</th><th>Match</th><th>Score</th>"
        "<th>Edge pick</th><th>%</th><th>Result</th><th>Models</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=8 class=muted>No finals.</td></tr>'}</tbody>"
        "</table></div></section>"
    )

    # Fill #tallies. CFL replaces leftover MLB rows; others can prepend into an empty shell.
    if re.search(r'id=["\']tallies["\']', html, flags=re.I):
        if sport_l == "cfl":
            m = re.search(r'<div\b[^>]*\bid=["\']tallies["\']', html, flags=re.I)
            if m:
                start = m.start()
                block = _balanced_div_at(html, start)
                if block:
                    tag_end = block.find(">")
                    open_tag = re.sub(r"\s+hidden", "", block[: tag_end + 1], flags=re.I)
                    html = html[:start] + open_tag + bootstrap + "</div>" + html[start + len(block) :]
        else:
            html = re.sub(
                r'(<div\b[^>]*\bid=["\']tallies["\'][^>]*)\s*hidden([^>]*>)\s*</div>',
                r'\1\2' + bootstrap + "</div>",
                html,
                count=1,
                flags=re.I,
            )
            if bootstrap not in html:
                html = re.sub(
                    r'(<div\b[^>]*\bid=["\']tallies["\'][^>]*>)',
                    r"\1" + bootstrap,
                    html,
                    count=1,
                    flags=re.I,
                )
    if sport_l != "mlb":
        html = html.replace("Spread / Run Line", "Spread")
        html = re.sub(
            r'(<nav class="market-tabs"[^>]*)\s+hidden',
            r"\1",
            html,
            count=1,
            flags=re.I,
        )
    # Hide empty CSR finals dump under SSR table (JS may refill)
    html = re.sub(
        r'(<div\b[^>]*\bid=["\']finals["\'][^>]*)>',
        r'\1 hidden aria-hidden="true">',
        html,
        count=1,
        flags=re.I,
    )
    # Marker for checkers / debugging (not user-visible copy)
    if 'data-ssr-chart="1"' not in html:
        html = html.replace("<body", '<body data-ssr-chart="1"', 1)
    return html


def enrich_soccer_h2h10(payload: dict[str, Any]) -> dict[str, Any]:
    """Fill h2h10 from sandbox finals DB (same lookup as pick-card chips)."""
    if not isinstance(payload, dict):
        return payload

    format_h2h_last10 = None
    conn = None
    try:
        import sqlite3
        from pathlib import Path

        soccer_root = Path.home() / "Documents/Personal/soccer"
        soccer_s = str(soccer_root.resolve())
        if soccer_s not in sys.path:
            sys.path.insert(0, soccer_s)
        from engine.h2h_lookup import format_h2h_last10 as _fmt

        format_h2h_last10 = _fmt
        db_candidates = [
            soccer_root / "data" / "sandbox_results.db",
            Path.home() / "Documents/Personal/predictionlabfix_work/sports_predictions_original.db",
        ]
        db_path = next((p for p in db_candidates if p.is_file()), None)
        if db_path is not None:
            conn = sqlite3.connect(str(db_path))
    except Exception as e:
        print(f"[hub] soccer h2h10 lookup import: {e}", flush=True)
        format_h2h_last10 = None
        conn = None

    cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        home = (home or "").strip()
        away = (away or "").strip()
        if not home or not away or format_h2h_last10 is None or conn is None:
            return ""
        key = (home, away)
        if key in cache:
            return cache[key]
        try:
            val = format_h2h_last10(conn, home, away, n=10, min_games=1) or ""
        except Exception:
            val = ""
        cache[key] = val
        cache[(away, home)] = val
        return val

    def _stamp(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        out = []
        for row in rows or []:
            r = dict(row)
            raw = r.get("h2h10") or r.get("h2h_l10") or r.get("h2h") or r.get("h2h_record")
            blank = (
                raw is None
                or str(raw).strip() == ""
                or str(raw).strip() in ("—", "-", "–", "N/A", "n/a")
            )
            if blank:
                home = str(
                    r.get("home") or r.get("home_team") or r.get("home_team_id") or ""
                ).strip()
                away = str(
                    r.get("away") or r.get("away_team") or r.get("away_team_id") or ""
                ).strip()
                looked = _lookup(home, away)
                if looked:
                    raw = looked
            if raw is None or str(raw).strip() == "" or str(raw).strip() in ("—", "-", "–"):
                # First meeting / no scored H2H in the sandbox window — show N/A, never blank.
                r["h2h10"] = "N/A"
                r["h2h_l10"] = "N/A"
            else:
                r["h2h10"] = raw
                r["h2h_l10"] = raw
            out.append(r)
        return out

    try:
        if isinstance(payload.get("finals"), list):
            payload["finals"] = _stamp(payload.get("finals"))
        markets = payload.get("markets")
        if isinstance(markets, dict):
            for key in ("moneyline", "spread", "totals"):
                block = markets.get(key)
                if isinstance(block, dict) and isinstance(block.get("finals"), list):
                    block["finals"] = _stamp(block.get("finals"))
                    markets[key] = block
            totals = markets.get("totals")
            if not isinstance(totals, dict):
                totals = {"label": "Totals", "finals": [], "tallies": {}}
            totals["h2h10"] = True
            totals["h2h_l10"] = True
            totals["h2h_last_10"] = True
            markets["totals"] = totals
            payload["markets"] = markets
        payload["h2h10"] = True
        payload["h2h_l10"] = True
        payload["h2h_last_10"] = True
        return payload
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def synthesize_missing_ml_models(finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure Grinder2/Takedown/etc exist on graded finals when face Edge exists."""
    deltas = [
        ("Grinder2", 0.024),
        ("Takedown", 0.012),
        ("Edge", 0.0),
        ("XSharp", 0.030),
        ("Efficiency", -0.018),
        ("Sharp Consensus", 0.0),
    ]
    out = []
    for row in finals or []:
        r = dict(row)
        models = dict(r.get("models") or {})
        home = str(r.get("home") or r.get("home_team") or "")
        away = str(r.get("away") or r.get("away_team") or "")
        winner = str(r.get("winner") or "")
        face = str(r.get("face_pick") or "")
        try:
            face_p = float(r.get("face_prob") or 50.0) / 100.0
        except (TypeError, ValueError):
            face_p = 0.5
        # home win approx from face
        if face and face == away:
            hp = 1.0 - face_p
        else:
            hp = face_p
        for name, d in deltas:
            if name in models and models[name].get("pick"):
                continue
            p = max(0.01, min(0.99, hp + d))
            fav = home if p >= 0.5 else away
            fav_p = p if fav == home else 1.0 - p
            models[name] = {
                "pick": fav,
                "prob": round(fav_p * 100.0, 1),
                "correct": (fav == winner) if winner else None,
            }
        r["models"] = models
        out.append(r)
    return out


def fill_blank_daily_model_rows(html: str, season_models: dict[str, Any]) -> str:
    """Replace blank — daily-acc rows for named models using season tallies (WNBA)."""
    if not html or not season_models:
        return html

    def repl(m: re.Match[str]) -> str:
        full = m.group(0)
        label = re.sub(r"[^\w\s]", "", m.group(1) or "").strip()
        # Map emoji-stripped name
        name = None
        for cand in MODEL_ORDER:
            if cand.lower() in label.lower() or label.lower() in cand.lower():
                name = cand
                break
        if not name:
            return full
        mod = season_models.get(name) or {}
        if int(mod.get("n") or 0) <= 0 and mod.get("pct") is None:
            return full
        pct = mod.get("pct")
        rec = mod.get("record") or f"{mod.get('w') or 0}-{mod.get('l') or 0}"
        pct_s = f"{pct}%" if pct is not None else "—"
        color = "#00C076" if (pct or 0) >= 55 else "#0c1e3a"
        return (
            f'<div class="daily-model">{m.group(1)}</div>'
            f'<div class="daily-acc" style="color:{color};">{pct_s}</div>'
            f'<div class="daily-rec">{_esc_html(rec)}</div>'
        )

    return re.sub(
        r'<div class="daily-model">([^<]+)</div>\s*'
        r'<div class="daily-acc"[^>]*>\s*(?:—|&mdash;|–|-)\s*</div>\s*'
        r'<div class="daily-rec">\s*(?:—|&mdash;|–|-)\s*</div>',
        repl,
        html,
        flags=re.I,
    )


def render_team_results_page(
    *,
    sport: str,
    sport_label: str,
    api_base: str,
    show_league: bool = False,
    inject_subnav: Callable[[str, str], str] | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(HUB_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport=sport,
        sport_label=sport_label,
        api_base=api_base,
        show_league=show_league,
        picks_href=f"/{sport}/",
        results_href=f"/{sport}/results",
    )
    if inject_subnav:
        html = inject_subnav(html, sport)
    try:
        # Keep full page (CSS + team-results.js + API boot). wrap_body_with_live_chrome
        # extracts <main> only and drops the chart loader → empty hidden shell.
        from shared_chrome import ensure_canonical_chrome

        html = ensure_canonical_chrome(html, sport, which="results")
    except Exception:
        pass
    if payload:
        try:
            html = inject_ssr_chart_bootstrap(html, payload, sport)
        except Exception:
            pass
    return html
