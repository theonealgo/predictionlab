#!/usr/bin/env python3
"""Soccer-style Moneyline | Spread | Totals results for hub team sports.

Builds markets payloads for MLB / WNBA (from live results HTML tallies + season
snapshots) and CFL (from isolation pipeline). Never imports NHL77FINAL.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HUB_DIR = Path(__file__).resolve().parent
LIVE_ROOT = Path.home() / "Documents/Personal/predictionlabfix_work"
SNAPSHOT_DIR = LIVE_ROOT / "data" / "season_snapshots"
CFL_ISO = Path.home() / "Documents/Personal/cfl"

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


def _model_block(name: str, pct: float | None, rec: str) -> dict[str, Any]:
    w, l, p = _parse_record(rec)
    n = w + l
    return {
        "w": w,
        "l": l,
        "n": n,
        "pushes": p,
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
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
    pct = round(100.0 * w / n, 1) if n else None
    label = extra.pop("model_label", "Prediction Lab")
    block = {
        "games": games if games else n,
        "w": w,
        "l": l,
        "n": n,
        "pct": pct,
        "record": f"{w}-{l}" + (f"-{p}" if p else ""),
        "models": {
            label: {
                "w": w,
                "l": l,
                "n": n,
                "pct": pct,
                "record": f"{w}-{l}" + (f"-{p}" if p else ""),
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
        ("moneyline", r"Moneyline[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)"),
        ("spread", r"Spread[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)"),
        ("totals", r"O/U[^<]*</div>\s*<div[^>]*>\s*([\d.]+)%\s*</div>\s*<div[^>]*>\s*([\d]+-[\d]+(?:-[\d]+)?)"),
    ):
        m = re.search(pat, chunk, re.I | re.S)
        if not m:
            continue
        pct = _parse_pct(m.group(1))
        w, l, p = _parse_record(m.group(2))
        out[key] = {"pct": pct, "w": w, "l": l, "pushes": p, "n": w + l, "games": w + l}
    return out


def _extract_game_rows(html: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """Best-effort finals rows from live game cards."""
    rows: list[dict[str, Any]] = []
    date_chunks = re.split(r'<div id="date-([^"]+)"[^>]*>', html or "")
    it = iter(date_chunks[1:])
    for date_key in it:
        content = next(it, "")
        # Prefer explicit matchup spans when present; otherwise skip noisy card scrape.
        for gm in re.finditer(
            r'class="(?:away-team|team-away|matchup-away)"[^>]*>([^<]+)</[^>]+>\s*'
            r'.*?class="(?:home-team|team-home|matchup-home)"[^>]*>([^<]+)<',
            content[:80000],
            re.I | re.S,
        ):
            away = re.sub(r"\s+", " ", gm.group(1)).strip()
            home = re.sub(r"\s+", " ", gm.group(2)).strip()
            if len(away) < 2 or len(home) < 2:
                continue
            rows.append(
                {
                    "game_date": str(date_key)[:10],
                    "league": "",
                    "away_team_id": away[:60],
                    "home_team_id": home[:60],
                    "away_score": None,
                    "home_score": None,
                    "final": True,
                    "face_pick": None,
                    "face_prob": None,
                    "correct": None,
                    "models": {},
                }
            )
            if len(rows) >= limit:
                return rows
        if rows:
            continue
        # Fallback: "Away @ Home" text near pick-card-header
        for gm in re.finditer(
            r"([A-Za-z0-9][A-Za-z0-9\s\.\'\-]{1,40})\s+@\s+"
            r"([A-Za-z0-9][A-Za-z0-9\s\.\'\-]{1,40})",
            content[:30000],
        ):
            away = re.sub(r"\s+", " ", gm.group(1)).strip()
            home = re.sub(r"\s+", " ", gm.group(2)).strip()
            if "http" in away.lower() or "http" in home.lower():
                continue
            rows.append(
                {
                    "game_date": str(date_key)[:10],
                    "league": "",
                    "away_team_id": away[:60],
                    "home_team_id": home[:60],
                    "away_score": None,
                    "home_score": None,
                    "final": True,
                    "face_pick": None,
                    "face_prob": None,
                    "correct": None,
                    "models": {},
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


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

    return {
        "moneyline": ml_season,
        "spread": _sou("spread_covered", "spread_graded", "spread_pct", "spread_pushes", "spread_model_label"),
        "totals": _sou("ou_correct", "ou_graded", "ou_pct", "total_pushes", "ou_model_label"),
    }


def markets_from_live_html(html: str, sport: str) -> dict[str, Any]:
    """Convert live results HTML tallies into soccer-style markets payload."""
    sections = _extract_tally_sections(html)
    season_roi = _extract_season_roi(html)
    snap = _snapshot_season(sport)
    finals = _extract_game_rows(html)

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

    # Season: prefer snapshot; fill gaps from ROI grid
    season_ml = snap.get("moneyline") or _empty_ml_tally(0, label="Season")
    season_sp = snap.get("spread") or _face_tally(0, 0, 0, label="Season")
    season_ou = snap.get("totals") or _face_tally(0, 0, 0, label="Season")
    if season_roi.get("moneyline") and not (season_ml.get("games") or 0):
        r = season_roi["moneyline"]
        season_ml = _empty_ml_tally(r["games"], label="Season")
        season_ml["models"]["Prediction Lab"] = _model_block(
            "Prediction Lab", r.get("pct"), f"{r['w']}-{r['l']}"
        )
    if season_roi.get("spread") and not (season_sp.get("games") or 0):
        r = season_roi["spread"]
        season_sp = _face_tally(r["games"], r["w"], r["l"], r.get("pushes") or 0, label="Season")
    if season_roi.get("totals") and not (season_ou.get("games") or 0):
        r = season_roi["totals"]
        season_ou = _face_tally(r["games"], r["w"], r["l"], r.get("pushes") or 0, label="Season")

    # Prefer live ROI season face numbers when snapshot differs in labeling
    if season_roi.get("spread"):
        r = season_roi["spread"]
        season_sp = _face_tally(
            r["games"], r["w"], r["l"], r.get("pushes") or 0, label="Season",
            model_label="Prediction Lab", ready=True,
        )
    if season_roi.get("totals"):
        r = season_roi["totals"]
        season_ou = _face_tally(
            r["games"], r["w"], r["l"], r.get("pushes") or 0, label="Season",
            model_label="Prediction Lab", ready=True,
        )

    return {
        "ok": True,
        "today": _today_et(),
        "model_order": MODEL_ORDER,
        "finals": finals,
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
                "label": "Spread",
                "tallies": {
                    "last_night": {**ln_sp, "label": "Last Night"},
                    "last_7": {**l7_sp, "label": "Last 7"},
                    "season": {**season_sp, "label": "Season"},
                },
                "model_order": ["Prediction Lab"],
                "finals": [f for f in finals if f.get("spread")][:80] or finals[:40],
            },
            "totals": {
                "label": "Totals",
                "tallies": {
                    "last_night": {**ln_ou, "label": "Last Night"},
                    "last_7": {**l7_ou, "label": "Last 7"},
                    "season": {**season_ou, "label": "Season"},
                },
                "model_order": ["Prediction Lab"],
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
        rows = pipe.list_graded_results(days=120)
    except Exception as e:
        return {"ok": False, "error": f"CFL results unavailable: {e}"}

    today = datetime.now(ZoneInfo("America/New_York")).date()
    by_day: dict[str, list] = {}
    for r in rows:
        dk = str(r.get("game_date") or "")[:10]
        if len(dk) >= 10:
            by_day.setdefault(dk, []).append(r)
    dates = sorted(by_day.keys(), reverse=True)
    last_night = dates[0] if dates else today.strftime("%Y-%m-%d")
    try:
        anchor = datetime.strptime(last_night, "%Y-%m-%d").date()
    except Exception:
        anchor = today
        last_night = today.strftime("%Y-%m-%d")
    last7 = {(anchor - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, 7)}

    def grade_ml(r: dict) -> bool | None:
        g = r.get("grade")
        if g == "WIN":
            return True
        if g == "LOSS":
            return False
        return None

    def grade_spread(r: dict) -> tuple[bool | None, bool]:
        """Return (correct, push). Home-centric model_spread vs actual margin."""
        sp = r.get("model_spread")
        hs, as_ = r.get("home_score"), r.get("away_score")
        if sp is None or hs is None or as_ is None:
            return None, False
        try:
            sp_f = float(sp)
            margin = float(hs) - float(as_)
        except (TypeError, ValueError):
            return None, False
        # Cover home spread: margin + (-sp) if sp is home line... model_spread home-centric
        # Positive model_spread → home favored by that many points.
        adj = margin - sp_f
        if abs(adj) < 1e-9:
            return None, True
        # Model pick: home if projected to cover its line
        pick_home = sp_f >= 0
        covered = (margin + abs(sp_f) > 0) if pick_home else ( -margin + abs(sp_f) > 0)
        # Simpler ATS: home gets -sp_f; cover if margin > sp_f when home favored
        cover_home = margin > sp_f
        if abs(margin - sp_f) < 1e-9:
            return None, True
        return (cover_home if pick_home else not cover_home), False

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
        n = w + l
        pct = round(100.0 * w / n, 1) if n else None
        return {
            "games": games,
            "models": {
                "Prediction Lab": {
                    "w": w,
                    "l": l,
                    "n": n,
                    "pct": pct,
                    "record": f"{w}-{l}",
                }
            },
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
    for r in rows[:80]:
        card = {
            "game_date": str(r.get("game_date") or "")[:10],
            "league": "CFL",
            "away_team_id": r.get("away_team"),
            "home_team_id": r.get("home_team"),
            "away_score": r.get("away_score"),
            "home_score": r.get("home_score"),
            "final": True,
            "face_pick": r.get("pick_ml"),
            "face_prob": None,
            "correct": grade_ml(r),
            "models": {},
        }
        sp_ok, sp_push = grade_spread(r)
        if sp_ok is not None or sp_push:
            card["spread"] = {
                "pick": f"Model {r.get('model_spread')}",
                "correct": sp_ok,
                "push": sp_push,
            }
        ou_ok, ou_push = grade_total(r)
        if ou_ok is not None or ou_push:
            card["totals"] = {
                "pick": f"O/U {r.get('model_total')}",
                "correct": ou_ok,
                "push": ou_push,
            }
        finals.append(card)

    return {
        "ok": True,
        "today": _today_et(),
        "model_order": ["Prediction Lab"],
        "finals": finals,
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
                "model_order": ["Prediction Lab"],
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


def render_team_results_page(
    *,
    sport: str,
    sport_label: str,
    api_base: str,
    show_league: bool = False,
    inject_subnav: Callable[[str, str], str] | None = None,
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
    return html
