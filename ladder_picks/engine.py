"""Ladder rules: pick selection, settlement, and ladder lifecycle.

Rules
- A ladder starts at $10 and runs 10 ladder days. A ladder day is one published
  pick (now a 3-leg parlay), not a calendar day: at most one per Eastern-time
  date. The next pick is chosen only after the previous one settles.
- Win: next stake = stake + profit at the published (combined) odds.
- Loss: the ladder ends (failed).
- Push or void: not a loss. The stake carries to the next day unchanged.
- No qualifying parlay on a date: the date is recorded and the ladder waits.
- LADDER_MAX_CALENDAR_DAYS (default 0 = no limit) can cap calendar length.

Pick rule (from 2026-11-16)
- Each ladder day is a 3-leg parlay: two moneylines at −300 or heavier
  (american ≤ −300) plus one spread that is likely to cover.
- Legs come from three different games on today's ET slate.
- Heavy ML and spread legs are chosen by model win chance (likely to win /
  cover). A −400 price needs ~80% just to break even, so the long-run type
  lower is not required to clear that chalk bar — it only ranks legs.
- Combined American odds are the product of the three leg prices.
"""
from __future__ import annotations

import json
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
    select_hour: int = field(default_factory=lambda: _env_int("LADDER_SELECT_HOUR", 8))
    no_pick_hour: int = field(default_factory=lambda: _env_int("LADDER_NO_PICK_HOUR", 18))
    min_lead_minutes: int = field(default_factory=lambda: _env_int("LADDER_MIN_LEAD_MINUTES", 30))
    # Legacy single-pick band (still used for graded-history filters on spreads)
    min_decimal: float = field(default_factory=lambda: _env_float("LADDER_MIN_DECIMAL_ODDS", 1.6666))
    max_decimal: float = field(default_factory=lambda: _env_float("LADDER_MAX_DECIMAL_ODDS", 2.50))
    # Parlay: ML favorites this heavy or heavier (e.g. -300, -400)
    heavy_ml_american: int = field(default_factory=lambda: _env_int("LADDER_HEAVY_ML_AMERICAN", -300))
    # Spread leg: chalk / likely cover band
    spread_min_decimal: float = field(default_factory=lambda: _env_float("LADDER_SPREAD_MIN_DECIMAL", 1.50))
    spread_max_decimal: float = field(default_factory=lambda: _env_float("LADDER_SPREAD_MAX_DECIMAL", 2.20))
    # "Very likely to win / cover" — model win chance for the ML and spread legs.
    ml_min_model_prob: float = field(default_factory=lambda: _env_float("LADDER_ML_MIN_MODEL_PROB", 0.70))
    spread_min_model_prob: float = field(default_factory=lambda: _env_float("LADDER_SPREAD_MIN_MODEL_PROB", 0.62))
    min_type_games: int = field(default_factory=lambda: _env_int("LADDER_MIN_TYPE_GAMES", 100))
    min_type_rate: float = field(default_factory=lambda: _env_float("LADDER_MIN_TYPE_HIT_RATE", 0.0))
    min_type_roi: float = field(default_factory=lambda: _env_float("LADDER_MIN_TYPE_ROI", 0.0))
    min_half_rate: float = field(default_factory=lambda: _env_float("LADDER_MIN_HALF_HIT_RATE", 0.0))
    void_after_hours: int = field(default_factory=lambda: _env_int("LADDER_VOID_AFTER_HOURS", 72))
    # A 21-day window picked whichever type was hot and went 13-18 when replayed
    # (tools/ladder_backtest.py); the whole history held up better.
    history_days: int = field(default_factory=lambda: _env_int("LADDER_HISTORY_DAYS", 400))
    price_z: float = field(default_factory=lambda: _env_float("LADDER_PRICE_CONFIDENCE_Z", 0.0))
    type_prior: int = field(default_factory=lambda: _env_int("LADDER_TYPE_PRIOR", 20))
    # Off: a pick type's record covers all its picks. Splitting by edge size made
    # many small records whose best 14-day hit rate did not hold up the next day.
    gap_buckets: bool = field(default_factory=lambda: _env_int("LADDER_GAP_BUCKETS", 0) == 1)
    # (sport, market, side) never used; None matches any. The model takes the
    # underdog on nearly every NHL puck line / soccer goal line, its record equals
    # the plain underdog cover rate, and those lines are priced well above -110. A
    # soccer -0.5 favourite also loses on a draw, a three-way result priced as
    # two-way. The rest lose over the long run on pre-game lines.
    # Totals stay eligible so a weak season model (e.g. NCAAW XSharp O/U)
    # can be faded into the −150…−100 ladder band.
    excluded_types: tuple = (("NHL", "spread", "dog"), ("SOCCER", "spread", None),
                             ("NHL", "total", "UNDER"), ("WNBA", None, None),
                             ("MLB", "moneyline", None), ("MLB", "spread", None))
    max_priced: int = 150


# Puck line / run line prices run far from -110; with no stored price the record
# cannot be priced, so it is left out.
UNPRICED_SPREAD = {"NHL", "MLB"}


def _wilson_lower(wins: int, n: int, z: float) -> float:
    """Low end of a hit rate's confidence range (Wilson score)."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (centre - margin) / denom


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
    def _leg_final(self, sport: str, game_id: str):
        final = None
        try:
            final = self.predictions.final_score(sport, game_id)
        except Exception as exc:
            logger.warning("ladder: final score lookup failed for %s: %s", game_id, exc)
        status = None
        if not final or final.get("state") != "final":
            status = self.event_fn(sport, game_id)
            if status and status.get("state") == "final":
                final = status
        return final, status

    def _outcome_parlay(self, day, now: datetime, legs: list[dict]) -> tuple[str, int | None, int | None, str] | None:
        """Grade every leg. Parlay wins only if all legs win; any loss loses; push/void soft-fail."""
        results = []
        notes = []
        latest_start = None
        for i, leg in enumerate(legs):
            sport, gid = leg.get("sport") or "", leg.get("game_id") or ""
            final, status = self._leg_final(sport, gid)
            start = _parse_iso(leg.get("event_start") or leg.get("start"))
            if start and (latest_start is None or start > latest_start):
                latest_start = start
            if final and final.get("state") == "final":
                hs, aw = int(final["home_score"]), int(final["away_score"])
                results.append(markets.grade(leg.get("market") or "moneyline", leg.get("side"), leg.get("line"), hs, aw))
                continue
            if status and status.get("state") in ("postponed", "canceled"):
                return "void", None, None, f"Leg {i + 1} postponed or canceled"
            if start and now < start:
                return None
            results.append(None)
        if any(r is None for r in results):
            if latest_start and now - latest_start > timedelta(hours=self.cfg.void_after_hours):
                return "void", None, None, "No final result on a parlay leg"
            return None
        if any(r == "loss" for r in results):
            lost = [i + 1 for i, r in enumerate(results) if r == "loss"]
            return "loss", None, None, f"Lost leg(s) {', '.join(map(str, lost))}"
        if any(r == "push" for r in results):
            return "push", None, None, "A parlay leg pushed"
        if all(r == "win" for r in results):
            return "win", None, None, ""
        return "void", None, None, "Parlay leg unsettled"

    def _outcome(self, day, now: datetime) -> tuple[str, int | None, int | None, str] | None:
        """(result, home_score, away_score, note) or None while unresolved."""
        legs_raw = None
        try:
            legs_raw = day["legs_json"]
        except (KeyError, IndexError, TypeError):
            legs_raw = None
        if legs_raw:
            try:
                legs = json.loads(legs_raw)
            except (TypeError, ValueError):
                legs = None
            if isinstance(legs, list) and legs:
                return self._outcome_parlay(day, now, legs)

        final, status = self._leg_final(day["sport"], day["game_id"])
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
            # Parlays may have a later leg; still attempt once the earliest tip has passed.
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
    def _price_ok(self, american, market: str | None = None) -> bool:
        """True when a graded/history price belongs in a pick-type record."""
        if american is None:
            return False
        dec = float(money.american_to_decimal(american))
        if self.cfg.min_decimal - 1e-9 <= dec <= self.cfg.max_decimal + 1e-9:
            return True
        # Heavy ML favorites used as parlay legs
        if (market or "") == "moneyline" and int(american) <= self.cfg.heavy_ml_american:
            return True
        if (market or "") == "spread" and (
                self.cfg.spread_min_decimal - 1e-9 <= dec <= self.cfg.spread_max_decimal + 1e-9):
            return True
        return False

    def _in_pick_band(self, american) -> bool:
        """One ladder pick: favorites priced from −100 to −150."""
        if american is None:
            return False
        price = int(american)
        return -150 <= price <= -100

    def _ml_heavy_ok(self, american) -> bool:
        return american is not None and int(american) <= self.cfg.heavy_ml_american

    def _ml_likely_ok(self, american, model_prob) -> bool:
        """Heavy favorite that the model also likes — not the type-vs-chalk bar."""
        if not self._ml_heavy_ok(american):
            return False
        if model_prob is None:
            return False
        return float(model_prob) + 1e-9 >= self.cfg.ml_min_model_prob

    def _spread_likely_ok(self, american, model_prob) -> bool:
        if american is None:
            return False
        dec = float(money.american_to_decimal(american))
        if not (self.cfg.spread_min_decimal - 1e-9 <= dec <= self.cfg.spread_max_decimal + 1e-9):
            return False
        if model_prob is None or float(model_prob) + 1e-9 < self.cfg.spread_min_model_prob:
            return False
        return True

    def excluded(self, sport: str, market: str, side: str) -> bool:
        return any(s == sport and m in (None, market) and d in (None, side)
                   for s, m, d in self.cfg.excluded_types)

    def type_stats(self, today: date) -> dict:
        """{pick type: record} from our graded history, rebuilt at most hourly."""
        key = today.isoformat()
        if self._types and self._types[0] == key and time.time() - self._types[1] < 3600:
            return self._types[2]
        since, until = sources.window(today, self.cfg.history_days)
        groups: dict[tuple, list[tuple[bool, float]]] = {}
        for rec in self.predictions.graded_history(since, until):
            if rec.get("unpriced"):
                dec = 1.909
            else:
                price = money.parse_american(rec["price"])
                if price is None and rec["market"] != "moneyline" and not (
                        rec["market"] == "spread" and rec["sport"] in UNPRICED_SPREAD):
                    price = -110  # no stored price: standard -110
                if not self._price_ok(price, rec.get("market")):
                    continue
                dec = float(money.american_to_decimal(price))
            edges = markets.gap_edges(rec["sport"], rec["market"])
            for edge in (edges if self.cfg.gap_buckets else edges[:1]):
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
                         and roi > self.cfg.min_type_roi
                         and first >= self.cfg.min_half_rate and second >= self.cfg.min_half_rate
                         and not self.excluded(k[0], k[1], k[3]))
            stats[k] = {"wins": wins, "n": n, "rate": wins / n, "lower": _wilson_lower(wins, n, self.cfg.price_z),
                        "first_half": first,
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

    def _used_game_ids(self, ladder_id: int) -> set[str]:
        used: set[str] = set()
        store.ensure_ladder_days_columns(self.conn)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(ladder_days)")}
        select = "SELECT game_id, legs_json FROM ladder_days WHERE ladder_id = ?" if "legs_json" in cols \
            else "SELECT game_id FROM ladder_days WHERE ladder_id = ?"
        for r in self.conn.execute(select, (ladder_id,)):
            if r["game_id"]:
                used.add(str(r["game_id"]))
            if "legs_json" not in cols:
                continue
            raw = r["legs_json"] if "legs_json" in r.keys() else None
            if not raw:
                continue
            try:
                legs = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for leg in legs or []:
                gid = leg.get("game_id")
                if gid:
                    used.add(str(gid))
        return used

    def _collect_leg_candidates(self, now: datetime, exclude_games: set[str] | None = None) -> dict:
        """Score open options that could become parlay legs."""
        exclude_games = exclude_games or set()
        today = now.astimezone(ET).date()
        rejected: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        stats = self.type_stats(today)
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
            pre.append(cand)

        remaining = False
        legs: list[dict] = []
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
            reason = "no pick type with a qualifying record"
            for o in markets.options(cand["sport"], cand.get("model") or {}, book):
                if o["market"] not in ("moneyline", "spread", "total"):
                    continue
                if self.excluded(cand["sport"], o["market"], o.get("side_class") or ""):
                    reason = "excluded pick type"
                    continue
                price = money.parse_american(o["price"])
                if price is None:
                    reason = "missing or invalid odds"
                    continue
                if not self._in_pick_band(price):
                    reason = "price outside −150 to −100"
                    continue
                model_p = o.get("model_prob")
                if model_p is None and o["market"] == "spread":
                    model_p = markets.cover_prob(cand["sport"], o.get("gap") or 0)
                s = None
                for edge in markets.gap_edges(cand["sport"], o["market"]):
                    if o["gap"] < edge - 1e-9:
                        continue
                    t = stats.get((cand["sport"], o["market"], o["model"], o["side_class"], edge))
                    if t and (s is None or (t["score"], t["n"], t["roi"]) > (s["score"], s["n"], s["roi"])):
                        s = t
                # Fade totals: if the model's own side has a poor season record,
                # rank the opposite using (1 − that rate) as the season hit rate.
                if o.get("faded") and o["market"] == "total":
                    lean_side = "UNDER" if o["side"] == "OVER" else "OVER"
                    lean = None
                    for edge in markets.gap_edges(cand["sport"], "total"):
                        if o["gap"] < edge - 1e-9:
                            continue
                        t = stats.get((cand["sport"], "total", o["model"], lean_side, edge))
                        if t and (lean is None or t["n"] > lean["n"]):
                            lean = t
                    if lean and lean["n"] >= 20 and lean["rate"] <= 0.45:
                        fade_rate = 1.0 - lean["rate"]
                        s = {
                            "rate": fade_rate, "n": lean["n"], "score": fade_rate,
                            "roi": -lean["roi"], "lower": fade_rate, "qualifies": True,
                        }
                    elif s is None:
                        # No history for the fade side — still allow model_prob fade.
                        pass
                # One pick in the −150 to −100 band. Rank uses season-type
                # results when we have them, then the model edge.
                if model_p is None:
                    reason = "no model result"
                    continue
                if s is None:
                    mp = float(model_p)
                    s = {"rate": mp, "n": 0, "score": mp, "roi": 0.0, "lower": mp, "qualifies": False}
                need = 1.0 / float(money.american_to_decimal(price))
                other = money.parse_american(o["other_price"])
                market_p = money.no_vig_prob(price, other) if other is not None else money.implied_prob(price)
                model_edge = float(model_p) - need
                type_bonus = 0.05 if s.get("qualifies") else 0.0
                # Prefer real season records over raw model probability.
                season_rank = s["rate"] if s["n"] >= 20 else float(model_p)
                legs.append({
                    **cand, **o, "start": start, "model_prob": model_p,
                    "american_odds": price, "opponent_american_odds": other,
                    "decimal_odds": round(float(money.american_to_decimal(price)), 4),
                    "market_prob": round(market_p, 4) if market_p is not None else None,
                    "hist_hit_rate": round(s["rate"], 4), "hist_n": s["n"],
                    "credible_prob": round(s["score"], 4), "type_roi": s["roi"],
                    "price_edge": model_edge + type_bonus,
                    "season_rank": round(season_rank, 4),
                    "pick_label": markets.label(o, cand["home_team"], cand["away_team"]),
                    "pick_type": markets.type_label(cand["sport"], o),
                })
            if not any(x["game_id"] == cand["game_id"] for x in legs):
                reject(reason)
        legs.sort(key=lambda c: (
            -c.get("season_rank", 0),
            -(c["hist_hit_rate"] if c["hist_n"] else 0),
            -c["price_edge"], -c["hist_n"], -c["type_roi"], c["start"],
        ))
        return {"legs": legs, "rejected": rejected, "prefiltered": len(pre), "remaining": remaining}

    def find_parlay_pick(self, now: datetime, exclude_games: set[str] | None = None) -> dict:
        """Build today's 3-leg parlay: 2 heavy ML + 1 likely spread, three games."""
        found = self._collect_leg_candidates(now, exclude_games)
        legs = found["legs"]
        ml = [x for x in legs if x["market"] == "moneyline"]
        spreads = [x for x in legs if x["market"] == "spread"]
        chosen_ml: list[dict] = []
        used: set[str] = set()
        for leg in ml:
            gid = str(leg["game_id"])
            if gid in used:
                continue
            chosen_ml.append(leg)
            used.add(gid)
            if len(chosen_ml) == 2:
                break
        spread = None
        for leg in spreads:
            gid = str(leg["game_id"])
            if gid in used:
                continue
            spread = leg
            break
        if len(chosen_ml) < 2 or spread is None:
            rejected = dict(found["rejected"])
            if len(chosen_ml) < 2:
                rejected["need 2 heavy ML legs"] = rejected.get("need 2 heavy ML legs", 0) + 1
            if spread is None:
                rejected["need 1 likely spread leg"] = rejected.get("need 1 likely spread leg", 0) + 1
            return {"pick": None, "rejected": rejected, "prefiltered": found["prefiltered"],
                    "remaining": found["remaining"]}

        trio = chosen_ml + [spread]
        americans = [int(x["american_odds"]) for x in trio]
        combined_am, combined_dec = money.combine_american(americans)
        starts = [x["start"] for x in trio]
        leg_payload = []
        for x in trio:
            leg_payload.append({
                "sport": x["sport"], "game_id": x["game_id"],
                "home_team": x["home_team"], "away_team": x["away_team"],
                "market": x["market"], "side": x["side"], "line": x.get("line"),
                "model": x["model"], "pick_label": x["pick_label"],
                "pick_type": x["pick_type"], "american_odds": x["american_odds"],
                "decimal_odds": x["decimal_odds"], "model_prob": x.get("model_prob"),
                "hist_hit_rate": x["hist_hit_rate"], "hist_n": x["hist_n"],
                "event_start": _iso(x["start"]),
                "event_label": f"{x['away_team']} @ {x['home_team']}",
            })
        labels = [x["pick_label"] for x in trio]
        pick = {
            "sport": "PARLAY",
            "game_id": "+".join(str(x["game_id"]) for x in trio),
            "home_team": "", "away_team": "",
            "side": "PARLAY", "market": "parlay", "line": None,
            "model": "Parlay", "model_prob": None,
            "start": min(starts),
            "american_odds": combined_am,
            "opponent_american_odds": None,
            "decimal_odds": round(combined_dec, 4),
            "market_prob": None,
            "hist_hit_rate": round(sum(x["hist_hit_rate"] for x in trio) / 3, 4),
            "hist_n": min(x["hist_n"] for x in trio),
            "credible_prob": round(sum(x["credible_prob"] for x in trio) / 3, 4),
            "type_roi": min(x["type_roi"] for x in trio),
            "price_edge": min(x["price_edge"] for x in trio),
            "pick_label": " + ".join(labels),
            "pick_type": "Parlay (2 ML ≤ −300 + 1 spread)",
            "legs": leg_payload,
            "event_label": "3-leg parlay · " + " · ".join(
                f"{x['sport']}: {x['pick_label']}" for x in trio),
        }
        return {"pick": pick, "rejected": found["rejected"], "prefiltered": found["prefiltered"],
                "remaining": found["remaining"]}

    def find_pick(self, now: datetime, exclude_games: set[str] | None = None) -> dict:
        """One pick, priced −150 to −100, best results then model edge."""
        found = self._collect_leg_candidates(now, exclude_games)
        legs = found["legs"]
        if not legs:
            return {"pick": None, "rejected": found["rejected"], "prefiltered": found["prefiltered"],
                    "remaining": found["remaining"]}
        best = legs[0]
        best["event_label"] = f"{best.get('away_team', '')} @ {best.get('home_team', '')}".strip(" @")
        best["pick_type"] = "Best −150 to −100"
        return {"pick": best, "rejected": found["rejected"], "prefiltered": found["prefiltered"],
                "remaining": found["remaining"]}

    def select_today(self, now: datetime) -> dict:
        et_now = now.astimezone(ET)
        today = et_now.date().isoformat()
        if et_now.hour < self.cfg.select_hour:
            return {"action": "too_early"}
        ladder = self.active_ladder()
        exclude: set[str] = set()
        if ladder:
            last = self.last_day(ladder["id"])
            if last and last["result"] == "pending":
                return {"action": "waiting_for_result", "ladder_id": ladder["id"]}
            if last and last["pick_date"] >= today:
                return {"action": "already_picked", "ladder_id": ladder["id"]}
            if today > ladder["cycle_end_date"]:
                return {"action": "cycle_over", "ladder_id": ladder["id"]}
            next_day = (last["day_number"] if last else 0) + 1
            exclude = self._used_game_ids(ladder["id"])
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
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO ladder_no_pick_dates (pick_date, reason, created_at) VALUES (?, ?, ?)",
                (today, reason, _iso(now)),
            )
        if not cur.rowcount:
            return {"action": "no_pick_recorded", "ladder_id": ladder["id"] if ladder else None}
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
            pick_team = {"HOME": pick.get("home_team"), "AWAY": pick.get("away_team")}.get(pick.get("side"))
            if pick.get("market") == "parlay":
                pick_team = "Parlay"
            model_p = pick.get("model_prob")
            event_label = pick.get("event_label") or (
                f"{pick.get('away_team', '')} @ {pick.get('home_team', '')}".strip(" @")
            )
            legs_json = json.dumps(pick["legs"]) if pick.get("legs") else None
            # Persistent /data/ladder.db may predate legs_json — migrate before insert.
            store.ensure_ladder_days_columns(self.conn)
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(ladder_days)")}
            base_cols = (
                "ladder_id, day_number, pick_date, result, sport, game_id, "
                "event_label, home_team, away_team, pick_team, pick_side, market, line, pick_label, "
                "pick_type, model_name, model_prob, market_prob, credible_prob, hist_hit_rate, hist_n, "
                "american_odds, decimal_odds, opponent_american_odds, stake_cents, event_start, "
                "selected_at"
            )
            base_vals = (
                ladder_id, next_day, today, "pending", pick["sport"], pick["game_id"],
                event_label, pick.get("home_team") or "", pick.get("away_team") or "",
                pick_team, pick.get("side"), pick["market"], pick.get("line"), pick["pick_label"],
                pick["pick_type"], pick.get("model"), round(model_p, 4) if model_p is not None else None,
                pick.get("market_prob"), pick["credible_prob"], pick["hist_hit_rate"], pick["hist_n"],
                pick["american_odds"], pick["decimal_odds"], pick.get("opponent_american_odds"), stake,
                _iso(pick["start"]), _iso(now),
            )
            if "legs_json" in cols:
                placeholders = ",".join("?" for _ in range(len(base_vals) + 1))
                self.conn.execute(
                    f"INSERT INTO ladder_days ({base_cols}, legs_json) VALUES ({placeholders})",
                    base_vals + (legs_json,),
                )
            else:
                placeholders = ",".join("?" for _ in range(len(base_vals)))
                self.conn.execute(
                    f"INSERT INTO ladder_days ({base_cols}) VALUES ({placeholders})",
                    base_vals,
                )
            self.conn.execute("DELETE FROM ladder_no_pick_dates WHERE pick_date = ?", (today,))
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
