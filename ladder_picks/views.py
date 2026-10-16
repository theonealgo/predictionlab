"""Ladder Picks page. Today's open pick is shown to premium members only."""
from __future__ import annotations

import logging

from flask import Blueprint, make_response, render_template

from . import markets, money, social, store
from .engine import Config, ET, NO_CYCLE_END
from .stats import ladder_stats

logger = logging.getLogger("ladder_picks")

bp = Blueprint("ladder_picks", __name__)

_RESULT_LABEL = {
    "pending": "Pending", "win": "Win", "loss": "Loss", "push": "Push",
    "void": "Void", "no_pick": "No qualifying pick",
}
_STATUS_LABEL = {
    "active": "In progress", "completed": "Completed all 10 days", "failed": "Ended on a loss",
    "no_pick": "Ended — no qualifying pick", "expired": "Ended — cycle ran out",
}


def _viewer() -> tuple[bool, bool, bool]:
    """(premium, logged_in, admin) from the site's own login and membership checks."""
    try:
        from flask_login import current_user
        logged_in = bool(getattr(current_user, "is_authenticated", False))
        admin = bool(logged_in and getattr(current_user, "is_admin", False))
    except Exception:
        logged_in = admin = False
    try:
        from auth_system import is_premium_user
        premium = bool(is_premium_user())
    except Exception:
        premium = False
    return premium, logged_in, admin


def _pct(p) -> str:
    return "—" if p is None else f"{float(p) * 100:.1f}%"


def _start_et(raw: str | None) -> str:
    from .engine import _parse_iso
    dt = _parse_iso(raw)
    if not dt:
        return ""
    return dt.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET")


def _day_view(row, show_pick: bool) -> dict:
    r = dict(row)
    view = {
        "day": r["day_number"],
        "date": r["pick_date"],
        "result": r["result"],
        "result_label": _RESULT_LABEL.get(r["result"], r["result"]),
        "stake": money.fmt_money(r.get("stake_cents")),
        "profit": money.fmt_money(r.get("profit_cents")) if r["result"] not in ("pending",) else "—",
        "profit_cents": r.get("profit_cents"),
        "amount_after": money.fmt_money(r.get("amount_after_cents")) if r["result"] != "pending" else "—",
        "locked": not show_pick,
    }
    if show_pick and r["result"] != "no_pick":
        score = ""
        if r.get("final_home_score") is not None and r.get("final_away_score") is not None:
            score = f"{r['away_team']} {r['final_away_score']} – {r['home_team']} {r['final_home_score']}"
        record = "—"
        if r.get("hist_n") and r.get("hist_hit_rate") is not None:
            record = f"{_pct(r['hist_hit_rate'])} of {r['hist_n']}"
        view.update({
            "sport": r.get("sport") or "",
            "event": r.get("event_label") or "",
            "pick": r.get("pick_label") or r.get("pick_team") or "",
            "market": markets.market_name(r.get("sport") or "", r.get("market") or "moneyline"),
            "pick_type": r.get("pick_type") or "",
            "record": record,
            "odds": money.fmt_american(r.get("american_odds")),
            "decimal": f"{r['decimal_odds']:.2f}" if r.get("decimal_odds") else "—",
            "model_prob": _pct(r.get("model_prob")),
            "start": _start_et(r.get("event_start")),
            "score": score,
            "note": r.get("note") or "",
        })
    elif r["result"] == "no_pick":
        view["note"] = "No pick met the requirements"
    return view


def page_context(conn, premium: bool, cfg: Config | None = None) -> dict:
    cfg = cfg or Config()
    current = conn.execute(
        "SELECT * FROM ladder_runs ORDER BY (status = 'active') DESC, id DESC LIMIT 1"
    ).fetchone()
    days, today_pick = [], None
    if current:
        for row in conn.execute(
            "SELECT * FROM ladder_days WHERE ladder_id = ? ORDER BY day_number", (current["id"],)
        ):
            # An open pick is premium; settled days are results and are shown to everyone.
            show = premium or row["result"] != "pending"
            view = _day_view(row, show)
            days.append(view)
            if row["result"] == "pending":
                today_pick = view
    reached = max((d["day"] for d in days), default=0)
    ladder = None
    if current:
        c = dict(current)
        net = c["current_amount_cents"] - c["start_amount_cents"]
        ladder = {
            "id": c["id"],
            "status": c["status"],
            "status_label": _STATUS_LABEL.get(c["status"], c["status"]),
            "start_date": c["start_date"],
            "cycle_end_date": "" if c["cycle_end_date"] == NO_CYCLE_END else c["cycle_end_date"],
            "start_amount": money.fmt_money(c["start_amount_cents"]),
            "current_amount": money.fmt_money(c["current_amount_cents"]),
            "net": money.fmt_money(net),
            "net_cents": net,
            "day": reached,
            "end_reason": c.get("end_reason") or "",
        }
    progress = []
    by_day = {d["day"]: d for d in days}
    for n in range(1, cfg.days + 1):
        d = by_day.get(n)
        progress.append({"day": n, "state": d["result"] if d else "upcoming"})
    history = []
    for r in conn.execute(
        "SELECT * FROM ladder_runs WHERE status != 'active' ORDER BY id DESC LIMIT 25"
    ):
        history.append({
            "id": r["id"],
            "start_date": r["start_date"],
            "finished": (r["finished_at"] or "")[:10],
            "status_label": _STATUS_LABEL.get(r["status"], r["status"]),
            "status": r["status"],
            "final_day": r["final_day"] or 0,
            "final_amount": money.fmt_money(r["current_amount_cents"]),
        })
    stats = ladder_stats(conn, cfg.days)
    for row in stats["per_day"]:
        row["rate"] = _pct(row["success_rate"])
    stats["completion_pct"] = _pct(stats["completion_rate"])
    return {
        "ladder": ladder,
        "days": days,
        "today_pick": today_pick,
        "progress": progress,
        "history": history,
        "stats": stats,
        "total_days": cfg.days,
    }


@bp.route("/ladder-picks")
@bp.route("/ladder")
def ladder_page():
    premium, logged_in, admin = _viewer()
    try:
        conn = store.connect()
        try:
            ctx = page_context(conn, premium)
            manual_post = social.manual_post_text(conn) if admin else None
        finally:
            conn.close()
    except Exception:
        logger.exception("ladder: page data failed")
        ctx = {"ladder": None, "days": [], "today_pick": None, "progress": [], "history": [],
               "stats": None, "total_days": 10}
        manual_post = None
    html = render_template(
        "ladder_picks.html", is_premium=premium, is_logged_in=logged_in, manual_post=manual_post, **ctx
    )
    resp = make_response(html)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
