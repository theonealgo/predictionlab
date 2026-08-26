#!/usr/bin/env python3
"""MLB UI SIGNED OFF — do not change without owner request.

# ============================================================
# MLB LOCK — DO NOT MODIFY
# MLB was previously fixed and verified.
# DO NOT change this logic unless the user explicitly says:
# "UNLOCK MLB"
# Changes to other sports must NOT modify MLB behavior.
# ============================================================

Locked 2026-08-10. See notes/MLB_LOCKED.md / qa/MLB_SIGNED_OFF.txt.

MLB results HTML enrichments ported from sandbox team_tabbed_results.

Used by mlb_ui_fixup.apply_mlb_results_fixups. No chrome replacement.

Canonical Season sample (per market) = live Season Performance face cards /
Moneyline Accuracy by Model grid (sandbox-signed graded universe). Locked
season_snapshots JSON is a separate mid-season archive — never unlabeled
"Season" next to face. Use it only under "Full Season Snapshot" labels
(e.g. Run Line Records).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_LIVE_ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = _LIVE_ROOT / "data" / "season_snapshots"

MODEL_ORDER = [
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
]

# WNBA does not publish Grinder2 / Takedown.
WNBA_MODEL_ORDER = [
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
]
_WNBA_DROP_MODELS = frozenset({"Grinder2", "Takedown"})


def model_order_for_sport(sport: str) -> list[str]:
    if (sport or "").strip().lower() == "wnba":
        return list(WNBA_MODEL_ORDER)
    return list(MODEL_ORDER)


def _strip_models_for_sport(block: dict[str, Any] | None, sport: str) -> dict[str, Any] | None:
    """Drop Grinder2/Takedown tiles from WNBA tallies. MLB order unchanged."""
    if not block or (sport or "").strip().lower() != "wnba":
        return block
    order = model_order_for_sport(sport)
    models = block.get("models") or {}
    block["models"] = {k: v for k, v in models.items() if k in order}
    block["model_order"] = [n for n in order if n in block["models"]]
    return block

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
        "graded": n,
        "events": n + (p or 0),
        "pushes": p or 0,
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "units": units,
        "models": {
            label: {
                "w": w,
                "l": l,
                "n": n,
                "graded": n,
                "events": n + (p or 0),
                "pushes": p or 0,
                "pct": pct,
                "record": f"{w}-{l}" + (f"-{p}" if p else ""),
                "units": units,
            }
        },
        "model_order": [label],
    }
    block.update(extra)
    return block


def _parse_wl_record(rec: str) -> tuple[int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", rec or "")]
    w = parts[0] if len(parts) > 0 else 0
    l = parts[1] if len(parts) > 1 else 0
    p = parts[2] if len(parts) > 2 else 0
    return w, l, p


def _honestize_model_block(m: dict[str, Any]) -> dict[str, Any]:
    """Accuracy = W/(W+L). n/graded = W+L. events = W+L+P. Never call pushes graded."""
    if not isinstance(m, dict):
        return m
    out = dict(m)
    w = int(out.get("w") or 0)
    l = int(out.get("l") or 0)
    p = int(out.get("pushes") or 0)
    rw, rl, rp = _parse_wl_record(str(out.get("record") or ""))
    if (rw or rl or rp) and (w + l + p) == 0:
        w, l, p = rw, rl, rp
    elif rp and not p:
        p = rp
    graded = w + l
    events = w + l + p
    out["w"] = w
    out["l"] = l
    out["pushes"] = p
    out["n"] = graded
    out["graded"] = graded
    out["events"] = events
    out["record"] = f"{w}-{l}" + (f"-{p}" if p else "")
    if graded:
        out["pct"] = round(100.0 * w / graded, 1)
    elif out.get("pct") is not None and not graded:
        out["pct"] = None
    if out.get("units") is None:
        out["units"] = _units_from_wl(w, l)
    return out


def _honestize_tally(block: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(block, dict):
        return block
    out = dict(block)
    models = {
        name: _honestize_model_block(m)
        for name, m in (out.get("models") or {}).items()
        if isinstance(m, dict)
    }
    out["models"] = models
    face = None
    for name in (out.get("model_order") or []):
        if name in models:
            face = models[name]
            break
    if face is None and models:
        face = next(iter(models.values()))
    if face:
        out["w"] = face.get("w")
        out["l"] = face.get("l")
        out["pushes"] = face.get("pushes") or 0
        out["n"] = face.get("graded")
        out["graded"] = face.get("graded")
        out["events"] = face.get("events")
        out["record"] = face.get("record")
        out["pct"] = face.get("pct")
        if out.get("units") is None:
            out["units"] = face.get("units")
    else:
        w = int(out.get("w") or 0)
        l = int(out.get("l") or 0)
        p = int(out.get("pushes") or 0)
        out["graded"] = w + l
        out["events"] = w + l + p
        out["n"] = w + l
    return out


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

def _best_model(models: dict[str, Any], *, skip: frozenset[str] | None = None) -> dict[str, Any] | None:
    best = None
    skip = skip or frozenset()
    for name, m in (models or {}).items():
        if name in skip:
            continue
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
    skip_best = _WNBA_DROP_MODELS if (sport or "").strip().lower() == "wnba" else frozenset()

    best = {
        "today": _best_model(ln.get("models") or {}, skip=skip_best),
        "last_7": _best_model(l7.get("models") or {}, skip=skip_best),
        "season": _best_model(season.get("models") or {}, skip=skip_best),
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

_INERT_TYPE_TOGGLE_RE = re.compile(
    r"(?:\s*<!--\s*[─\-]*\s*Type Toggle\s*[─\-]*\s*-->)?"
    r"\s*<div\b[^>]*\bclass=\"[^\"]*\btype-toggle\b[^\"]*\"[^>]*>"
    r"[\s\S]*?</div>",
    re.I,
)


def strip_inert_results_market_toggle(html: str) -> str:
    """Remove the ALL|Moneyline|Spread|Total type-toggle (no-op without section-* targets)."""
    if not html or "type-toggle" not in html:
        return html
    return _INERT_TYPE_TOGGLE_RE.sub("", html)


def inject_mlb_results_analytics_html(
    html: str,
    analytics: dict[str, Any],
    *,
    sport: str = "mlb",
) -> str:
    """Inject Best Model / Efficiency breakout into normal results (no health dashboard)."""
    if not html or not analytics:
        return html
    html = strip_inert_results_market_toggle(html)
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
          .season-perf-compact .daily-tally-grid{{display:none!important}}
          .season-perf-summary{{display:block}}
          @media(max-width:720px){{.pl-analytics-grid{{grid-template-columns:1fr}}}}
        </style>
        """

    # Insert after Moneyline Accuracy by Model grid (before date slider).
    # The old ALL|Moneyline|Spread|Total type-toggle was a no-op and is stripped.
    m = re.search(
        r'(Moneyline Accuracy by Model[\s\S]*?</div>\s*</div>\s*)(\s*<!--\s*──\s*Type Toggle|\s*<!--\s*──\s*Date Slider|\s*<div class="type-toggle"|\s*<div class="date-nav")',
        html,
        re.I,
    )
    if m:
        return strip_inert_results_market_toggle(html[: m.end(1)] + panel + html[m.end(1) :])
    i = html.find("Moneyline Accuracy by Model")
    if i >= 0:
        j = html.find('<div class="type-toggle"', i)
        if j < 0:
            j = html.find("<!-- ── Type Toggle", i)
        if j < 0:
            j = html.find('<div class="date-nav"', i)
        if j < 0:
            j = html.find("<!-- ── Date Slider", i)
        if j >= 0:
            return strip_inert_results_market_toggle(html[:j] + panel + html[j:])

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
            "grade": "WIN" if mark == "ok" else "LOSS" if mark == "no" else None,
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
            "push": "push" in pick.lower(),
            "grade": "PUSH" if "push" in pick.lower() else (
                "WIN" if mark == "ok" else "LOSS" if mark == "no" else None
            ),
        }
    row_re = re.compile(
        r'<td class="market-k">([^<]+)</td>\s*'
        r'<td class="val-books">([^<]*)</td>\s*'
        r'<td class="val-pl">([^<]*)</td>\s*'
        r'<td class="val-xs">([^<]*)</td>',
        re.I,
    )
    for m in row_re.finditer(card_html or ""):
        kind = (m.group(1) or "").strip().lower()
        books = _clean_team_label(m.group(2))
        pl = _clean_team_label(m.group(3))
        xs = _clean_team_label(m.group(4))
        if kind in ("run line", "spread"):
            spread = spread or {}
            spread["book"] = books
            spread["pl_pick"] = pl
            spread["xs_pick"] = xs
        elif kind == "total":
            totals = totals or {}
            totals["book"] = books
            totals["book_line"] = _parse_line_number(books)
            totals["pl_pick"] = pl
            totals["xs_pick"] = xs
            totals["pl_line"] = _parse_line_number(pl)
            totals["xs_line"] = _parse_line_number(xs)
    return spread, totals


def _parse_line_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", str(raw).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _grade_total_proj(
    proj: float | None, book: float | None, actual: float | None
) -> str | None:
    if book is None or actual is None:
        return None
    if abs(actual - book) < 1e-9:
        return "PUSH"
    if proj is None or abs(proj - book) < 1e-9:
        return None
    lean_over = proj > book
    hit_over = actual > book
    if lean_over:
        return "WIN" if hit_over else "LOSS"
    return "WIN" if not hit_over else "LOSS"


def _apply_pl_xs_grades(row: dict[str, Any]) -> None:
    totals = row.get("totals")
    hs = row.get("home_score")
    aa = row.get("away_score")
    if isinstance(totals, dict) and hs is not None and aa is not None:
        actual = float(hs) + float(aa)
        book = totals.get("book_line")
        totals["pl_grade"] = _grade_total_proj(totals.get("pl_line"), book, actual)
        totals["xs_grade"] = _grade_total_proj(totals.get("xs_line"), book, actual)
        if book is not None and abs(actual - float(book)) < 1e-9:
            totals["push"] = True
            totals["correct"] = None
            totals["grade"] = "PUSH"

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
    gid_m = re.search(r'data-game-id="([^"]*)"', card_html, re.I)
    action_m = re.search(r'data-spread-action="([^"]*)"', card_html, re.I)
    our_m = re.search(r'data-our-spread="([^"]*)"', card_html, re.I)
    our_spread = None
    if our_m and (our_m.group(1) or "").strip():
        try:
            our_spread = float(our_m.group(1))
        except ValueError:
            our_spread = None
    h2h_m = re.search(
        r'H2H Last 10</span>\s*<span class="sf-val">([^<]+)',
        card_html,
        re.I,
    )
    time_m = re.search(
        r'data-time="([^"]+)"|class="t-time"[^>]*>\s*([^<]+)|'
        r'class="card-hero-meta-line">([^<]+)|'
        r'(\d{1,2}:\d{2}\s*[AP]M(?:\s*ET)?)',
        card_html,
        re.I,
    )
    game_time = ""
    if time_m:
        game_time = (
            time_m.group(1) or time_m.group(2) or time_m.group(3) or time_m.group(4) or ""
        ).strip()
    pl_proj = ""
    xs_proj = ""
    for pm in re.finditer(
        r'<span class="proj-model[^"]*">([^<]*)</span>\s*'
        r'<span class="proj-val">([^<]+)</span>',
        card_html,
        re.I | re.S,
    ):
        name = _strip_emoji(pm.group(1)).lower()
        val = _clean_team_label(pm.group(2))
        if "prediction lab" in name or name.strip() == "pl":
            pl_proj = val
        elif "xsharp" in name or name.strip() == "xs":
            xs_proj = val
    if isinstance(totals, dict):
        if pl_proj:
            totals["pl_proj"] = pl_proj
        if xs_proj:
            totals["xs_proj"] = xs_proj
    row = {
        "game_id": (gid_m.group(1) if gid_m else "") or None,
        "spread_action": (action_m.group(1) if action_m else "") or None,
        "our_spread": our_spread,
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
        "h2h10": _clean_team_label(h2h_m.group(1)) if h2h_m else "",
        "game_time": game_time or "Final",
        "pl_proj": pl_proj,
        "xs_proj": xs_proj,
        "total_ev": None,
    }
    _apply_pl_xs_grades(row)
    return row


def _extract_game_rows(html: str, *, limit: int = 800) -> list[dict[str, Any]]:
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
    # Keep summary face on first model in order for the season line.
    first = models.get(order[0]) or {}
    for k in ("w", "l", "n", "pct", "record", "units", "games"):
        if first.get(k) is not None:
            out[k] = first.get(k) if k != "games" else first.get("n")
    return out

def _snapshot_season(sport: str) -> dict[str, Any]:
    """Season Spread/Totals (+ ML face) from locked snapshot when present.

    Matches independent_sports/hub/team_tabbed_results.py — required so
    `_mlb_run_line_record_cards_from_snapshot` can read snap['spread']['models'].
    """
    candidates = sorted(SNAPSHOT_DIR.glob(f"{sport.upper()}_*_regular.json"), reverse=True)
    if not candidates:
        return {}
    try:
        blob = json.loads(candidates[0].read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(blob, dict):
        return {}
    sp = blob.get("season_perf") or {}
    st = blob.get("spread_total_stats") or {}
    win = blob.get("window") or {}

    def _sou(covered_key: str, graded_key: str, pct_key: str, push_key: str, label_key: str):
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
            pct=pct,
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


def synthesize_missing_ml_models(finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Passthrough — full synthesis lives in sandbox hub; not required for MLB analytics cards."""
    return finals or []

def markets_from_live_html(html: str, sport: str) -> dict[str, Any]:
    """Convert live results HTML tallies into soccer-style markets payload."""
    sections = _extract_tally_sections(html)
    season_roi = _extract_season_roi(html)
    snap = _snapshot_season(sport)
    # Do not regex 800 season cards on the request path — that hung live /mlb-results.
    finals = []

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
    # Fill blank named models (Efficiency, and G2/TD on sports that publish them).
    season_ml = _backfill_blank_ml_models(season_ml, finals)
    ln_ml = _backfill_blank_ml_models(ln_ml, finals[:40])
    l7_ml = _backfill_blank_ml_models(l7_ml, finals[:80])
    season_ml = _strip_models_for_sport(season_ml, sport)
    ln_ml = _strip_models_for_sport(ln_ml, sport)
    l7_ml = _strip_models_for_sport(l7_ml, sport)
    # Face Season Performance merges into dual PL/XSharp (does not drop the other model).
    if season_roi.get("spread"):
        season_sp = _merge_face_roi_into_sou(season_sp, season_roi["spread"], sport=sport)
    if season_roi.get("totals"):
        season_ou = _merge_face_roi_into_sou(season_ou, season_roi["totals"], sport=sport)

    # Attach units on face season/last7 blocks
    for blk in (season_sp, season_ou, l7_sp, l7_ou, ln_sp, ln_ou):
        if isinstance(blk, dict) and blk.get("units") is None:
            blk["units"] = _units_from_wl(int(blk.get("w") or 0), int(blk.get("l") or 0))

    def _spread_window_ids(date_from: str | None, date_to: str | None, exact: str | None = None):
        ids = []
        w = l = 0
        for f in finals:
            d = str(f.get("game_date") or "")[:10]
            if exact and d != exact:
                continue
            if not exact:
                if date_from and d < date_from:
                    continue
                if date_to and d > date_to:
                    continue
            action = (f.get("spread_action") or "").upper()
            side = None
            if action in ("HOME", "AWAY"):
                side = action
            elif f.get("spread") and f["spread"].get("pick"):
                side = "PICK"
            if side is None or action == "NO BET":
                continue
            gid = f.get("game_id") or f"{d}|{f.get('away_team_id')}|{f.get('home_team_id')}"
            ids.append(gid)
            if f.get("spread") and f["spread"].get("correct") is True:
                w += 1
            elif f.get("spread") and f["spread"].get("correct") is False:
                l += 1
        return {"game_ids": ids, "w": w, "l": l, "n": w + l}

    ln_date = ln_sp.get("date") or (by_kind.get("last_night") or {}).get("date")
    l7_from = l7_sp.get("date_from") or (by_kind.get("last_7") or {}).get("date_from")
    l7_to = l7_sp.get("date_to") or (by_kind.get("last_7") or {}).get("date_to") or ln_date
    spread_windows = {
        "last_night": {"date": ln_date, **_spread_window_ids(None, None, exact=ln_date)},
        "last_7": {
            "date_from": l7_from,
            "date_to": l7_to,
            **_spread_window_ids(l7_from, l7_to),
        },
        "season_label": (
            (season_roi.get("spread") or {}).get("model_label")
            or (season_sp.get("model_order") or ["Prediction Lab"])[0]
        ),
    }

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

    if isinstance(season_sp, dict) and season_sp.get("locked"):
        season_sp = {**season_sp, "label": "Season snapshot"}
    if isinstance(season_ou, dict) and season_ou.get("locked"):
        season_ou = {**season_ou, "label": "Season snapshot"}
    if (sport or "").lower() == "mlb" and isinstance(season_ml, dict):
        if not season_ml.get("date_from"):
            for src in (season_ou, season_sp):
                if isinstance(src, dict) and src.get("date_from"):
                    season_ml["date_from"] = src.get("date_from")
                    season_ml["date_to"] = src.get("date_to")
                    break
        if season_ml.get("date_from") and season_ml.get("label") in (None, "", "Season"):
            season_ml["label"] = "Season snapshot"

    def _fin(market_key: str) -> list[dict[str, Any]]:
        hit = [f for f in finals if f.get(market_key)]
        return hit or list(finals)

    ln_ml = _honestize_tally(ln_ml)
    l7_ml = _honestize_tally(l7_ml)
    season_ml = _honestize_tally(season_ml)
    ln_sp = _honestize_tally({**ln_sp, "label": "Last Night"})
    l7_sp = _honestize_tally({**l7_sp, "label": "Last 7"})
    season_sp = _honestize_tally({**season_sp, "label": season_sp.get("label") or "Season"})
    ln_ou = _honestize_tally({**ln_ou, "label": "Last Night"})
    l7_ou = _honestize_tally({**l7_ou, "label": "Last 7"})
    season_ou = _honestize_tally({**season_ou, "label": season_ou.get("label") or "Season"})

    out = {
        "ok": True,
        "today": _today_et(),
        "model_order": model_order_for_sport(sport),
        "finals": finals,
        "spread_windows": spread_windows,
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
                "model_order": model_order_for_sport(sport),
                "finals": finals,
            },
            "spread": {
                "label": "Spread" if (sport or "").lower() != "mlb" else "Run Line",
                "windows": spread_windows,
                "tallies": {
                    "last_night": {**ln_sp, "label": "Last Night"},
                    "last_7": {**l7_sp, "label": "Last 7"},
                    "season": {**season_sp, "label": season_sp.get("label") or "Season"},
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
                "finals": _fin("spread"),
            },
            "totals": {
                "label": "Totals",
                "tallies": {
                    "last_night": {**ln_ou, "label": "Last Night"},
                    "last_7": {**l7_ou, "label": "Last 7"},
                    "season": {**season_ou, "label": season_ou.get("label") or "Season"},
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
                "finals": _fin("totals"),
            },
        },
    }
    for _mk, _m in out["markets"].items():
        fins = list(_m.get("finals") or [])
        ids = [str(f.get("game_id") or "") for f in fins if f.get("game_id")]
        _m["records_shown"] = len(fins)
        _m["unique_games"] = len(set(ids)) if ids else len(fins)
        _m["ungraded"] = sum(
            1
            for f in fins
            if (_mk == "moneyline" and f.get("correct") is None)
            or (
                _mk != "moneyline"
                and ((f.get(_mk) or {}).get("grade") not in ("WIN", "LOSS", "PUSH"))
            )
        )
    return out


def fix_mlb_results_display(html: str) -> str:
    """Fill blank Season Efficiency + inject results analytics (sandbox signed-off)."""
    if not html:
        return html
    html = strip_inert_results_market_toggle(html)
    if "Moneyline Accuracy by Model" not in html:
        return html
    try:
        # Drop prior analytics so Season sample unification always re-applies.
        if 'class="pl-mlb-analytics"' in html or "class='pl-mlb-analytics'" in html:
            html = re.sub(
                r'<section class="pl-mlb-analytics"[\s\S]*?</section>\s*'
                r'(?:<style>[\s\S]*?\.pl-mlb-analytics[\s\S]*?</style>\s*)?',
                '',
                html,
                count=1,
                flags=re.I,
            )
        html = patch_mlb_season_efficiency_html(html)
        html = enrich_mlb_tally_units_html(html)
        payload = markets_from_live_html(html, "mlb")
        analytics = payload.get("analytics") or {}
        if analytics:
            html = inject_mlb_results_analytics_html(html, analytics, sport="mlb")
    except Exception as e:
        print(f"[mlb_results_ui] fix_mlb_results_display: {e}", flush=True)
    return html


def _esc_html(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def mlb_results_view_toggle_html(*, active: str = "normal") -> str:
    """Cards|Chart toggle matching sandbox `/mlb/results` (work2 URLs)."""
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/mlb-results">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/mlb-results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 16px 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def inject_mlb_results_view_toggle(html: str, *, active: str = "normal") -> str:
    """Inject Cards|Chart toggle into live MLB results cards HTML (keeps live chrome)."""
    if not html:
        return html
    if 'class="pl-view-toggle"' in html or "class='pl-view-toggle'" in html:
        return html
    bar = mlb_results_view_toggle_html(active=active)
    if re.search(r"<main\b", html, re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + bar, html, count=1, flags=re.I)
    if re.search(r'class="container\b', html, re.I):
        return re.sub(
            r'(<div class="container\b[^"]*"[^>]*>)',
            r"\1" + bar,
            html,
            count=1,
            flags=re.I,
        )
    return bar + html


def inject_ssr_chart_bootstrap(html: str, payload: dict[str, Any], sport: str) -> str:
    """Pre-render Best Performing + Edge tallies + score table for chart first paint."""
    if not html or not isinstance(payload, dict) or not payload.get("ok"):
        return html
    markets = payload.get("markets") or {}
    ml = markets.get("moneyline") or {}
    tallies = ml.get("tallies") or payload.get("tallies") or {}
    finals = list(ml.get("finals") or payload.get("finals") or [])[:40]
    analytics = payload.get("analytics") or {}
    order = list(ml.get("model_order") or payload.get("model_order") or model_order_for_sport(sport))
    if (sport or "").strip().lower() == "wnba":
        order = [n for n in order if n not in _WNBA_DROP_MODELS] or list(WNBA_MODEL_ORDER)

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

    rows = []
    for c in finals:
        home = c.get("home") or c.get("home_team") or ""
        away = c.get("away") or c.get("away_team") or ""
        hs, aws = c.get("home_score"), c.get("away_score")
        score = f"{aws}–{hs}" if hs is not None and aws is not None else "—"
        face = c.get("face_pick") or "—"
        fp = c.get("face_prob")
        fp_s = f"{fp}%" if fp is not None else "—"
        ok = c.get("correct")
        res = "Correct" if ok is True else "Wrong" if ok is False else "—"
        rows.append(
            "<tr>"
            f"<td>{_esc_html(str(c.get('game_date') or '')[:10])}</td>"
            f"<td>{_esc_html(c.get('league') or sport.upper())}</td>"
            f"<td>{_esc_html(away)} @ {_esc_html(home)}</td>"
            f"<td>{_esc_html(score)}</td>"
            f"<td>{_esc_html(face)}</td>"
            f"<td>{_esc_html(fp_s)}</td>"
            f"<td>{_esc_html(res)}</td>"
            "<td class=\"mono-models\">Edge</td>"
            "</tr>"
        )

    bootstrap = (
        f"{analytics_html}"
        f'{_window_block("last_night", "Last Night")}'
        f'{_window_block("last_7", "Last 7")}'
        f'{_window_block("season", "Season")}'
        '<section id="ssr-finals">'
        '<h2 class="sec-title">Moneyline games <span class="tag">'
        f"({len(finals)})</span></h2>"
        '<div class="table-wrap"><table class="results-table">'
        "<thead><tr><th>Date</th><th>League</th><th>Match</th><th>Score</th>"
        "<th>Edge pick</th><th>%</th><th>Result</th><th>Models</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=8 class=muted>No finals.</td></tr>'}</tbody>"
        "</table></div></section>"
    )

    if re.search(r'id=["\']tallies["\']', html, flags=re.I):
        html = re.sub(
            r'(<div\b[^>]*\bid=["\']tallies["\'][^>]*)\s*hidden([^>]*>)\s*</div>',
            r"\1\2" + bootstrap + "</div>",
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
    html = re.sub(
        r'(<div\b[^>]*\bid=["\']finals["\'][^>]*)>',
        r'\1 hidden aria-hidden="true">',
        html,
        count=1,
        flags=re.I,
    )
    if 'data-ssr-chart="1"' not in html:
        html = html.replace("<body", '<body data-ssr-chart="1"', 1)
    # Unhide market tabs for first paint (JS also toggles)
    html = re.sub(
        r'(<nav\b[^>]*\bid=["\']market-tabs["\'][^>]*)\s*hidden',
        r"\1",
        html,
        count=1,
        flags=re.I,
    )
    return html


def render_mlb_results_chart_page(payload: dict[str, Any] | None = None) -> str:
    """Sandbox-parity MLB results chart page (team-results.js + SSR bootstrap)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(_LIVE_ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport="mlb",
        sport_label="MLB",
        api_base="/mlb/api",
        show_league=False,
        picks_href="/mlb-picks",
        results_href="/mlb-results",
    )
    html = inject_mlb_results_view_toggle(html, active="chart")
    if 'id="league-controls" hidden' not in html:
        html = html.replace('id="league-controls"', 'id="league-controls" hidden')
    if payload:
        try:
            html = inject_ssr_chart_bootstrap(html, payload, "mlb")
        except Exception as e:
            print(f"[mlb_results_ui] chart SSR bootstrap: {e}", flush=True)
    return html
