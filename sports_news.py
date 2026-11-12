"""Original sports reporting from structured, verified final-game facts."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import logging
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MAX_DAILY = 3
STORE = Path(__file__).resolve().parent/"data/sports_news_posts.json"
SPORTS = {
    "NBA": ("basketball/nba", "nba", "NBA", "points"),
    "NHL": ("hockey/nhl", "nhl", "NHL", "goals"),
    "NFL": ("football/nfl", "nfl", "NFL", "points"),
    "NCAAF": ("football/college-football", "college-football", "college football", "points"),
    "MLB": ("baseball/mlb", "mlb", "MLB", "runs"),
    "WNBA": ("basketball/wnba", "wnba", "WNBA", "points"),
    "NCAAB": ("basketball/mens-college-basketball", "mens-college-basketball", "men's college basketball", "points"),
    "NCAAW": ("basketball/womens-college-basketball", "womens-college-basketball", "women's college basketball", "points"),
}


def _stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def final_facts(sport, payload, day, now=None):
    now = now or datetime.now(timezone.utc)
    facts = {}
    for event in payload.get("events") or []:
        identity = str(event.get("id") or "")
        comp = (event.get("competitions") or [{}])[0]
        status = ((comp.get("status") or event.get("status") or {}).get("type") or {})
        start = _stamp(comp.get("date") or event.get("date"))
        if not identity.isdigit() or not start or start >= now or start.astimezone(ET).date().isoformat() != day \
                or status.get("completed") is not True or "FINAL" not in str(status.get("name") or "").upper():
            continue
        sides = comp.get("competitors") or []
        home = next((row for row in sides if row.get("homeAway") == "home"), None)
        away = next((row for row in sides if row.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        try:
            hs, aws = float(home["score"]), float(away["score"])
            if any(not math.isfinite(value) or value < 0 or not value.is_integer() for value in (hs, aws)):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        hname = str((home.get("team") or {}).get("displayName") or "").strip()
        aname = str((away.get("team") or {}).get("displayName") or "").strip()
        if not hname or not aname:
            continue
        facts[identity] = {"event_id": identity, "sport": sport, "date": day,
                           "home": hname, "away": aname, "home_score": int(hs), "away_score": int(aws),
                           "venue": str((comp.get("venue") or {}).get("fullName") or "").strip(),
                           "neutral": bool(comp.get("neutralSite")),
                           "overtime": "overtime" in str(status.get("detail") or "").lower()
                                       or "OT" in str(status.get("shortDetail") or ""),
                           "source_url": f"https://www.espn.com/{SPORTS[sport][1]}/boxscore/_/gameId/{identity}"}
    return sorted(facts.values(), key=lambda row: row["event_id"])


def build_article(sport, facts, published_at=None):
    if not facts or sport not in SPORTS:
        raise ValueError("Verified final facts are required")
    published_at = published_at or datetime.now(timezone.utc)
    day = facts[0]["date"]
    if any(row["sport"] != sport or row["date"] != day for row in facts):
        raise ValueError("A recap must use one sport and one game date")
    date_label = datetime.fromisoformat(day).strftime("%A, %B %d").replace(" 0", " ")
    label, unit = SPORTS[sport][2], SPORTS[sport][3]
    leader = max(facts, key=lambda row: abs(row["home_score"]-row["away_score"]))
    winner = leader["home"] if leader["home_score"] > leader["away_score"] else leader["away"]
    margin = abs(leader["home_score"]-leader["away_score"])
    title = (f"{winner} wins by {margin}: {date_label}'s {label} roundup"
             if margin else f"{label} final-score recap: {date_label}")
    high = max(facts, key=lambda row: max(row["home_score"], row["away_score"]))
    high_team = high["home"] if high["home_score"] >= high["away_score"] else high["away"]
    high_score = max(high["home_score"], high["away_score"])
    body = [
        f"A {margin}-{unit.rstrip('s')} victory for {winner} produced the largest winning margin "
        f"among the {len(facts)} confirmed {label} finals from {date_label}."
        if margin else f"The confirmed {label} games from {date_label} finished level on the scoreboard.",
        f"{high_team} recorded the highest single-team score in this set of completed games, finishing with "
        f"{high_score} {unit}. The results below cover confirmed finals only; games that were still in progress "
        f"when the scores were checked are not included.",
    ]
    for row in sorted(facts, key=lambda row: -abs(row["home_score"]-row["away_score"]))[:8]:
        hs, aws = row["home_score"], row["away_score"]
        if hs == aws:
            text = f"{row['away']} and {row['home']} finished tied at {hs}–{aws}."
        else:
            win, lose = (row["home"], row["away"]) if hs > aws else (row["away"], row["home"])
            win_score, lose_score = (hs, aws) if hs > aws else (aws, hs)
            text = f"{win} defeated {lose} {win_score}–{lose_score}, a difference of {win_score-lose_score} {unit}."
        if row["venue"]:
            text += f" The game was played at {row['venue']}."
        if row["overtime"]:
            text += " The final was decided after overtime."
        body.append(text)
    total = sum(row["home_score"]+row["away_score"] for row in facts)
    home_wins = sum(row["home_score"] > row["away_score"] and not row["neutral"] for row in facts)
    away_wins = sum(row["away_score"] > row["home_score"] and not row["neutral"] for row in facts)
    body.append(f"The {len(facts)} finals produced {total} {unit} between the teams. "
                f"Among games not listed at a neutral venue, home teams won {home_wins} and away teams won {away_wins}. "
                "Individual final box scores are linked below for the complete game details.")
    slug = f"{sport.lower()}-daily-recap-{day}"
    canonical = f"/blog/{sport.lower()}/{slug}"
    timestamp = published_at.isoformat()
    return {"slug": slug, "title": title, "sport_tag": sport, "kind": "sports_news", "status": "draft",
            "date": published_at.astimezone(ET).date().isoformat(), "game_date": day,
            "created_date": published_at.astimezone(ET).date().isoformat(),
            "date_published": timestamp, "date_modified": timestamp,
            "canonical_path": canonical, "article_url": canonical,
            "excerpt": body[0], "meta_description": body[0][:155],
            "body": body, "sections": {"intro": body, "models": [], "markets": {}, "game_info": {}},
            "image": "/static/pl-logo.svg", "author": "Prediction Lab",
            "news_items": [{"sport": sport, "topic": f"{row['away']} at {row['home']} — final box score",
                            "source": "ESPN", "url": row["source_url"]} for row in facts],
            "provenance": {"facts": facts, "sha256": hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
                           "checked_at": timestamp, "method": "Original prose from structured final-game facts"},
            "social_drafts": {"status": "draft", "ready_for_delivery": False,
                              "facebook": title+"\n\n"+body[0]+"\n\nhttps://predictionlab.io"+canonical,
                              "instagram": title+"\n\n"+body[0]+"\n\nRead more at predictionlab.io.",
                              "requires": "Article review and explicit Meta publishing authorization"}}


def save_articles(path, articles, publish_local=False):
    from sports_news_store import update

    def change(document):
        existing = document["posts"]
        by_slug = {post["slug"]: post for post in existing}
        for post in articles[:MAX_DAILY]:
            validate_article(post)
            same_day = sum(item.get("created_date", item.get("date")) == post.get("created_date", post["date"])
                           for item in by_slug.values())
            if post["slug"] not in by_slug and same_day >= MAX_DAILY:
                continue
            if post["slug"] in by_slug:
                if publish_local and by_slug[post["slug"]].get("status") == "draft":
                    by_slug[post["slug"]]["status"] = "published"
                elif by_slug[post["slug"]].get("status") == "draft" and (
                        by_slug[post["slug"]].get("provenance", {}).get("sha256")
                        != post["provenance"]["sha256"]):
                    by_slug[post["slug"]] = {**post, "status": "draft"}
                continue
            post = {**post, "status": "published" if publish_local else "draft"}
            by_slug[post["slug"]] = post
        document["posts"] = list(by_slug.values())
        return document["posts"]

    return update(change, path)


def validate_article(post):
    if post.get("kind") != "sports_news" or post.get("sport_tag") not in SPORTS:
        raise ValueError("Not a supported sports-news article")
    body = post.get("body")
    if not isinstance(body, list) or not body or not all(isinstance(part, str) for part in body):
        raise ValueError("Plain-text original paragraphs are required")
    if re.search(r"\b(?:betting|sportsbook|parlay|moneyline|odds)\b", " ".join(body), re.I):
        raise ValueError("Sports news cannot contain gambling recommendations")
    facts = (post.get("provenance") or {}).get("facts") or []
    expected = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    if not facts or expected != post["provenance"].get("sha256"):
        raise ValueError("Fact provenance is missing or changed")
    slug = str(post.get("slug") or "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid article slug")
    if post.get("canonical_path") != f"/blog/{post['sport_tag'].lower()}/{slug}":
        raise ValueError("Article route is not local")
    ids = set()
    sources = set()
    for fact in facts:
        identity = str(fact.get("event_id") or "")
        if not identity.isdigit() or identity in ids or fact.get("sport") != post["sport_tag"] \
                or fact.get("date") != post.get("game_date"):
            raise ValueError("Game facts are inconsistent")
        ids.add(identity)
        expected_source = f"https://www.espn.com/{SPORTS[post['sport_tag']][1]}/boxscore/_/gameId/{identity}"
        if fact.get("source_url") != expected_source:
            raise ValueError("A final-score citation is not verified")
        sources.add(expected_source)
        for key in ("home_score", "away_score"):
            value = fact.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Final scores must be nonnegative integers")
    for item in post.get("news_items") or []:
        url = urlsplit(item.get("url") or "")
        if url.scheme != "https" or url.hostname not in ("espn.com", "www.espn.com"):
            raise ValueError("An unverified source link cannot be published")
    if {item.get("url") for item in post.get("news_items") or []} != sources:
        raise ValueError("Every verified final needs its source citation")
    sections = post.get("sections") or {}
    if sections.get("models") or sections.get("markets"):
        raise ValueError("News is not a model/market preview")
    return True


def published_posts(path=None):
    from sports_news_store import load
    try:
        posts = load(path)["posts"]
    except (ValueError, OSError):
        logging.getLogger(__name__).exception("Published news store unavailable")
        return []
    safe = []
    for post in posts:
        if post.get("status") != "published" or post.get("kind") != "sports_news":
            continue
        try:
            validate_article(post)
        except ValueError:
            logging.getLogger(__name__).warning("An invalid news article was withheld")
            continue
        safe.append({**post, "image": f"/blog/news-assets/{post['slug']}/facebook.png",
                     "image_is_logo": False})
    return sorted(safe,
                  key=lambda post: post.get("date_published") or "", reverse=True)


def generate(day=None, get=None, now=None, report=None):
    now = now or datetime.now(timezone.utc)
    day = day or (now.astimezone(ET).date()-timedelta(days=1)).isoformat()
    parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    if parsed_day.isoformat() != day or parsed_day >= now.astimezone(ET).date():
        raise ValueError("Only completed past calendar days can be recapped")
    def fetch(url):
        import requests
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        return response.json()
    get = get or fetch
    articles = []
    for sport, (endpoint, _web, _label, _unit) in SPORTS.items():
        params = {"dates": day.replace("-", ""), "limit": 1000}
        if sport in ("NCAAB", "NCAAW", "NCAAF"):
            params["groups"] = 80 if sport == "NCAAF" else 50
        try:
            payload = get(f"https://site.api.espn.com/apis/site/v2/sports/{endpoint}/scoreboard?"+urlencode(params))
        except Exception:
            if report is not None:
                report.append({"sport": sport, "status": "unavailable", "reason": "Scoreboard request failed"})
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            if report is not None:
                report.append({"sport": sport, "status": "unavailable", "reason": "Invalid scoreboard response"})
            continue
        unfinished = []
        for event in payload["events"]:
            comp = (event.get("competitions") or [{}])[0]
            start = _stamp(comp.get("date") or event.get("date"))
            kind = ((comp.get("status") or event.get("status") or {}).get("type") or {})
            if start and start.astimezone(ET).date().isoformat() == day and (
                    kind.get("completed") is not True and kind.get("name") not in
                    ("STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_POSTPONED")):
                unfinished.append(event.get("id"))
        if unfinished:
            if report is not None:
                report.append({"sport": sport, "status": "pending", "reason": "The slate still has unfinished games"})
            continue
        facts = final_facts(sport, payload, day, now)
        expected_finals = set()
        for event in payload["events"]:
            comp = (event.get("competitions") or [{}])[0]
            start = _stamp(comp.get("date") or event.get("date"))
            kind = ((comp.get("status") or event.get("status") or {}).get("type") or {})
            if start and start.astimezone(ET).date().isoformat() == day and "FINAL" in str(kind.get("name", "")):
                expected_finals.add(str(event.get("id")))
        if {fact["event_id"] for fact in facts} != expected_finals:
            if report is not None:
                report.append({"sport": sport, "status": "unavailable",
                               "reason": "Some final-score records could not be verified"})
            continue
        if report is not None:
            report.append({"sport": sport, "status": "generated" if facts else "empty",
                           "games": len(facts)})
        if facts:
            articles.append(build_article(sport, facts, now))
        if len(articles) == MAX_DAILY:
            break
    return articles


def queue_daily_review():
    """Compatibility entry point; generation is owned by the daily worker."""
    return None
