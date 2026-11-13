"""Request-local windows for the original sport-page renderer."""
from contextlib import contextmanager
from contextvars import ContextVar

_WINDOW = ContextVar("pl_card_window", default=None)


def active():
    return _WINDOW.get() is not None


@contextmanager
def window(offset, limit):
    state = {"offset": offset, "limit": limit, "total": None, "nextOffset": None}
    token = _WINDOW.set(state)
    try:
        yield state
    finally:
        _WINDOW.reset(token)


def select(predictions, grouped, dates, default_date):
    """Run before per-card preparation; keep the original objects and renderer."""
    state = _WINDOW.get()
    if state is None:
        return predictions, grouped, dates, default_date
    rows = [pred for day in dates for pred in grouped.get(day, [])]
    # Match the API's established upcoming-first ordering, stably within dates.
    rows.sort(key=lambda pred: (pred.get("home_score") is not None,
                               str(pred.get("game_date") or "") if pred.get("home_score") is None else ""))
    state["total"] = len(rows)
    end = min(len(rows), state["offset"] + state["limit"])
    chosen = rows[state["offset"]:end]
    state["nextOffset"] = end if end < len(rows) else None
    identities = {id(pred) for pred in chosen}
    selected = {day: [pred for pred in grouped.get(day, []) if id(pred) in identities] for day in dates}
    selected = {day: items for day, items in selected.items() if items}
    dates = [day for day in dates if day in selected]
    return chosen, selected, dates, default_date if default_date in selected else next(iter(dates), default_date)
