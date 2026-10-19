"""Ladder lifecycle tests with simulated predictions, book lines, start times and results."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from ladder_picks import markets, money, social, sources, store
from ladder_picks.engine import Config, LadderEngine
from ladder_picks.stats import ladder_stats
from ladder_picks.views import page_context

D0 = date(2026, 10, 20)


def _noon_et(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc)  # 12:00 EDT


def _settle_time(d: date) -> datetime:
    nxt = d + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, 15, 30, tzinfo=timezone.utc)  # 11:30 EDT


class World:
    """Fake main database (predictions, games, stored book lines) plus fake live feeds.

    Default game: book has the home team -3; XSharp projects the home team to win
    by 1, so XSharp takes the away team +3 (an underdog spread pick, gap 2).
    """

    def __init__(self, tmp_path):
        self.main = str(tmp_path / "main.db")
        con = sqlite3.connect(self.main)
        con.executescript(
            """
            CREATE TABLE predictions (id INTEGER PRIMARY KEY, sport TEXT, league TEXT, game_id TEXT,
                game_date TEXT, home_team_id TEXT, away_team_id TEXT, win_probability REAL,
                lock_card_json TEXT, locked INTEGER, created_at TEXT, lock_pl_spread REAL,
                lock_xs_spread REAL, lock_pl_total REAL, lock_xs_total REAL);
            CREATE TABLE games (sport TEXT, game_id TEXT, game_date TEXT, status TEXT,
                home_score INTEGER, away_score INTEGER);
            CREATE TABLE betting_lines (sport TEXT, game_id TEXT, game_date TEXT, spread REAL,
                total REAL, home_moneyline INTEGER, away_moneyline INTEGER, fetched_at TEXT);
            """
        )
        con.commit()
        con.close()
        self.odds: dict[str, dict] = {}
        self.events: dict[str, dict] = {}
        self.n = 0

    def _insert(self, d, gid, sport, model, book, final=None):
        con = sqlite3.connect(self.main)
        con.execute(
            "INSERT INTO predictions (sport, league, game_id, game_date, home_team_id, away_team_id, "
            "win_probability, lock_card_json, locked, created_at, lock_pl_spread, lock_xs_spread, "
            "lock_pl_total, lock_xs_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (sport, sport, gid, d.isoformat(), f"Home {gid}", f"Away {gid}", model.get("home_prob", 0.5),
             json.dumps({}), d.isoformat(), model.get("pl_spread"), model.get("xs_spread"),
             model.get("pl_total"), model.get("xs_total")),
        )
        status, hs, aw = ("final", *final) if final else ("scheduled", None, None)
        con.execute("INSERT INTO games VALUES (?, ?, ?, ?, ?, ?)", (sport, gid, d.isoformat(), status, hs, aw))
        if book is not None:
            con.execute(
                "INSERT INTO betting_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sport, gid, d.isoformat(), book.get("spread"), book.get("total"), book.get("home_ml"),
                 book.get("away_ml"), f"{d.isoformat()} 12:00:00"),
            )
        con.commit()
        con.close()

    def seed_history(self, wins, losses, sport="NFL", model=None, book=None, win_score=(20, 21),
                     loss_score=(27, 20), start=None):
        """Past graded games of one pick type, wins and losses spread evenly over time."""
        model = model or {"xs_spread": 1.0}
        book = book or {"spread": -3.0}
        total = wins + losses
        start = start or D0 - timedelta(days=total + 1)
        pattern = [i * wins // total != (i + 1) * wins // total for i in range(total)]
        for i, win in enumerate(pattern):
            self.n += 1
            self._insert(start + timedelta(days=i), f"h{self.n}", sport, model, book,
                         final=win_score if win else loss_score)

    def add_game(self, d, gid, sport="NFL", model=None, book=None, start_hour_utc=23):
        model = model or {"xs_spread": 1.0}
        live = {"spread": -3.0, "spread_price_home": -110, "spread_price_away": -110}
        live.update(book or {})
        self._insert(d, gid, sport, model, None)
        self.odds[gid] = live
        self.events[gid] = {"start": datetime(d.year, d.month, d.day, start_hour_utc, 0, tzinfo=timezone.utc),
                            "state": "pre"}

    def final(self, gid, home, away):
        con = sqlite3.connect(self.main)
        con.execute("UPDATE games SET status = 'final', home_score = ?, away_score = ? WHERE game_id = ?",
                    (home, away, gid))
        con.commit()
        con.close()

    def odds_fn(self, cand):
        return self.odds.get(cand["game_id"])

    def event_fn(self, sport, gid):
        return self.events.get(gid)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.seed_history(33, 17)  # 66% over 50 games: the away +3 underdog pick type qualifies
    return w


@pytest.fixture
def ladder_db(tmp_path):
    return str(tmp_path / "ladder.db")


def make_engine(world, ladder_db, **cfg):
    return LadderEngine(
        conn=store.connect(ladder_db),
        predictions=sources.PredictionSource(world.main),
        odds_fn=world.odds_fn,
        event_fn=world.event_fn,
        config=Config(**cfg),
    )


def day_rows(engine):
    return [dict(r) for r in engine.conn.execute("SELECT * FROM ladder_days ORDER BY ladder_id, day_number")]


def ladder(engine, lid=None):
    if lid is None:
        return dict(engine.conn.execute("SELECT * FROM ladder_runs ORDER BY id DESC LIMIT 1").fetchone())
    return dict(engine.conn.execute("SELECT * FROM ladder_runs WHERE id = ?", (lid,)).fetchone())


AWAY_COVERS = (20, 21)   # home -3 loses outright
HOME_COVERS = (27, 20)
PUSH = (23, 20)          # home wins by exactly 3


# ── Odds math ────────────────────────────────────────────────────────────────
def test_profit_positive_and_negative_american():
    assert money.profit_cents(1000, -150) == 667
    assert money.profit_cents(1000, 120) == 1200
    assert money.profit_cents(1000, -110) == 909
    assert money.profit_cents(1909, -110) == 1735
    assert float(money.american_to_decimal(250)) == pytest.approx(3.5)


def test_parse_american_rejects_bad_values():
    for bad in (None, "", "N/A", "abc", 50, -99, 0, 1.5, True, "-150.5"):
        assert money.parse_american(bad) is None
    assert money.parse_american("+120") == 120
    assert money.parse_american("EVEN") == 100


# ── Pick types and grading ───────────────────────────────────────────────────
def test_options_and_grading():
    opts = markets.options("NHL", {"home_prob": 0.6, "pl_spread": -0.4, "xs_total": 5.2},
                           {"home_ml": -140, "away_ml": 120, "spread": -1.5, "total": 6.5,
                            "spread_price_away": -210, "under_price": -105, "over_price": -115})
    by = {o["market"]: o for o in opts}
    assert by["moneyline"]["side"] == "HOME" and by["moneyline"]["price"] == -140
    sp = by["spread"]
    assert sp["side"] == "AWAY" and sp["line"] == 1.5 and sp["side_class"] == "dog" and sp["price"] == -210
    assert sp["gap"] == pytest.approx(1.9) and sp["bucket"] == 1.5
    tot = by["total"]
    assert tot["side"] == "UNDER" and tot["gap"] == pytest.approx(1.3) and tot["bucket"] == 1.0
    assert markets.label(sp, "Home", "Away") == "Away +1.5"
    assert markets.label(tot, "Home", "Away") == "Under 6.5"
    assert markets.grade("spread", "AWAY", 1.5, 3, 2) == "win"
    assert markets.grade("spread", "AWAY", 1.5, 4, 2) == "loss"
    assert markets.grade("spread", "HOME", -3, 23, 20) == "push"
    assert markets.grade("total", "UNDER", 6.5, 3, 2) == "win"
    assert markets.grade("total", "OVER", 6, 4, 2) == "push"
    assert markets.grade("moneyline", "HOME", None, 2, 2) == "push"


def test_pick_type_needs_games_rate_and_both_halves(tmp_path, ladder_db):
    def qualifies(w):
        eng = make_engine(w, ladder_db)
        s = eng.type_stats(D0).get(("NFL", "spread", "XSharp", "dog", 2.0))
        return bool(s and s["qualifies"])

    (tmp_path / "a").mkdir()
    few = World(tmp_path / "a")
    few.seed_history(30, 9)  # 77% but only 39 games
    assert not qualifies(few)
    (tmp_path / "b").mkdir()
    low = World(tmp_path / "b")
    low.seed_history(28, 22)  # 56%
    assert not qualifies(low)
    (tmp_path / "c").mkdir()
    fade = World(tmp_path / "c")
    fade.seed_history(24, 1, start=D0 - timedelta(days=80))  # strong first half ...
    fade.seed_history(12, 13, start=D0 - timedelta(days=40))  # ... then 48%
    assert not qualifies(fade)
    (tmp_path / "d").mkdir()
    good = World(tmp_path / "d")
    good.seed_history(33, 17)
    assert qualifies(good)


def test_in_game_lines_are_not_used(tmp_path, ladder_db):
    w = World(tmp_path)
    w.seed_history(33, 17)
    con = sqlite3.connect(w.main)
    con.execute("UPDATE betting_lines SET fetched_at = game_date || ' 20:00:00'")
    con.commit()
    con.close()
    assert make_engine(w, ladder_db).qualifying_types(D0) == []


# ── Lifecycle ────────────────────────────────────────────────────────────────
def test_win_then_win_compounds(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    assert eng.run_cycle(_noon_et(D0))["selection"]["action"] == "published"
    d1 = day_rows(eng)[0]
    assert d1["stake_cents"] == 1000 and d1["american_odds"] == -110
    assert d1["market"] == "spread" and d1["pick_side"] == "AWAY" and d1["line"] == 3.0
    assert d1["pick_label"] == "Away g1 +3" and d1["hist_n"] == 50

    world.final("g1", *AWAY_COVERS)
    d2 = D0 + timedelta(days=1)
    world.add_game(d2, "g2", book={"spread_price_away": 120})
    out = eng.run_cycle(_settle_time(D0))
    assert out["settled"][0]["result"] == "win"
    rows = day_rows(eng)
    assert rows[0]["profit_cents"] == 909 and rows[0]["amount_after_cents"] == 1909
    assert out["selection"]["action"] == "published" and rows[1]["stake_cents"] == 1909

    world.final("g2", *AWAY_COVERS)
    eng.run_cycle(_settle_time(d2))
    assert day_rows(eng)[1]["profit_cents"] == money.profit_cents(1909, 120)
    assert ladder(eng)["current_amount_cents"] == 1909 + money.profit_cents(1909, 120)


def test_loss_ends_ladder_and_next_starts_fresh(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    world.final("g1", *HOME_COVERS)
    nxt = D0 + timedelta(days=1)
    world.add_game(nxt, "g2")
    out = eng.run_cycle(_settle_time(D0))
    assert out["settled"][0]["result"] == "loss"
    first = ladder(eng, 1)
    assert first["status"] == "failed" and first["final_day"] == 1 and first["current_amount_cents"] == 0
    assert out["selection"]["action"] == "published"
    second = ladder(eng)
    assert second["id"] == 2 and second["start_amount_cents"] == 1000 and second["start_date"] == nxt.isoformat()


def test_push_and_void_carry_stake(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    world.final("g1", *PUSH)
    d2 = D0 + timedelta(days=1)
    world.add_game(d2, "g2")
    eng.run_cycle(_settle_time(D0))
    rows = day_rows(eng)
    assert rows[0]["result"] == "push" and rows[0]["amount_after_cents"] == 1000
    assert rows[1]["day_number"] == 2 and rows[1]["stake_cents"] == 1000

    world.events["g2"]["state"] = "postponed"
    d3 = d2 + timedelta(days=1)
    world.add_game(d3, "g3")
    eng.run_cycle(_settle_time(d2))
    rows = day_rows(eng)
    assert rows[1]["result"] == "void" and rows[1]["profit_cents"] == 0
    assert rows[2]["day_number"] == 3 and rows[2]["stake_cents"] == 1000


def test_total_pick_settles_on_combined_score(tmp_path, ladder_db):
    w = World(tmp_path)
    w.seed_history(30, 15, sport="NHL", model={"pl_total": 5.4}, book={"total": 6.5},
                   win_score=(3, 2), loss_score=(4, 3))
    w.add_game(D0, "g1", sport="NHL", model={"pl_total": 5.4},
               book={"spread": None, "total": 6.5, "under_price": 105, "over_price": -125})
    eng = make_engine(w, ladder_db)
    eng.run_cycle(_noon_et(D0))
    row = day_rows(eng)[0]
    assert row["pick_label"] == "Under 6.5" and row["american_odds"] == 105
    w.final("g1", 2, 3)
    eng.settle_pending(_settle_time(D0))
    row = day_rows(eng)[0]
    assert row["result"] == "win" and row["profit_cents"] == 1050


def test_unresolved_game_voids_after_timeout(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    world.events["g1"]["state"] = "in"
    eng.settle_pending(_settle_time(D0))
    assert day_rows(eng)[0]["result"] == "pending"
    eng.settle_pending(_settle_time(D0) + timedelta(days=3))
    assert day_rows(eng)[0]["result"] == "void"


def test_no_qualifying_pick(tmp_path, ladder_db):
    w = World(tmp_path)
    w.seed_history(26, 24)  # 52%: nothing qualifies
    w.add_game(D0, "g1")
    eng = make_engine(w, ladder_db)
    out = eng.run_cycle(_noon_et(D0))
    assert out["selection"]["action"] == "no_pick_yet"
    assert out["selection"]["rejected"] == {"no qualifying pick type for this sport": 1}
    late = datetime(D0.year, D0.month, D0.day, 22, 30, tzinfo=timezone.utc)  # 18:30 ET
    assert eng.select_today(late)["action"] == "no_pick"
    assert eng.conn.execute("SELECT COUNT(*) FROM ladder_runs").fetchone()[0] == 0
    assert eng.select_today(late)["action"] == "no_pick_recorded"


def test_short_betting_lines_table(world, ladder_db):
    """Production keeps a short betting_lines table: id, game_id, spread, total."""
    con = sqlite3.connect(world.main)
    con.executescript(
        """
        CREATE TABLE bl (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, spread REAL, total REAL);
        INSERT INTO bl (game_id, spread, total) SELECT game_id, spread, total FROM betting_lines;
        DROP TABLE betting_lines;
        ALTER TABLE bl RENAME TO betting_lines;
        """
    )
    con.commit()
    con.close()
    world.add_game(D0, "g1")
    out = make_engine(world, ladder_db).run_cycle(_noon_et(D0))
    assert out["selection"]["action"] == "published"


def test_predictions_without_lock_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE predictions (id INTEGER PRIMARY KEY, sport TEXT, league TEXT, game_id TEXT, "
                "game_date TEXT, home_team_id TEXT, away_team_id TEXT, win_probability REAL, "
                "lock_card_json TEXT, locked INTEGER, created_at TEXT)")
    con.execute("INSERT INTO predictions VALUES (1, 'NFL', 'NFL', 'g1', ?, 'H', 'A', 0.6, ?, 1, ?)",
                (D0.isoformat(), json.dumps({"xgb_spread": 2.0}), D0.isoformat()))
    con.commit()
    con.close()
    rows = sources.PredictionSource(str(path)).candidates(D0.isoformat())
    assert rows[0]["model"]["xs_spread"] == 2.0 and rows[0]["model"]["pl_spread"] is None


def test_recorded_no_pick_does_not_block_a_later_game(world, ladder_db):
    eng = make_engine(world, ladder_db)
    early_evening = datetime(D0.year, D0.month, D0.day, 22, 0, tzinfo=timezone.utc)  # 18:00 ET
    assert eng.select_today(early_evening)["action"] == "no_pick"
    world.add_game(D0, "late")  # 19:00 ET
    out = eng.select_today(early_evening + timedelta(minutes=20))
    assert out["action"] == "published"
    assert eng.conn.execute("SELECT COUNT(*) FROM ladder_no_pick_dates").fetchone()[0] == 0


def test_no_pick_pauses_active_ladder(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    world.final("g1", *AWAY_COVERS)
    eng.settle_pending(_settle_time(D0))
    for gap in (1, 2):  # two dates with nothing qualifying
        d = D0 + timedelta(days=gap)
        late = datetime(d.year, d.month, d.day, 22, 30, tzinfo=timezone.utc)
        assert eng.select_today(late)["action"] == "no_pick"
        assert eng.select_today(late)["action"] == "no_pick_recorded"
    lad = ladder(eng)
    assert lad["status"] == "active" and lad["current_amount_cents"] == 1909
    assert len(day_rows(eng)) == 1
    d3 = D0 + timedelta(days=3)
    world.add_game(d3, "g2")
    out = eng.run_cycle(_noon_et(d3))
    assert out["selection"] == {"action": "published", "ladder_id": 1, "day": 2}
    rows = day_rows(eng)
    assert rows[1]["pick_date"] == d3.isoformat() and rows[1]["stake_cents"] == 1909
    paid = page_context(eng.conn, premium=True)
    assert paid["ladder"]["cycle_end_date"] == "" and paid["ladder"]["day"] == 2


def test_optional_calendar_cap_expires_ladder(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db, max_calendar_days=3)
    eng.run_cycle(_noon_et(D0))
    assert ladder(eng)["cycle_end_date"] == (D0 + timedelta(days=2)).isoformat()
    world.final("g1", *AWAY_COVERS)
    eng.settle_pending(_settle_time(D0))
    assert eng.expire_overdue(_noon_et(D0 + timedelta(days=3))) == 1
    assert ladder(eng)["status"] == "expired"


def test_price_range_and_missing_odds(world, ladder_db):
    world.add_game(D0, "heavy", book={"spread_price_away": -200})
    world.add_game(D0, "long", book={"spread_price_away": 160})
    world.add_game(D0, "noprice", book={"spread_price_away": None})
    world.add_game(D0, "nobook")
    world.odds["nobook"] = None
    world.add_game(D0, "wrongside", model={"xs_spread": 5.0})  # takes the home favorite: no record
    eng = make_engine(world, ladder_db)
    found = eng.find_pick(_noon_et(D0))
    assert found["pick"] is None
    assert found["rejected"] == {"price outside -150 to +150": 2, "missing or invalid odds": 2,
                                 "no pick type with a qualifying record": 1}


def test_started_and_unknown_start_rejected(world, ladder_db):
    world.add_game(D0, "early", start_hour_utc=15)
    world.add_game(D0, "nostart")
    world.events["nostart"] = {"start": None, "state": "pre"}
    found = make_engine(world, ladder_db).find_pick(_noon_et(D0))
    assert found["pick"] is None
    assert found["rejected"] == {"event started or finished": 1, "start time unknown": 1}


def test_best_record_wins(world, ladder_db):
    # A second pick type (gap of 4+) with a stronger record.
    world.seed_history(40, 10, model={"xs_spread": 2.0}, book={"spread": -6.0},
                       win_score=(20, 21), loss_score=(30, 20))
    world.add_game(D0, "ok")
    world.add_game(D0, "better", model={"xs_spread": 2.0}, book={"spread": -6.0})
    pick = make_engine(world, ladder_db).find_pick(_noon_et(D0))["pick"]
    assert pick["game_id"] == "better" and pick["bucket"] == 4.0


def test_snapshot_does_not_change_after_model_update(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    con = sqlite3.connect(world.main)
    con.execute("UPDATE predictions SET lock_xs_spread = 9")
    con.commit()
    con.close()
    world.odds["g1"]["spread_price_away"] = 140
    eng.run_cycle(_noon_et(D0) + timedelta(hours=1))
    row = day_rows(eng)[0]
    assert row["american_odds"] == -110 and row["line"] == 3.0 and row["pick_side"] == "AWAY"


def test_day_ten_completes(world, ladder_db):
    eng = make_engine(world, ladder_db)
    amount = 1000
    for i in range(10):
        d = D0 + timedelta(days=i)
        world.add_game(d, f"g{i}")
        out = eng.run_cycle(_noon_et(d) if i == 0 else _settle_time(d - timedelta(days=1)))
        assert out["selection"]["action"] == "published", (i, out)
        world.final(f"g{i}", *AWAY_COVERS)
        amount += money.profit_cents(amount, -110)
    out = eng.run_cycle(_settle_time(D0 + timedelta(days=9)))
    lad = ladder(eng, 1)
    assert lad["status"] == "completed" and lad["final_day"] == 10 and lad["current_amount_cents"] == amount
    assert out["settled"][0]["ladder_status"] == "completed"
    stats = ladder_stats(eng.conn)
    assert stats["completed"] == 1 and stats["completion_rate"] == 1.0
    assert stats["per_day"][9]["reached"] == 1 and stats["per_day"][9]["success_rate"] == 1.0


def test_duplicate_scheduler_runs(world, ladder_db):
    world.add_game(D0, "g1")
    world.add_game(D0, "g2")
    eng = make_engine(world, ladder_db)
    now = _noon_et(D0)
    eng.run_cycle(now)
    assert eng.run_cycle(now)["selection"]["action"] == "waiting_for_result"
    assert make_engine(world, ladder_db).run_cycle(now)["selection"]["action"] == "waiting_for_result"
    assert len(day_rows(eng)) == 1

    world.final(day_rows(eng)[0]["game_id"], *AWAY_COVERS)
    later = _settle_time(D0)
    results = []

    def settle():
        results.append(make_engine(world, ladder_db).settle_pending(later))

    threads = [threading.Thread(target=settle) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(len(r) for r in results) == 1
    assert ladder(eng)["current_amount_cents"] == 1909


def test_concurrent_selection_publishes_once(world, ladder_db):
    world.add_game(D0, "g1")
    store.connect(ladder_db).close()
    outs = []

    def run():
        outs.append(make_engine(world, ladder_db).select_today(_noon_et(D0))["action"])

    threads = [threading.Thread(target=run) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outs.count("published") == 1
    con = store.connect(ladder_db)
    assert con.execute("SELECT COUNT(*) FROM ladder_runs").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM ladder_days").fetchone()[0] == 1


def test_old_ladder_db_gets_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(store.SCHEMA.replace(
        "    line REAL,                         -- spread line for the picked side, or the total\n", "").replace(
        "    pick_label TEXT,                   -- bet as placed, e.g. 'Under 6.5'\n", ""))
    con.close()
    cols = {r["name"] for r in store.connect(path).execute("PRAGMA table_info(ladder_days)")}
    assert {"line", "pick_label", "pick_type", "model_name"} <= cols


def test_stats_do_not_count_running_ladder_as_failure(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    stats = ladder_stats(eng.conn)
    assert stats["total"] == 1 and stats["finished"] == 0 and stats["completion_rate"] is None
    assert stats["per_day"][0]["pending"] == 1 and stats["per_day"][0]["success_rate"] is None


# ── Premium access ───────────────────────────────────────────────────────────
def test_open_pick_hidden_from_free_viewers(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    free = page_context(eng.conn, premium=False)
    assert free["today_pick"]["locked"] is True
    assert "pick" not in free["today_pick"] and "odds" not in free["today_pick"]
    flat = json.dumps(free, default=str)
    assert "g1" not in flat and "+3" not in flat and "-110" not in flat
    paid = page_context(eng.conn, premium=True)
    assert paid["today_pick"]["pick"] == "Away g1 +3" and paid["today_pick"]["odds"] == "-110"
    assert paid["today_pick"]["market"] == "Spread" and paid["today_pick"]["record"] == "66.0% of 50"


def test_settled_days_visible_to_free_viewers(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    world.final("g1", *AWAY_COVERS)
    eng.settle_pending(_settle_time(D0))
    free = page_context(eng.conn, premium=False)
    assert free["days"][0]["pick"] == "Away g1 +3" and free["days"][0]["result"] == "win"


# ── Odds source guard ────────────────────────────────────────────────────────
def test_book_odds_drops_spread_estimated_moneyline(monkeypatch):
    import pl_book_odds_api as api

    est = api._ml_from_spread_fallback(-3.5)
    base = {"spread": -3.5, "total": 44.5, "spread_price_home": -110, "spread_price_away": -110,
            "total_over_price": -105, "total_under_price": -115}
    monkeypatch.setattr(api, "build_pl_book_odds",
                        lambda *a, **k: {**base, "home_moneyline": est[0], "away_moneyline": est[1]})
    got = sources.book_odds({"sport": "NFL", "game_id": "x", "home_team": "A", "away_team": "B"})
    assert got["home_ml"] is None and got["away_ml"] is None
    assert got["spread"] == -3.5 and got["under_price"] == -115
    monkeypatch.setattr(api, "build_pl_book_odds",
                        lambda *a, **k: {**base, "home_moneyline": -170, "away_moneyline": 145})
    got = sources.book_odds({"sport": "NFL", "game_id": "x", "home_team": "A", "away_team": "B"})
    assert (got["home_ml"], got["away_ml"]) == (-170, 145)


# ── Social posting ───────────────────────────────────────────────────────────
def test_social_retries_without_duplicates(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    assert social.queue_due_posts(eng.conn, ["x", "discord"]) == 2
    assert social.queue_due_posts(eng.conn, ["x", "discord"]) == 0
    calls = {"x": 0, "discord": 0}

    def flaky_x(text):
        calls["x"] += 1
        assert "g1" not in text and "Day 1 is live" in text
        if calls["x"] == 1:
            raise RuntimeError("rate limited")
        return "tweet-1"

    def discord(text):
        calls["discord"] += 1
        return "ok"

    senders = {"x": flaky_x, "discord": discord}
    assert social.process_queue(eng.conn, senders)["failed"] == 1
    assert social.process_queue(eng.conn, senders)["posted"] == 1
    assert social.process_queue(eng.conn, senders)["posted"] == 0
    assert calls == {"x": 2, "discord": 1}


def test_social_stale_send_goes_to_review(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    social.queue_due_posts(eng.conn, ["x"])
    eng.conn.execute("UPDATE ladder_social_posts SET status = 'sending', updated_at = '2000-01-01T00:00:00Z'")
    out = social.process_queue(eng.conn, {"x": lambda t: "id"})
    assert out["needs_review"] == 1 and out["posted"] == 0


def test_social_disabled_without_env(monkeypatch):
    monkeypatch.delenv("LADDER_SOCIAL_ENABLED", raising=False)
    assert social.enabled_platforms() == []
    monkeypatch.setenv("LADDER_SOCIAL_ENABLED", "1")
    monkeypatch.setenv("LADDER_DISCORD_WEBHOOK_URL", "https://example.invalid/hook")
    for k in ("X_API_KEY", "FB_PAGE_ID", "BLUESKY_HANDLE"):
        monkeypatch.delenv(k, raising=False)
    assert social.enabled_platforms() == ["discord"]


def test_result_post_only_after_settlement(world, ladder_db):
    world.add_game(D0, "g1")
    eng = make_engine(world, ladder_db)
    eng.run_cycle(_noon_et(D0))
    social.queue_due_posts(eng.conn, ["discord"])
    assert {r["kind"] for r in eng.conn.execute("SELECT kind FROM ladder_social_posts")} == {"day_live"}
    world.final("g1", *HOME_COVERS)
    eng.settle_pending(_settle_time(D0))
    social.queue_due_posts(eng.conn, ["discord"])
    rows = [dict(r) for r in eng.conn.execute("SELECT kind, text FROM ladder_social_posts ORDER BY id")]
    assert rows[-1]["kind"] == "ladder_result" and "ended on Day 1" in rows[-1]["text"]
    out = social.process_queue(eng.conn, {"discord": lambda t: "ok"})
    assert out["skipped"] == 1 and out["posted"] == 1
