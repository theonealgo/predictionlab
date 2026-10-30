"""All Sports Picks board: every sport's open picks, each pick type's record
over the ladder's window (the same record the ladder uses), and graded results.

Built from stored data only (no network), on a background thread; page
requests read the last built board.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import markets, money, sources, store
from .engine import ET, Config, LadderEngine

logger = logging.getLogger("ladder_picks")

REBUILD_SECONDS = 600
LOOKAHEAD_DAYS = 7
# Sports whose picks live only on their own picks page (no main-database rows).
PAGE_SPORTS = ("CFL",)
_CACHE_FILE = Path(__file__).resolve().parent.parent / ".cache" / "all_sports_picks.json"
_SPORT_ORDER = ("NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "NCAAW", "WNBA", "CFL",
                "SOCCER", "TENNIS", "UFC")
_SPORT_NAME = {"SOCCER": "Soccer", "TENNIS": "Tennis"}
_FINAL = ("final", "complete", "completed", "closed", "post")

_state: dict = {"data": None, "at": 0.0, "building": False}
_lock = threading.Lock()


def sport_name(sport: str) -> str:
    return _SPORT_NAME.get(sport, sport)


def _pct(p) -> str:
    return "—" if p is None else f"{float(p) * 100:.1f}%"


def _rec(s: dict | None) -> dict | None:
    if not s:
        return None
    return {"wins": s["wins"], "n": s["n"], "rate": round(s["rate"], 4),
            "text": f"{s['wins']}-{s['n'] - s['wins']} ({_pct(s['rate'])})",
            "qualifies": bool(s["qualifies"])}


def _type_record(stats: dict, sport: str, o: dict) -> dict | None:
    """The record the ladder would use for this pick: its best qualifying gap
    bucket, else the widest bucket the pick falls in."""
    best = widest = None
    for edge in markets.gap_edges(sport, o["market"]):
        if o["gap"] < edge - 1e-9:
            continue
        t = stats.get((sport, o["market"], o["model"], o["side_class"], edge))
        if not t:
            continue
        if widest is None:
            widest = t
        if t["qualifies"] and (best is None or (t["score"], t["n"], t["roi"]) > (best["score"], best["n"], best["roi"])):
            best = t
    return best or widest


def _latest_books(conn, keys: list[tuple[str, str]]) -> dict:
    out: dict = {}
    if not keys or "game_id" not in sources._table_cols(conn, "betting_lines"):
        return out
    ids = sorted({k[1] for k in keys})
    marks = ",".join("?" for _ in ids)
    for b in conn.execute(
        f"""SELECT sport, game_id, spread, total, home_moneyline, away_moneyline
            FROM betting_lines WHERE game_id IN ({marks}) ORDER BY id""",
        ids,
    ):
        out[(b["sport"], b["game_id"])] = b
    return out


def _statuses(conn, keys: list[tuple[str, str]]) -> dict:
    ids = sorted({k[1] for k in keys})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {(r["sport"], r["game_id"]): str(r["status"] or "").lower()
            for r in conn.execute(
                f"SELECT sport, game_id, status FROM games WHERE game_id IN ({marks})", ids)}


def _page_games(sport: str, today: date, days: int) -> list[dict]:
    """Upcoming cards from a sport's saved picks page, for a sport whose picks are
    not in the main database. Moneyline and PL spread as the page shows them."""
    path = _CACHE_FILE.parent / f"served_picks_{sport}.html"
    try:
        page = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    last = (today + timedelta(days=days)).isoformat()
    out = []
    for m in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", page):
        tag = m.group(0)

        def attr(name: str) -> str:
            a = re.search(rf'\b{name}="([^"]*)"', tag)
            return html.unescape(a.group(1)).strip() if a else ""

        when = attr("data-time")
        if attr("data-result") or when.upper().startswith("FINAL") or not re.match(r"\d{4}-\d{2}-\d{2}", when):
            continue
        day = when[:10]
        if not today.isoformat() <= day <= last:
            continue
        picks = []
        if attr("data-pick") and attr("data-conf"):
            picks.append({"market": "Moneyline", "model": "Consensus", "label": f"{attr('data-pick')} ML",
                          "type": "", "odds": "", "win_pct": f"{float(attr('data-conf')):.1f}%",
                          "edge": "", "record": None, "ladder": False, "score": 0.0, "n": 0})
        if attr("data-pl-spread"):
            picks.append({"market": "Spread", "model": "PL", "label": attr("data-pl-spread"),
                          "type": "", "odds": "", "win_pct": "", "edge": "", "record": None,
                          "ladder": False, "score": 0.0, "n": 0})
        if picks:
            out.append({"sport": sport, "sport_name": sport_name(sport), "date": day, "league": "",
                        "event": f"{attr('data-away')} @ {attr('data-home')}", "picks": picks})
    return out


def _open_picks(src: sources.PredictionSource, stats: dict, cfg: Config, today: date) -> list[dict]:
    """Today's and tomorrow's picks; a sport with none in those two days shows
    its next slate within the week."""
    cands = []
    for i in range(LOOKAHEAD_DAYS):
        cands.extend(src.candidates((today + timedelta(days=i)).isoformat()))
    soon = (today + timedelta(days=1)).isoformat()
    first: dict = {}
    for c in cands:
        first[c["sport"]] = min(first.get(c["sport"], c["game_date"]), c["game_date"])
    cands = [c for c in cands if c["game_date"] <= soon or c["game_date"] == first[c["sport"]]]
    keys = [(c["sport"], c["game_id"]) for c in cands]
    with closing(sources._ro(src.path)) as conn:
        books = _latest_books(conn, keys)
        status = _statuses(conn, keys)
    games, seen = [], set()
    for c in cands:
        key = (c["sport"], c["game_id"])
        if key in seen:
            continue
        seen.add(key)
        st = status.get(key, "")
        if c["sport"] in sources._OWN_DB:
            ev = sources._own_db_event(c["sport"], c["game_id"]) or {}
            st = str(ev.get("status") or st).lower()
        if st in _FINAL or st in ("in", "live", "in progress"):
            continue
        b = books.get(key)
        book = {}
        if b is not None:
            book = {"home_ml": b["home_moneyline"], "away_ml": b["away_moneyline"],
                    "spread": b["spread"], "total": b["total"]}
        elif c["sport"] == "UFC":
            fm = sources._ufc_model([sources.event_id(c["game_id"])]).get(sources.event_id(c["game_id"]) or "")
            if fm:
                book = {"home_ml": fm["home_ml"], "away_ml": fm["away_ml"]}
        picks = []
        for o in markets.options(c["sport"], c.get("model") or {}, book):
            rec = _type_record(stats, c["sport"], o)
            price = money.parse_american(o["price"])
            price_ok = price is None or (cfg.min_decimal - 1e-9 <= float(money.american_to_decimal(price))
                                         <= cfg.max_decimal + 1e-9)
            picks.append({
                "market": markets.market_name(c["sport"], o["market"]),
                "model": o["model"],
                "label": markets.label(o, c["home_team"], c["away_team"]),
                "type": markets.type_label(c["sport"], o),
                "odds": money.fmt_american(price) if price is not None else "",
                "win_pct": _pct(o.get("model_prob")) if o.get("model_prob") is not None else "",
                "edge": "" if o["market"] == "moneyline" else f"{o['gap']:g}",
                "record": _rec(rec),
                "ladder": bool(rec and rec["qualifies"] and price_ok),
                "score": rec["score"] if rec else 0.0,
                "n": rec["n"] if rec else 0,
            })
        if not picks:
            continue
        picks.sort(key=lambda p: (not p["ladder"], -p["score"], -p["n"]))
        games.append({
            "sport": c["sport"], "sport_name": sport_name(c["sport"]), "date": c["game_date"],
            "league": c.get("league") or "", "event": f"{c['away_team']} @ {c['home_team']}",
            "picks": picks,
        })
    have = {g["sport"] for g in games}
    for sport in PAGE_SPORTS:
        if sport in have:
            continue
        extra = _page_games(sport, today, LOOKAHEAD_DAYS - 1)
        if extra:
            soon_first = min(g["date"] for g in extra)
            games.extend(g for g in extra if g["date"] <= soon or g["date"] == soon_first)
    order = {s: i for i, s in enumerate(_SPORT_ORDER)}
    games.sort(key=lambda g: (g["date"], order.get(g["sport"], 99), g["league"], g["event"]))
    return games


def _results(rows: list[dict], day: str) -> list[dict]:
    by_game: dict = {}
    for r in rows:
        if r["date"] != day or "game_id" not in r:
            continue
        g = by_game.setdefault((r["sport"], r["game_id"]), {
            "sport": r["sport"], "sport_name": sport_name(r["sport"]),
            "event": f"{r['away_team']} @ {r['home_team']}",
            "score": (f"{r['away_score']}-{r['home_score']}" if r["sport"] not in sources._OWN_DB
                      else (r["home_team"] if r["home_score"] > r["away_score"] else r["away_team"]) + " won"),
            "picks": [],
        })
        g["picks"].append({
            "market": markets.market_name(r["sport"], r["market"]), "model": r["model"],
            "label": r["label"], "win": r["win"],
        })
    order = {s: i for i, s in enumerate(_SPORT_ORDER)}
    return sorted(by_game.values(), key=lambda g: (order.get(g["sport"], 99), g["event"]))


def _summary(rows: list[dict], since: str, until: str) -> list[dict]:
    """W-L per sport and market for dates since <= d <= until."""
    tally: dict = {}
    for r in rows:
        if not since <= r["date"] <= until:
            continue
        t = tally.setdefault((r["sport"], r["market"]), [0, 0])
        t[0 if r["win"] else 1] += 1
    order = {s: i for i, s in enumerate(_SPORT_ORDER)}
    mk = {"moneyline": 0, "spread": 1, "total": 2}
    out = []
    for (sport, market), (w, l) in sorted(tally.items(), key=lambda kv: (order.get(kv[0][0], 99), mk.get(kv[0][1], 9))):
        out.append({"sport": sport_name(sport), "market": markets.market_name(sport, market),
                    "record": f"{w}-{l}", "rate": _pct(w / (w + l)) if w + l else "—"})
    return out


def _type_table(stats: dict) -> list[dict]:
    rows = []
    for (sport, market, model, side, edge), s in stats.items():
        if s["n"] < 5:
            continue
        o = {"market": market, "model": model, "side_class": side}
        gap = ("our win chance " if market == "moneyline" else "edge ") + (
            f"{edge * 100:.0f}%+" if market == "moneyline" else f"{edge:g}+")
        rows.append({"type": markets.type_label(sport, o), "gap": gap, "record": _rec(s),
                     "rate": s["rate"], "n": s["n"], "qualifies": bool(s["qualifies"])})
    rows.sort(key=lambda r: (not r["qualifies"], -r["rate"], -r["n"]))
    return rows[:60]


def _ladder_today(today: str) -> dict | None:
    try:
        conn = store.connect()
        try:
            row = conn.execute(
                "SELECT * FROM ladder_days WHERE pick_date = ? ORDER BY id DESC LIMIT 1", (today,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row or row["result"] == "no_pick":
        return None
    return {"sport": sport_name(row["sport"] or ""), "event": row["event_label"] or "",
            "pick": row["pick_label"] or "", "type": row["pick_type"] or "",
            "odds": money.fmt_american(row["american_odds"]),
            "record": (f"{_pct(row['hist_hit_rate'])} of {row['hist_n']}" if row["hist_n"] else ""),
            "result": row["result"]}


def _copy_text(data: dict) -> str:
    lines = [f"PredictionLab — All Sports Picks ({data['today_label']})", ""]
    cur = None
    for g in data["games"]:
        if g["date"] != cur:
            cur = g["date"]
            lines.append(f"== {g['date_label']} ==")
        lines.append(f"{g['sport_name']}{' · ' + g['league'] if g['league'] and g['sport'] == 'SOCCER' else ''} — {g['event']}")
        for p in g["picks"]:
            bits = [f"  {p['market']} ({p['model']}): {p['label']}"]
            if p["odds"]:
                bits.append(p["odds"])
            if p["win_pct"]:
                bits.append(p["win_pct"])
            if p["record"]:
                bits.append(f"{data['rules']['days']}-day {p['record']['text']}")
            if p["ladder"]:
                bits.append("ladder qualifier")
            lines.append(" · ".join(bits))
    if data["results"]:
        lines += ["", f"== Results — {data['results_label']} =="]
        for g in data["results"]:
            lines.append(f"{g['sport_name']} — {g['event']} ({g['score']})")
            for p in g["picks"]:
                lines.append(f"  {p['market']} ({p['model']}): {p['label']} — {'WIN' if p['win'] else 'LOSS'}")
    if data["summary_last7"]:
        lines += ["", "== Last 7 days =="]
        for r in data["summary_last7"]:
            lines.append(f"{r['sport']} {r['market']}: {r['record']} ({r['rate']})")
    return "\n".join(lines)


def _label(d: str) -> str:
    try:
        return date.fromisoformat(d).strftime("%a %b %-d")
    except ValueError:
        return d


def build(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ET).date()
    cfg = Config()
    src = sources.PredictionSource()
    conn = store.connect()
    try:
        eng = LadderEngine(conn=conn, predictions=src, config=cfg)
        stats = eng.type_stats(today)
    finally:
        conn.close()
    games = _open_picks(src, stats, cfg, today)
    for g in games:
        g["date_label"] = _label(g["date"])
    since = (today - timedelta(days=7)).isoformat()
    rows = src.graded_history(since, (today + timedelta(days=1)).isoformat())
    graded_days = sorted({r["date"] for r in rows if "game_id" in r and r["date"] <= today.isoformat()})
    yday = (today - timedelta(days=1)).isoformat()
    res_day = yday if yday in graded_days else (graded_days[-1] if graded_days else yday)
    data = {
        "built_at": now.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET"),
        "today": today.isoformat(),
        "today_label": today.strftime("%a %b %-d, %Y"),
        "games": games,
        "ladder_today": _ladder_today(today.isoformat()),
        "types": _type_table(stats),
        "results": _results(rows, res_day),
        "results_label": _label(res_day),
        "today_results": _results(rows, today.isoformat()) if res_day != today.isoformat() else [],
        "summary_last_night": _summary(rows, res_day, res_day),
        "summary_last7": _summary(rows, (date.fromisoformat(res_day) - timedelta(days=6)).isoformat(), res_day),
        "rules": {"games": cfg.min_type_games, "rate": _pct(cfg.min_type_rate), "days": cfg.history_days},
    }
    data["copy_text"] = _copy_text(data)
    return data


def _build_bg() -> None:
    try:
        data = build()
        with _lock:
            _state.update(data=data, at=time.time())
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": time.time(), "data": data}, default=str))
            tmp.replace(_CACHE_FILE)
        except OSError as exc:
            logger.info("all-sports picks: cache write failed: %s", exc)
    except Exception:
        logger.exception("all-sports picks: build failed")
    finally:
        with _lock:
            _state["building"] = False


def current() -> dict | None:
    """Last built board; starts a background rebuild when it is stale."""
    with _lock:
        data, at, building = _state["data"], _state["at"], _state["building"]
    if data is None:
        try:
            saved = json.loads(_CACHE_FILE.read_text())
            data, at = saved.get("data"), float(saved.get("at") or 0)
            with _lock:
                if _state["data"] is None:
                    _state.update(data=data, at=at)
        except (OSError, ValueError):
            pass
    today = datetime.now(ET).date().isoformat()
    stale = data is None or time.time() - at > REBUILD_SECONDS or (data or {}).get("today") != today
    if stale and not building:
        with _lock:
            if not _state["building"]:
                _state["building"] = True
                threading.Thread(target=_build_bg, name="all-sports-picks", daemon=True).start()
    if data is not None and data.get("today") != today:
        # Yesterday's board while the rebuild runs: never show yesterday's ladder pick as today's.
        data = dict(data, ladder_today=_ladder_today(today))
    return data
