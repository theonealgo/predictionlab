"""Ladder rules: pick selection, settlement, and ladder lifecycle.

Rules
- A ladder starts at $10 and runs 10 ladder days. A ladder day is one pick, not
  a calendar day: at most one pick per Eastern-time date, for an event on that
  date that has not started. The next pick is chosen only after the previous
  one settles.
- Win: next stake = stake + profit at the published odds.
- Loss: the ladder ends (failed).
- Push or void: not a loss. The stake carries to the next day unchanged and the
  ladder day is used. A push or void on Day 10 completes the ladder.
- No qualifying pick on a date: the date is recorded and nothing else changes.
  A running ladder pauses until the next qualifying pick.
- LADDER_MAX_CALENDAR_DAYS (default 0 = no limit) can cap how long a ladder may
  run; one that runs out before Day 10 ends as expired.

Pick rule
- Candidates are our moneyline, spread and total picks for today's games.
- A pick qualifies only if its pick type (see markets.py) has, over the last
  365 days against pre-game book lines: at least 40 graded picks, a 60%+ hit
  rate, and 55%+ in each half of that period (oldest half / newest half).
- The posted price at pick time must be -150 to +150.
- The best record wins (shrunk slightly toward 50% so a small sample does not
  beat a large one), then return, then earliest start.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from . import markets, money, sources, store

logger = logging.getLogger("ladder_picks")
ET = ZoneInfo("America/New_York")
NO_CYCLE_END = "9999-12-31"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    start_cents: int = field(default_factory=lambda: _env_int("LADDER_START_CENTS", 1000))
    days: int = field(default_factory=lambda: _env_int("LADDER_DAYS", 10))
    max_calendar_days: int = field(default_factory=lambda: _env_int("LADDER_MAX_CALENDAR_DAYS", 0))
    select_hour: int = field(default_factory=lambda: _env_int("LADDER_SELECT_HOUR", 11))
    no_pick_hour: int = field(default_factory=lambda: _env_int("LADDER_NO_PICK_HOUR", 18))
    min_lead_minutes: int = field(default_factory=lambda: _env_int("LADDER_MIN_LEAD_MINUTES", 30))
    # -150 to +150
    min_decimal: float = field(default_factory=lambda: _env_float("LADDER_MIN_DECIMAL_ODDS", 1.6666))
    max_decimal: float = field(default_factory=lambda: _env_float("LADDER_MAX_DECIMAL_ODDS", 2.50))
    min_type_games: int = field(default_factory=lambda: _env_int("LADDER_MIN_TYPE_GAMES", 40))
    min_type_rate: float = field(default_factory=lambda: _env_float("LADDER_MIN_TYPE_HIT_RATE", 0.60))
    min_half_rate: float = field(default_factory=lambda: _env_float("LADDER_MIN_HALF_HIT_RATE", 0.55))
    void_after_hours: int = field(default_factory=lambda: _env_int("LADDER_VOID_AFTER_HOURS", 72))
    history_days: int = 365
    type_prior: int = 10
    max_priced: int = 40


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class LadderEngine:
    def __init__(
        self,
        conn=None,
        predictions: sources.PredictionSource | None = None,
        odds_fn: Callable[[dict], dict | None] = sources.book_odds,
        event_fn: Callable[[str, str], dict | None] = sources.event_status,
        config: Config | None = None,
    ):
        self.conn = conn or store.connect()
        self.predictions = predictions or sources.PredictionSource()
        self.odds_fn = odds_fn
        self.event_fn = event_fn
        self.cfg = config or Config()
        self._types: tuple[str, float, dict] | None = None

    # ── State ────────────────────────────────────────────────────────────────
    def active_ladder(self):
        return self.conn.execute(
            "SELECT * FROM ladder_runs WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def last_day(self, ladder_id: int):
        return self.conn.execute(
            "SELECT * FROM ladder_days WHERE ladder_id = ? ORDER BY day_number DESC LIMIT 1",
            (ladder_id,),
        ).fetchone()

    def _last_pick_date(self) -> str:
        row = self.conn.execute("SELECT MAX(pick_date) AS d FROM ladder_days").fetchone()
        return (row["d"] if row else None) or ""

    # ── Settlement ───────────────────────────────────────────────────────────
    def _outcome(self, day, now: datetime) -> tuple[str, int | None, int | None, str] | None:
        """(result, home_score, away_score, note) or None while the game is unresolved."""
        final = None
        try:
            final = self.predictions.final_score(day["sport"], day["game_id"])
        except Exception as exc:
            logger.warning("ladder: final score lookup failed for %s: %s", day["game_id"], exc)
        status = None
        if not final or final.get("state") != "final":
            status = self.event_fn(day["sport"], day["game_id"])
            if status and status.get("state") == "final":
                final = status
        if final and final.get("state") == "final":
            hs, aw = int(final["home_score"]), int(final["away_score"])
            result = markets.grade(day["market"] or "moneyline", day["pick_side"], day["line"], hs, aw)
            return result, hs, aw, "Push" if result == "push" else ""
        if status and status.get("state") in ("postponed", "canceled"):
            return "void", None, None, "Event postponed or canceled"
        start = _parse_iso(day["event_start"])
        if start and now - start > timedelta(hours=self.cfg.void_after_hours):
            return "void", None, None, "No final result"
        return None

    def settle_pending(self, now: datetime) -> list[dict]:
        settled = []
        pending = self.conn.execute(
            "SELECT * FROM ladder_days WHERE result = 'pending' ORDER BY id"
        ).fetchall()
        for day in pending:
            start = _parse_iso(day["event_start"])
            if start and now < start:
                continue
            outcome = self._outcome(day, now)
            if not outcome:
                continue
            result, hs, aw, note = outcome
            stake = int(day["stake_cents"])
            if result == "win":
                profit = money.profit_cents(stake, int(day["american_odds"]))
                after = stake + profit
            elif result == "loss":
                profit, after = -stake, 0
            else:
                profit, after = 0, stake
            with store.write_txn(self.conn):
                cur = self.conn.execute(
                    """UPDATE ladder_days SET result = ?, profit_cents = ?, amount_after_cents = ?,
                           settled_at = ?, final_home_score = ?, final_away_score = ?, note = ?
                       WHERE id = ? AND result = 'pending'""",
                    (result, profit, after, _iso(now), hs, aw, note or None, day["id"]),
                )
                if cur.rowcount != 1:
                    continue
                ladder_status, reason = "active", None
                if result == "loss":
                    ladder_status, reason = "failed", f"Lost on Day {day['day_number']}"
                elif day["day_number"] >= self.cfg.days:
                    ladder_status, reason = "completed", f"Completed all {self.cfg.days} days"
                if ladder_status == "active":
                    self.conn.execute(
                        "UPDATE ladder_runs SET current_amount_cents = ? WHERE id = ? AND status = 'active'",
                        (after, day["ladder_id"]),
                    )
                else:
                    self.conn.execute(
                        """UPDATE ladder_runs SET current_amount_cents = ?, status = ?, final_day = ?,
                               end_reason = ?, finished_at = ?
                           WHERE id = ? AND status = 'active'""",
                        (after, ladder_status, day["day_number"], reason, _iso(now), day["ladder_id"]),
                    )
            logger.info("ladder: settled ladder %s day %s as %s", day["ladder_id"], day["day_number"], result)
            settled.append({"ladder_id": day["ladder_id"], "day": day["day_number"], "result": result,
                            "ladder_status": ladder_status})
        return settled

    def expire_overdue(self, now: datetime) -> int | None:
        ladder = self.active_ladder()
        if not ladder:
            return None
        today = now.astimezone(ET).date().isoformat()
        if today <= ladder["cycle_end_date"]:
            return None
        last = self.last_day(ladder["id"])
        if last and last["result"] == "pending":
            return None
        with store.write_txn(self.conn):
            cur = self.conn.execute(
                """UPDATE ladder_runs SET status = 'expired', final_day = ?, end_reason = ?, finished_at = ?
                   WHERE id = ? AND status = 'active'""",
                (last["day_number"] if last else 0, "Cycle ended before Day "
                 f"{self.cfg.days}", _iso(now), ladder["id"]),
            )
        if cur.rowcount == 1:
            logger.info("ladder: ladder %s expired", ladder["id"])
            return ladder["id"]
        return None

    # ── Selection ────────────────────────────────────────────────────────────
    def _price_ok(self, american) -> bool:
        if american is None:
            return False
        return self.cfg.min_decimal - 1e-9 <= float(money.american_to_decimal(american)) <= self.cfg.max_decimal + 1e-9

    def type_stats(self, today: date) -> dict:
        """{pick type: record} from our graded history, rebuilt at most hourly."""
        key = today.isoformat()
        if self._types and self._types[0] == key and time.time() - self._types[1] < 3600:
            return self._types[2]
        since, until = sources.window(today, self.cfg.history_days)
        groups: dict[tuple, list[tuple[bool, float]]] = {}
        for rec in self.predictions.graded_history(since, until):
            if rec["market"] == "moneyline":
                price = money.parse_american(rec["price"])
                if not self._price_ok(price):
                    continue
                dec = float(money.american_to_decimal(price))
            else:
                dec = 1.909  # spread / total prices are not stored; standard -110
            for edge in markets.gap_edges(rec["sport"], rec["market"]):
                if rec["gap"] >= edge - 1e-9:
                    k = (rec["sport"], rec["market"], rec["model"], rec["side_class"], edge)
                    groups.setdefault(k, []).append((rec["win"], dec))
        stats = {}
        for k, picks in groups.items():
            n = len(picks)
            wins = sum(1 for w, _ in picks if w)
            half = n // 2
            first = sum(1 for w, _ in picks[:half] if w) / half if half else 0.0
            second = sum(1 for w, _ in picks[half:] if w) / (n - half) if n - half else 0.0
            roi = sum((d - 1) if w else -1 for w, d in picks) / n
            qualifies = (n >= self.cfg.min_type_games and wins / n >= self.cfg.min_type_rate
                         and first >= self.cfg.min_half_rate and second >= self.cfg.min_half_rate)
            stats[k] = {"wins": wins, "n": n, "rate": wins / n, "first_half": first,
                        "second_half": second, "roi": roi, "qualifies": qualifies,
                        "score": (wins + self.cfg.type_prior * 0.5) / (n + self.cfg.type_prior)}
        self._types = (key, time.time(), stats)
        return stats

    def qualifying_types(self, today: date) -> list[dict]:
        rows = []
        for (sport, market, model, side, edge), s in self.type_stats(today).items():
            if s["qualifies"]:
                rows.append({"sport": sport, "market": market, "model": model, "side": side,
                             "gap_at_least": edge, **{k: round(v, 4) if isinstance(v, float) else v
                                                      for k, v in s.items()}})
        rows.sort(key=lambda r: -r["score"])
        return rows

    def find_pick(self, now: datetime, exclude_games: set[str] | None = None) -> dict:
        """Best qualifying pick for today's ET date.

        Returns {'pick': dict|None, 'rejected': {reason: count}, 'prefiltered': int,
        'remaining': bool} where remaining means a prefiltered game has not started yet.
        """
        exclude_games = exclude_games or set()
        today = now.astimezone(ET).date()
        rejected: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        stats = self.type_stats(today)
        live_sports = {k[0] for k, s in stats.items() if s["qualifies"]}
        seen: set[str] = set()
        pre: list[dict] = []
        for cand in self.predictions.candidates(today.isoformat()):
            gid = cand.get("game_id")
            if not gid or gid in seen:
                reject("duplicate")
                continue
            seen.add(gid)
            if gid in exclude_games:
                reject("already used in this ladder")
                continue
            if cand["sport"] not in live_sports:
                reject("no qualifying pick type for this sport")
                continue
            pre.append(cand)

        remaining = False
        scored: list[dict] = []
        lead = timedelta(minutes=self.cfg.min_lead_minutes)
        for cand in pre[: self.cfg.max_priced]:
            status = self.event_fn(cand["sport"], cand["game_id"])
            start = status.get("start") if status else None
            if not status or not start:
                reject("start time unknown")
                continue
            if status.get("state") not in ("pre", "", None) or start <= now:
                reject("event started or finished")
                continue
            remaining = True
            if start - now < lead:
                reject("starts too soon")
                continue
            if start.astimezone(ET).date() != today:
                reject("event not on today's date")
                continue
            book = self.odds_fn(cand)
            if not book:
                reject("missing or invalid odds")
                continue
            best, reason = None, "no pick type with a qualifying record"
            for o in markets.options(cand["sport"], cand.get("model") or {}, book):
                s = stats.get((cand["sport"], o["market"], o["model"], o["side_class"], o["bucket"]))
                if not s or not s["qualifies"]:
                    continue
                price = money.parse_american(o["price"])
                if price is None:
                    reason = "missing or invalid odds"
                    continue
                if not self._price_ok(price):
                    reason = "price outside -150 to +150"
                    continue
                other = money.parse_american(o["other_price"])
                market_p = money.no_vig_prob(price, other) if other is not None else money.implied_prob(price)
                pick = {
                    **cand, **o, "start": start,
                    "american_odds": price, "opponent_american_odds": other,
                    "decimal_odds": round(float(money.american_to_decimal(price)), 4),
                    "market_prob": round(market_p, 4) if market_p is not None else None,
                    "hist_hit_rate": round(s["rate"], 4), "hist_n": s["n"],
                    "credible_prob": round(s["score"], 4), "type_roi": s["roi"],
                    "pick_label": markets.label(o, cand["home_team"], cand["away_team"]),
                    "pick_type": markets.type_label(cand["sport"], o),
                }
                if best is None or (pick["credible_prob"], pick["type_roi"]) > (best["credible_prob"], best["type_roi"]):
                    best = pick
            if best is None:
                reject(reason)
                continue
            scored.append(best)
        scored.sort(key=lambda c: (-c["credible_prob"], -c["type_roi"], c["start"]))
        return {"pick": scored[0] if scored else None, "rejected": rejected,
                "prefiltered": len(pre), "remaining": remaining}

    def select_today(self, now: datetime) -> dict:
        et_now = now.astimezone(ET)
        today = et_now.date().isoformat()
        if et_now.hour < self.cfg.select_hour:
            return {"action": "too_early"}
        ladder = self.active_ladder()
        exclude: set[str] = set()
        if self.conn.execute(
            "SELECT 1 FROM ladder_no_pick_dates WHERE pick_date = ?", (today,)
        ).fetchone():
            return {"action": "no_pick_recorded", "ladder_id": ladder["id"] if ladder else None}
        if ladder:
            last = self.last_day(ladder["id"])
            if last and last["result"] == "pending":
                return {"action": "waiting_for_result", "ladder_id": ladder["id"]}
            if last and last["pick_date"] >= today:
                return {"action": "already_picked", "ladder_id": ladder["id"]}
            if today > ladder["cycle_end_date"]:
                return {"action": "cycle_over", "ladder_id": ladder["id"]}
            next_day = (last["day_number"] if last else 0) + 1
            exclude = {r["game_id"] for r in self.conn.execute(
                "SELECT game_id FROM ladder_days WHERE ladder_id = ? AND game_id IS NOT NULL",
                (ladder["id"],))}
        else:
            if self._last_pick_date() >= today:
                return {"action": "already_picked"}
            next_day = 1

        found = self.find_pick(now, exclude)
        pick = found["pick"]
        if not pick:
            final = et_now.hour >= self.cfg.no_pick_hour or (found["prefiltered"] and not found["remaining"])
            if not final:
                return {"action": "no_pick_yet", "rejected": found["rejected"]}
            return self._record_no_pick(now, today, ladder, next_day, found["rejected"])
        return self._publish(now, today, ladder, next_day, pick)

    def _record_no_pick(self, now, today, ladder, next_day, rejected) -> dict:
        reason = ", ".join(f"{k} ({v})" for k, v in sorted(rejected.items())) or "no published picks"
        with store.write_txn(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO ladder_no_pick_dates (pick_date, reason, created_at) VALUES (?, ?, ?)",
                (today, reason, _iso(now)),
            )
        logger.info("ladder: no qualifying pick for %s (%s)%s", today, reason,
                    f"; ladder {ladder['id']} paused before Day {next_day}" if ladder else "")
        return {"action": "no_pick", "ladder_id": ladder["id"] if ladder else None, "reason": reason}

    def _cycle_end(self, start: date) -> str:
        if self.cfg.max_calendar_days > 0:
            return (start + timedelta(days=self.cfg.max_calendar_days - 1)).isoformat()
        return NO_CYCLE_END

    def _publish(self, now, today, ladder, next_day, pick) -> dict:
        start_date = date.fromisoformat(today)
        with store.write_txn(self.conn):
            if ladder:
                row = self.conn.execute(
                    "SELECT * FROM ladder_runs WHERE id = ? AND status = 'active'", (ladder["id"],)
                ).fetchone()
                if not row:
                    return {"action": "already_handled"}
                if self.conn.execute(
                    "SELECT 1 FROM ladder_days WHERE ladder_id = ? AND (day_number = ? OR pick_date >= ?)",
                    (row["id"], next_day, today),
                ).fetchone():
                    return {"action": "already_picked", "ladder_id": row["id"]}
                ladder_id, stake = row["id"], int(row["current_amount_cents"])
            else:
                if self.conn.execute("SELECT 1 FROM ladder_runs WHERE status = 'active'").fetchone():
                    return {"action": "already_handled"}
                if (self.conn.execute("SELECT MAX(pick_date) AS d FROM ladder_days").fetchone()["d"] or "") >= today:
                    return {"action": "already_picked"}
                cur = self.conn.execute(
                    """INSERT INTO ladder_runs (status, start_date, cycle_end_date, start_amount_cents,
                           current_amount_cents, created_at)
                       VALUES ('active', ?, ?, ?, ?, ?)""",
                    (today, self._cycle_end(start_date), self.cfg.start_cents, self.cfg.start_cents, _iso(now)),
                )
                ladder_id, stake = cur.lastrowid, self.cfg.start_cents
            pick_team = {"HOME": pick["home_team"], "AWAY": pick["away_team"]}.get(pick["side"])
            model_p = pick.get("model_prob")
            self.conn.execute(
                """INSERT INTO ladder_days (ladder_id, day_number, pick_date, result, sport, game_id,
                       event_label, home_team, away_team, pick_team, pick_side, market, line, pick_label,
                       pick_type, model_name, model_prob, market_prob, credible_prob, hist_hit_rate, hist_n,
                       american_odds, decimal_odds, opponent_american_odds, stake_cents, event_start, selected_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ladder_id, next_day, today, pick["sport"], pick["game_id"],
                 f"{pick['away_team']} @ {pick['home_team']}", pick["home_team"], pick["away_team"],
                 pick_team, pick["side"], pick["market"], pick.get("line"), pick["pick_label"],
                 pick["pick_type"], pick["model"], round(model_p, 4) if model_p is not None else None,
                 pick["market_prob"], pick["credible_prob"], pick["hist_hit_rate"], pick["hist_n"],
                 pick["american_odds"], pick["decimal_odds"], pick["opponent_american_odds"], stake,
                 _iso(pick["start"]), _iso(now)),
            )
        logger.info("ladder: published ladder %s day %s: %s %s (%s)", ladder_id, next_day,
                    pick["sport"], pick["pick_label"], money.fmt_american(pick["american_odds"]))
        return {"action": "published", "ladder_id": ladder_id, "day": next_day}

    # ── One scheduler pass ───────────────────────────────────────────────────
    def run_cycle(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        out = {"settled": self.settle_pending(now)}
        out["expired"] = self.expire_overdue(now)
        out["selection"] = self.select_today(now)
        return out
