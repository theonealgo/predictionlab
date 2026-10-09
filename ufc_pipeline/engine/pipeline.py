"""Sync ESPN + Odds into sqlite and write UFC predictions."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("UFC_SANDBOX_DB") or (ROOT / "database" / "ufc_sandbox.db"))
SCHEMA_PATH = ROOT / "database" / "schema.sql"

import importlib.util


def _load(name: str, rel: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_fetch = _load("ufc_fetch_mod", "engine/fetch.py")
_predict = _load("ufc_predict_mod", "engine/predict.py")

fetch_espn_history = _fetch.fetch_espn_history
fetch_espn_upcoming = _fetch.fetch_espn_upcoming
fetch_odds_events = _fetch.fetch_odds_events
lookup_odds = _fetch.lookup_odds
train_elo = _predict.train_elo
predict_fight = _predict.predict_fight
american_from_prob = _predict.american_from_prob


def connect() -> sqlite3.Connection:
    global DB_PATH
    try:
        from shared.db import ensure_writable_sqlite

        DB_PATH = ensure_writable_sqlite(DB_PATH)
    except Exception:
        pass
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> Path:
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    return DB_PATH


def sync_fights(*, refresh_cache: bool = False) -> dict[str, Any]:
    init_db()
    hist = fetch_espn_history(use_cache=not refresh_cache)
    upcoming = fetch_espn_upcoming(use_cache=not refresh_cache)
    by_id: dict[str, dict[str, Any]] = {}
    for f in hist + upcoming:
        by_id[f["fight_id"]] = f
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for f in by_id.values():
            for name, fid, rec in (
                (f["home_fighter"], f.get("home_id"), f.get("home_record")),
                (f["away_fighter"], f.get("away_id"), f.get("away_record")),
            ):
                if not name:
                    continue
                row = conn.execute(
                    "SELECT fighter_id FROM ufc_fighters WHERE name=?", (name,)
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE ufc_fighters SET record_summary=?, updated_at=? WHERE name=?",
                        (rec or None, now, name),
                    )
                else:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO ufc_fighters (fighter_id, name, elo, record_summary, updated_at)
                        VALUES (?, ?, 1500, ?, ?)
                        """,
                        (fid or name.lower().replace(" ", "-"), name, rec or None, now),
                    )
            conn.execute(
                """
                INSERT INTO ufc_fights (
                  fight_id, event_id, event_name, fight_date, home_fighter, away_fighter,
                  home_id, away_id, winner, status, home_record, away_record, source, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fight_id) DO UPDATE SET
                  winner=excluded.winner,
                  status=excluded.status,
                  home_record=excluded.home_record,
                  away_record=excluded.away_record,
                  event_name=excluded.event_name,
                  fight_date=excluded.fight_date,
                  updated_at=excluded.updated_at
                """,
                (
                    f["fight_id"],
                    f.get("event_id"),
                    f.get("event_name"),
                    f["fight_date"],
                    f["home_fighter"],
                    f["away_fighter"],
                    f.get("home_id"),
                    f.get("away_id"),
                    f.get("winner"),
                    f.get("status"),
                    f.get("home_record"),
                    f.get("away_record"),
                    f.get("source"),
                    now,
                ),
            )
        conn.commit()
    return {
        "history": len(hist),
        "upcoming": len(upcoming),
        "unique": len(by_id),
        "db": str(DB_PATH),
    }


def ensure_predictions(*, refresh: bool = False) -> dict[str, Any]:
    meta = sync_fights(refresh_cache=refresh)
    with connect() as conn:
        completed = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM ufc_fights
                WHERE status='complete' AND winner IS NOT NULL AND winner != ''
                ORDER BY fight_date ASC
                """
            ).fetchall()
        ]
        upcoming = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM ufc_fights
                WHERE status IN ('scheduled','live')
                ORDER BY fight_date ASC
                """
            ).fetchall()
        ]
    elo = train_elo(completed)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for name, rating in elo.ratings.items():
            conn.execute(
                "UPDATE ufc_fighters SET elo=?, updated_at=? WHERE name=?",
                (rating, now, name),
            )
        conn.commit()

    odds_events = fetch_odds_events(use_cache=not refresh)
    written = 0
    updated = 0
    with_books = 0
    sources: dict[str, int] = {}
    with connect() as conn:
        if refresh:
            # Upcoming slate only — wipe locked stub rows so books can land.
            ids = [f["fight_id"] for f in upcoming]
            if ids:
                conn.executemany(
                    "DELETE FROM ufc_predictions WHERE fight_id=?",
                    [(i,) for i in ids],
                )
                conn.commit()

        for f in upcoming:
            home = f["home_fighter"]
            away = f["away_fighter"]
            odds = lookup_odds(home, away, odds_events)
            existing = conn.execute(
                """
                SELECT pred_id, home_ml, books_count, prob_source
                FROM ufc_predictions
                WHERE fight_id=?
                ORDER BY created_at ASC LIMIT 1
                """,
                (f["fight_id"],),
            ).fetchone()

            # Keep a locked row only when it already has books, or no books exist yet.
            if existing and not refresh:
                has_books = existing["home_ml"] is not None and int(existing["books_count"] or 0) > 0
                if has_books or not odds:
                    if has_books:
                        with_books += 1
                    src = existing["prob_source"] or "elo"
                    sources[src] = sources.get(src, 0) + 1
                    continue

            pred = predict_fight(
                home,
                away,
                elo=elo,
                home_record=f.get("home_record"),
                away_record=f.get("away_record"),
                odds=odds,
            )
            if odds:
                with_books += 1
            sources[pred["prob_source"]] = sources.get(pred["prob_source"], 0) + 1

            if existing and not refresh:
                conn.execute(
                    """
                    UPDATE ufc_predictions SET
                      created_at=?, model_name=?,
                      home_win_prob=?, away_win_prob=?, pick_ml=?, confidence=?,
                      prob_source=?, home_ml=?, away_ml=?, books_count=?, explanation=?
                    WHERE pred_id=?
                    """,
                    (
                        now,
                        "UFC Isolation",
                        pred["home_win_prob"],
                        pred["away_win_prob"],
                        pred["pick_ml"],
                        pred["confidence"],
                        pred["prob_source"],
                        pred.get("home_ml"),
                        pred.get("away_ml"),
                        pred.get("books_count") or 0,
                        pred.get("explanation"),
                        existing["pred_id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO ufc_predictions (
                      pred_id, fight_id, created_at, model_name,
                      home_win_prob, away_win_prob, pick_ml, confidence,
                      prob_source, home_ml, away_ml, books_count, explanation
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid4()),
                        f["fight_id"],
                        now,
                        "UFC Isolation",
                        pred["home_win_prob"],
                        pred["away_win_prob"],
                        pred["pick_ml"],
                        pred["confidence"],
                        pred["prob_source"],
                        pred.get("home_ml"),
                        pred.get("away_ml"),
                        pred.get("books_count") or 0,
                        pred.get("explanation"),
                    ),
                )
                written += 1
        conn.commit()
    meta.update(
        {
            "completed_for_elo": len(completed),
            "elo_fighters": len(elo.ratings),
            "predictions": written,
            "updated": updated,
            "with_books": with_books,
            "odds_events": len(odds_events),
            "sources": sources,
        }
    )
    print(
        f"[ufc.pipeline] predictions={written} updated={updated} "
        f"books={with_books} sources={sources}",
        flush=True,
    )
    return meta


def list_pick_cards() -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        ensure_predictions(refresh=False)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT f.*, p.home_win_prob, p.away_win_prob, p.pick_ml, p.confidence,
                   p.prob_source, p.home_ml, p.away_ml, p.books_count, p.explanation,
                   fh.elo AS home_elo, fa.elo AS away_elo
            FROM ufc_fights f
            JOIN ufc_predictions p ON p.fight_id = f.fight_id
            LEFT JOIN ufc_fighters fh ON fh.name = f.home_fighter
            LEFT JOIN ufc_fighters fa ON fa.name = f.away_fighter
            WHERE f.status IN ('scheduled','live')
            ORDER BY f.fight_date ASC, f.event_name ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_graded_results(limit: int = 40) -> list[dict[str, Any]]:
    """Completed fights graded walk-forward (Elo trained only on prior fights).

    Locked ufc_predictions rows are rare for history (pipeline writes upcoming only),
    so retrospective picks use pre-fight Elo — never the post-fight rating.
    """
    with connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM ufc_fights
                WHERE status='complete' AND winner IS NOT NULL AND winner != ''
                ORDER BY fight_date ASC, fight_id ASC
                """
            ).fetchall()
        ]

    elo = _predict.EloSystem()
    graded: list[dict[str, Any]] = []
    for f in rows:
        home = f.get("home_fighter") or ""
        away = f.get("away_fighter") or ""
        winner = f.get("winner") or ""
        if not home or not away or not winner:
            continue
        pred = predict_fight(
            home,
            away,
            elo=elo,
            home_record=f.get("home_record"),
            away_record=f.get("away_record"),
            odds=None,
        )
        pick = pred.get("pick_ml") or ""
        d = {
            **f,
            "pick_ml": pick,
            "home_win_prob": pred.get("home_win_prob"),
            "away_win_prob": pred.get("away_win_prob"),
            "confidence": pred.get("confidence"),
            "grade": "WIN" if pick and str(pick) == str(winner) else ("LOSS" if pick else None),
        }
        graded.append(d)
        # Update Elo only after the pick is locked for this fight
        if winner == home:
            elo.update(home, away)
        elif winner == away:
            elo.update(away, home)

    graded.reverse()  # newest first
    return graded[: max(1, int(limit))]


def window_tally_records(cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Last Night / Last 7 / Season W-L from graded result cards (ET calendar days)."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    cards = cards if cards is not None else list_graded_results(limit=500)
    et = ZoneInfo("America/New_York")

    def _day(c: dict[str, Any]):
        raw = str(c.get("fight_date") or "")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(et).date()
        except ValueError:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").date()
            except ValueError:
                return None

    dated = [(c, _day(c)) for c in cards if c.get("grade") in ("WIN", "LOSS")]
    dated = [(c, d) for c, d in dated if d is not None]
    last_night = max((d for _, d in dated), default=None)

    def _wl(pred) -> tuple[int, int]:
        w = l = 0
        for c, d in dated:
            if not pred(d):
                continue
            if c.get("grade") == "WIN":
                w += 1
            else:
                l += 1
        return w, l

    ln_w, ln_l = _wl(lambda d: d == last_night) if last_night else (0, 0)
    if last_night:
        lo = last_night - timedelta(days=6)
        l7_w, l7_l = _wl(lambda d: lo <= d <= last_night)
    else:
        l7_w = l7_l = 0
    # Sandbox "Season" = all graded history in the local slate
    season_w, season_l = _wl(lambda _d: True)

    return {
        "labels": ["Last Night", "Last 7", "Season"],
        "values": [ln_w, l7_w, season_w],
        "records": {
            "last_night": {
                "w": ln_w,
                "l": ln_l,
                "date": last_night.isoformat() if last_night else None,
            },
            "last_7": {"w": l7_w, "l": l7_l},
            "season": {"w": season_w, "l": season_l},
        },
    }
