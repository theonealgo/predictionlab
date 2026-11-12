"""Public article URLs and summaries; stored slugs remain immutable identities."""
from datetime import datetime, timezone
import re
import unicodedata
from zoneinfo import ZoneInfo

SPORT_LABELS = {
    "NCAAF": "college football", "NCAAB": "men's college basketball",
    "NCAAW": "women's college basketball", "SOCCER": "soccer",
    "TENNIS": "tennis", "GOLF": "golf",
}


def slug_words(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    value = value.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _game_day(post):
    candidates = [post.get("game_date"), post.get("slug"), post.get("date")]
    for candidate in candidates:
        match = re.search(r"\d{4}-\d{2}-\d{2}", str(candidate or ""))
        if match:
            try:
                return datetime.strptime(match.group(), "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _subject(post):
    sport = str(post.get("sport_tag") or "").upper()
    label = SPORT_LABELS.get(sport, sport)
    facts = (post.get("provenance") or {}).get("facts") or []
    if post.get("kind") == "sports_news" and facts:
        leader = max(facts, key=lambda fact: abs(fact["home_score"] - fact["away_score"]))
        winner = leader["home"] if leader["home_score"] > leader["away_score"] else leader["away"]
        margin = abs(leader["home_score"] - leader["away_score"])
        unit = {"NHL": "goal", "MLB": "run"}.get(sport, "point")
        return sport, label, facts, winner, margin, unit
    return sport, label, [], None, None, None


def public_article_path(post):
    old = post.get("canonical_path") or post.get("article_url") or f"/blog/{post.get('slug', '')}"
    day = _game_day(post)
    sport, label, facts, winner, margin, unit = _subject(post)
    if not day or not re.fullmatch(r"[A-Z]+", sport):
        return old
    if facts:
        subject = (f"{winner} {margin} {unit} win {label} recap" if margin
                   else f"{label} final scores recap")
    else:
        info = (post.get("sections") or {}).get("game_info") or {}
        away, home = post.get("away") or info.get("away"), post.get("home") or info.get("home")
        if not away or not home:
            return old
        subject = f"{away} vs {home} game preview"
    return f"/blog/{sport.lower()}/{slug_words(subject)}-{day.isoformat()}"


def article_aliases(post):
    paths = [post.get("canonical_path"), post.get("article_url"), public_article_path(post)]
    aliases = {str(post.get("slug") or ""), str(post.get("legacy_slug") or "")}
    aliases.update(str(path).rstrip("/").rsplit("/", 1)[-1] for path in paths if path)
    return aliases - {""}


def with_public_url(post):
    path = public_article_path(post)
    return {**post, "canonical_path": path, "article_url": path}


def article_description(post):
    day = _game_day(post)
    if not day:
        return str(post.get("meta_description") or post.get("excerpt") or "")
    date_label = f"{day.strftime('%B')} {day.day}, {day.year}"
    sport, label, facts, winner, margin, unit = _subject(post)
    if facts:
        possessive = winner + ("'" if winner.endswith("s") else "'s")
        lead = f"{possessive} {margin}-{unit} win leads" if margin else "Final scores from"
        text = (f"{lead} our {date_label} {label} recap. "
                f"See all {len(facts)} final scores and game summaries.")
    else:
        info = (post.get("sections") or {}).get("game_info") or {}
        away, home = post.get("away") or info.get("away"), post.get("home") or info.get("home")
        if not away or not home:
            return str(post.get("meta_description") or post.get("excerpt") or "")
        text = (f"{away} vs {home}: {label} game preview for {date_label}, "
                "with matchup details and model win probabilities.")
    text = " ".join(text.split())
    return text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def publication_dates(post, now=None):
    """A scheduled game date is not evidence of a future publication time."""
    now = now or datetime.now(timezone.utc)
    values = []
    for field in ("date_published", "date_modified"):
        raw = post.get(field)
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if not stamp.tzinfo:
                stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
            values.append(stamp.isoformat() if stamp <= now else None)
        except (TypeError, ValueError):
            values.append(None)
    return tuple(values)
