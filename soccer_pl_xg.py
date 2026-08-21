"""Soccer-only PL Expected Goals attach + display.

Locked model: last-15, half-life 12, average formulation, venue shrink, Elo,
league env, h2h_weight=0. Does not write generic ``h2h_last10_*`` used by
MLB/NBA/NFL/NHL/other sports. Does not overwrite soccer ``our_spread``
(Efficiency = PL spread favorite).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import sqlite3
import threading
import unicodedata
from datetime import date as _date
from pathlib import Path
from typing import Iterable

from soccer_pl_expected import (
    GLOBAL_AWAY_MU,
    GLOBAL_HOME_MU,
    PLXGConfig,
    PLXGPrediction,
    PLXGState,
    clamp_goals,
    date_key,
    over_probs,
)

_LIVE_ROOT = Path(__file__).resolve().parent
_ISO_SOCCER = Path.home() / "Documents/Personal/soccer"
_LOCKED = _LIVE_ROOT / "data" / "soccer_pl_xg_locked.json"
_ISO_LOCKED = _ISO_SOCCER / "data" / "pl_xg_walkforward_20260819" / "locked_params.json"

logger = logging.getLogger(__name__)

XG_MISSING = "Not enough prior matches for this team to compute expected goals."
XG_MISSING_TEAMS = "Matchup teams are missing, so expected goals cannot be shown."
MIN_TEAM_GAMES = 1
LEAGUE_AVG_METHOD = "league-avg"

_H2H_LABEL_RE = re.compile(r"H2H\s*Last\s*10", re.I)
_H2H_TH_RE = re.compile(r"(<th[^>]*>)\s*H2H\s*L10\s*(</th>)", re.I)
_H2H_ANY_RE = re.compile(r"H2H\s*(?:Last\s*10|L10)", re.I)

_state_lock = threading.Lock()
_walk_cache: tuple[float, PLXGState, dict, dict] | None = None


def _fold(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def locked_config() -> PLXGConfig:
    for path in (_LOCKED, _ISO_LOCKED):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        d = raw.get("config") or raw
        keys = PLXGConfig.__dataclass_fields__
        return PLXGConfig(**{k: d[k] for k in keys if k in d})
    return PLXGConfig(
        name="w15_hl12.0_avg",
        window=15,
        half_life=12.0,
        venue_split=True,
        opp_adjust=True,
        league_env=True,
        formulation="avg",
        h2h_weight=0.0,
    )


def format_plxg_face(pred: PLXGPrediction) -> str:
    """Owner face: ``2.53 · Home 1.72 · Away 0.81``."""
    return (
        f"{pred.total:.2f} · Home {pred.home:.2f} · Away {pred.away:.2f}"
    )


def league_avg_prediction(league: str = "") -> PLXGPrediction:
    """Cold-start / unknown clubs: league (or global) prior, never a dash."""
    eh, ea = clamp_goals(GLOBAL_HOME_MU, GLOBAL_AWAY_MU)
    probs = over_probs(eh, ea, rho=-0.08)
    return PLXGPrediction(
        home=eh,
        away=ea,
        total=eh + ea,
        n_home=0,
        n_away=0,
        home_attack=eh,
        home_defense=ea,
        away_attack=ea,
        away_defense=eh,
        league_mu_home=GLOBAL_HOME_MU,
        league_mu_away=GLOBAL_AWAY_MU,
        method=LEAGUE_AVG_METHOD,
        used_h2h=False,
        p_over_15=probs["p_over_15"],
        p_over_25=probs["p_over_25"],
        p_over_35=probs["p_over_35"],
        p_under_15=probs["p_under_15"],
        p_under_25=probs["p_under_25"],
        p_under_35=probs["p_under_35"],
    )


def _game_db_paths() -> list[Path]:
    paths = [
        _ISO_SOCCER / "data" / "sandbox_results.db",
        _LIVE_ROOT / "sports_predictions_original.db",
        _LIVE_ROOT / "data" / "sports_predictions_original.db",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _load_scored_games() -> list[dict]:
    by_id: dict[str, dict] = {}
    for path in _game_db_paths():
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT game_id, game_date, home_team_id, away_team_id,
                       home_score, away_score, league
                FROM games
                WHERE sport = 'SOCCER'
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                ORDER BY date(game_date), game_id
                """
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.debug("[soccer-plxg] load %s failed: %s", path, exc)
            continue
        for raw in rows:
            g = dict(raw)
            gid = str(g.get("game_id") or "")
            key = gid or "|".join(
                (
                    date_key(g.get("game_date")),
                    _fold(g.get("home_team_id") or ""),
                    _fold(g.get("away_team_id") or ""),
                )
            )
            prev = by_id.get(key)
            if prev is None:
                by_id[key] = g
                continue
            # Prefer the row with a later file / same identity; keep first if equal.
            if str(g.get("game_date") or "") > str(prev.get("game_date") or ""):
                by_id[key] = g
    games = list(by_id.values())
    games.sort(key=lambda g: (date_key(g.get("game_date")), str(g.get("game_id") or "")))
    return games


def _canon_games(games: Iterable[dict]) -> list[dict]:
    out = []
    for g in games:
        home = g.get("home_team_id") or g.get("home_team") or g.get("home")
        away = g.get("away_team_id") or g.get("away_team") or g.get("away")
        if not home or not away:
            continue
        row = dict(g)
        row["home_team_id"] = _fold(home) or str(home)
        row["away_team_id"] = _fold(away) or str(away)
        row["home_team"] = row["home_team_id"]
        row["away_team"] = row["away_team_id"]
        out.append(row)
    return out


def _source_mtime() -> float:
    latest = 0.0
    for path in _game_db_paths():
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def _walked() -> tuple[PLXGState, dict, dict]:
    """One chronological predict-then-update pass. Shared by picks and results."""
    global _walk_cache
    mtime = _source_mtime()
    with _state_lock:
        if _walk_cache and _walk_cache[0] == mtime:
            return _walk_cache[1], _walk_cache[2], _walk_cache[3]
        games = _canon_games(_load_scored_games())
        cfg = locked_config()
        state = PLXGState()
        by_match = {}
        by_id = {}
        for g in games:
            home = g.get("home_team_id")
            away = g.get("away_team_id")
            if not home or not away:
                continue
            pred = state.predict(home, away, league=str(g.get("league") or ""), cfg=cfg)
            dt = date_key(g.get("game_date") or g.get("date"))
            by_match[(home, away, dt)] = pred
            gid = str(g.get("game_id") or "")
            if gid:
                by_id[gid] = pred
            state.update(g)
        _walk_cache = (mtime, state, by_match, by_id)
        return state, by_match, by_id


def fitted_state(asof: str) -> PLXGState:
    """Current-form state after all scored games with date < asof."""
    cut = (asof or "")[:10] or _date.today().isoformat()
    state, _by_match, _by_id = _walked()
    # Full walk already ended at the latest scored date. For upcoming asof
    # at/after that, reuse it. Historical asof uses the per-match snapshot.
    if not cut:
        return state
    return state


def predict_matchup(
    home: str,
    away: str,
    *,
    league: str = "",
    asof: str | None = None,
    game_id: str | None = None,
) -> PLXGPrediction | None:
    ht = _fold(home)
    at = _fold(away)
    if not ht or not at:
        return None
    cut = (asof or "")[:10]
    pred = None
    try:
        state, by_match, by_id = _walked()
        if game_id and str(game_id) in by_id:
            pred = by_id[str(game_id)]
        if pred is None and cut:
            pred = by_match.get((ht, at, cut))
        if pred is None:
            pred = state.predict(ht, at, league=str(league or ""), cfg=locked_config())
    except Exception as exc:
        logger.debug("[soccer-plxg] predict failed %s vs %s: %s", home, away, exc)
        pred = None
    if pred is None:
        return league_avg_prediction(league)
    # Unknown / cold-start clubs still get the engine's league prior (~1.3–1.4).
    # Never drop the number — owner forbids a silent dash.
    if pred.n_home < MIN_TEAM_GAMES or pred.n_away < MIN_TEAM_GAMES:
        if pred.method != LEAGUE_AVG_METHOD:
            pred.method = LEAGUE_AVG_METHOD
    return pred


def soccer_plxg_missing_reason(*, home: str = "", away: str = "") -> str:
    if not home or not away:
        return XG_MISSING_TEAMS
    return XG_MISSING


def apply_plxg_to_card(pred: dict, xg: PLXGPrediction | None) -> None:
    """Stamp soccer-only xG fields. Never writes ``h2h_last10_*`` or ``our_spread``."""
    if not isinstance(pred, dict):
        return
    if not xg:
        xg = league_avg_prediction(str(pred.get("league") or ""))
        ht = str(pred.get("home_team_id") or pred.get("home") or "")
        at = str(pred.get("away_team_id") or pred.get("away") or "")
        if not ht or not at:
            pred.setdefault("soccer_pl_expected_home", None)
            pred.setdefault("soccer_pl_expected_away", None)
            pred.setdefault("soccer_pl_expected_total", None)
            pred.setdefault("soccer_pl_expected_face", None)
            pred["soccer_pl_expected_missing_reason"] = XG_MISSING_TEAMS
            return
    pred["soccer_pl_expected_home"] = round(xg.home, 3)
    pred["soccer_pl_expected_away"] = round(xg.away, 3)
    pred["soccer_pl_expected_total"] = round(xg.total, 3)
    pred["soccer_pl_expected_face"] = format_plxg_face(xg)
    pred["soccer_pl_expected_p_over_15"] = round(xg.p_over_15, 4)
    pred["soccer_pl_expected_p_over_25"] = round(xg.p_over_25, 4)
    pred["soccer_pl_expected_p_over_35"] = round(xg.p_over_35, 4)
    pred["soccer_pl_expected_n_home"] = xg.n_home
    pred["soccer_pl_expected_n_away"] = xg.n_away
    pred["soccer_pl_expected_method"] = xg.method
    pred["soccer_pl_expected_missing_reason"] = None
    # Soccer totals input = PL-xG. Leave Efficiency spread (our_spread) alone —
    # never mint PK (0) from a missing / equal xG prior.
    pred["our_total"] = round(xg.total, 3)
    pred["disp_pl_total"] = round(xg.total * 2.0) / 2.0


def fill_soccer_plxg_fields(predictions: list | None) -> None:
    """Attach PL-xG to soccer pick/result dicts only."""
    if not predictions:
        return
    for pred in predictions:
        if not isinstance(pred, dict):
            continue
        ht = pred.get("home_team_id") or pred.get("home_team") or pred.get("home")
        at = pred.get("away_team_id") or pred.get("away_team") or pred.get("away")
        if not ht or not at:
            apply_plxg_to_card(pred, None)
            continue
        asof = None
        if pred.get("home_score") is not None:
            asof = (pred.get("date") or pred.get("game_date") or "")[:10] or None
        if not asof:
            asof = (pred.get("date") or pred.get("game_date") or "")[:10] or None
        league = pred.get("league") or ""
        apply_plxg_to_card(
            pred,
            predict_matchup(
                str(ht),
                str(at),
                league=str(league),
                asof=asof,
                game_id=str(pred.get("game_id") or "") or None,
            ),
        )


def fill_soccer_plxg_from_daily_results(daily_results: dict | None) -> None:
    if not daily_results:
        return
    games: list[dict] = []
    for dd in daily_results.values():
        games.extend(dd.get("games") or [])
    fill_soccer_plxg_fields(games)


def _esc_attr(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )


def _info_btn(tip: str) -> str:
    text = " ".join((tip or "").split())
    esc = html_lib.escape(text, quote=True)
    return (
        f'<button type="button" class="pl-info-btn" data-tip="{esc}" '
        f'data-pl-info-tip="{esc}" title="{esc}" aria-label="{esc}" '
        f'aria-expanded="false" aria-haspopup="true">ⓘ</button>'
    )


def _chip_html(face: str, *, missing_reason: str = "") -> str:
    extra = (" " + _info_btn(missing_reason)) if missing_reason else ""
    return (
        '<div class="sf-item">'
        '<span class="sf-label">PL Expected Goals</span> '
        f'<span class="sf-val">{html_lib.escape(face)}</span>'
        f"{extra}"
        "</div>"
    )


def strip_soccer_h2h_labels(html: str) -> str:
    """Soccer HTML must never contain H2H Last 10 / H2H L10.

    Heading/label text only. Never rewrite ``data-plxg`` or other attributes —
    a global ``PLxG`` substring replace used to turn ``data-plxg="…"`` into
    ``data-Predicted Score="…"`` and drop following book attributes.
    """
    if not html:
        return html
    html = _H2H_TH_RE.sub(r"\1PL-xG\2", html)
    html = _H2H_LABEL_RE.sub("PL Expected Goals", html)
    html = re.sub(
        r"(<th[^>]*>)\s*H2H\s*(?:Last\s*10|L10)\s*(</th>)",
        r"\1PL-xG\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>)\s*'
        r"H2H\s*(?:Last\s*10|L10)\s*(</span>)",
        r"\1PL Expected Goals\2",
        html,
        flags=re.I,
    )
    # Inline chart JS still had `H2H L10` in head= strings after chip relabel.
    html = re.sub(r"H2H\s*Last\s*10", "PL Expected Goals", html, flags=re.I)
    html = re.sub(r"H2H\s*L10", "PL-xG", html, flags=re.I)
    return html


def enrich_soccer_plxg_html(html: str) -> str:
    """Replace soccer H2H Last 10 chips with PL Expected Goals. Soccer HTML only."""
    if not html:
        return html
    html = strip_soccer_h2h_labels(html)
    if "data-pick-card" not in html:
        return html

    cache: dict[tuple[str, str, str, str], PLXGPrediction | None] = {}

    def _lookup(
        home: str, away: str, asof: str | None, league: str = ""
    ) -> PLXGPrediction | None:
        key = (home, away, asof or "", league or "")
        if key in cache:
            return cache[key]
        pred = (
            predict_matchup(home, away, league=league, asof=asof)
            if home and away
            else None
        )
        cache[key] = pred
        return pred

    def _set_attr(tag: str, name: str, value: str) -> str:
        esc = _esc_attr(value)
        if re.search(rf'\b{name}="[^"]*"', tag, flags=re.I):
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{esc}">'

    def _drop_attr(tag: str, name: str) -> str:
        return re.sub(rf'\s*\b{name}="[^"]*"', "", tag, count=1, flags=re.I)

    chip_re = re.compile(
        r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*'
        r'(?:H2H\s*Last\s*10|PL\s*Expected\s*Goals)\s*</span>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]*?)\s*</span>'
        r'(?:\s*<button\b[^>]*\bpl-info-btn\b[^>]*>\s*ⓘ\s*</button>)?'
        r'\s*</div>',
        flags=re.I,
    )

    def _ensure_chip(rest: str, face: str, *, missing_reason: str = "") -> str:
        display = face if face and face not in ("—", "-", "–", "N/A") else format_plxg_face(
            league_avg_prediction()
        )
        replacement = _chip_html(display, missing_reason=missing_reason)
        m = chip_re.search(rest)
        if m:
            return rest[: m.start()] + replacement + rest[m.end() :]
        foot_m = re.search(
            r'(<div\b[^>]*\bclass="[^"]*\bodds-extras-footer\b[^"]*"[^>]*>)',
            rest,
            flags=re.I,
        )
        if foot_m:
            return rest[: foot_m.end()] + "\n        " + replacement + rest[foot_m.end() :]
        return rest

    def _names(open_tag: str) -> tuple[str, str]:
        def _attr(*names: str) -> str:
            for name in names:
                m = re.search(rf'\b{name}="([^"]*)"', open_tag, flags=re.I)
                if m:
                    return html_lib.unescape((m.group(1) or "").strip())
            return ""

        return _attr("data-home-full", "data-home"), _attr("data-away-full", "data-away")

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        home, away = _names(open_tag)
        league = ""
        lm = re.search(r'\bdata-league="([^"]*)"', open_tag, flags=re.I)
        if lm:
            league = html_lib.unescape((lm.group(1) or "").strip())
        before = ""
        dm = re.search(r'\bdata-date="([^"]*)"', open_tag, flags=re.I)
        if dm:
            before = (html_lib.unescape((dm.group(1) or "").strip()) or "")[:10]
        is_final = bool(re.search(r'\bdata-time="FINAL"', open_tag, flags=re.I))
        asof = before if (is_final and before) else (before or None)
        xg = _lookup(home, away, asof, league=league)
        if xg is None:
            xg = league_avg_prediction()
        face = format_plxg_face(xg)
        open2 = _set_attr(open_tag, "data-plxg", face)
        open2 = _drop_attr(open2, "data-h2h")
        open2 = _drop_attr(open2, "data-h2h-reason")
        open2 = _drop_attr(open2, "data-plxg-reason")
        rest2 = _ensure_chip(rest, face, missing_reason="")
        return open2 + rest2

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return strip_soccer_h2h_labels(html)
    return strip_soccer_h2h_labels(parts[0] + "".join(_patch_stack(p) for p in parts[1:]))
