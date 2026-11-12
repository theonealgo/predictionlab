"""Presentation-only date guards; never change stored predictions or grades."""
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from html import unescape

_RESULT = re.compile(r'\bdata-result=["\']([^"\']*)', re.I)
_TIME = re.compile(r'class=["\'][^"\']*\bgame-time\b[^"\']*["\'][^>]*>([\s\S]*?)</', re.I)


def current_payload(payload, day=None):
    day = day or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    shown, suppressed = [], 0
    for original in payload.get("cards") or []:
        card = dict(original)
        html = card.get("html") or ""
        result = _RESULT.search(html[:html.find(">")+1])
        time = _TIME.search(html)
        status = unescape(re.sub(r"<[^>]+>", " ", time[1])) if time else ""
        final = (card.get("isFinal") is True or bool(result and result[1].lower() in ("win", "loss", "push", "void", "final"))
                 or bool(re.search(r"\b(?:FINAL|COMPLETED)\b", status, re.I)))
        card["isFinal"] = final
        date = str(card.get("date") or "")[:10]
        if date and date < day and not final:
            suppressed += 1
            continue
        shown.append(card)
    return {**payload, "cards": shown, "total": len(shown), "as_of_date": day,
            "suppressed_stale_predictions": suppressed}
