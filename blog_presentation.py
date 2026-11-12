"""Public editorial copy only; never changes archived articles or forecast ledgers."""
from copy import deepcopy
import re

_COMMERCIAL = re.compile(
    r"\b(?:sportsbooks?|bookmakers?|wagers?|wagering|parlays?|bankroll|gambling|betting|bets?|odds|ROI)\b"
    r"|over\s*/\s*under|point spreads?|run[- ]line", re.I,
)


def editorial_text(value):
    text = str(value or "")
    text = re.sub(r"Prediction,\s*Odds\s*&\s*Preview", "Game Preview", text, flags=re.I)
    text = re.sub(r"(?:,\s*(?:and\s+)?|\band\s+)?not betting advice", "", text, flags=re.I)
    text = re.sub(r"\b(?:moneyline)\b", "game outcome", text, flags=re.I)
    text = re.sub(r"\bpicks\b", "predictions", text, flags=re.I)
    text = re.sub(r"\bpick\b", "prediction", text, flags=re.I)
    text = re.sub(r"\bnot locks\b", "not certainties", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def paragraphs(values):
    if isinstance(values, str):
        values = [values]
    return [clean for value in values or [] if (clean := editorial_text(value))
            and not _COMMERCIAL.search(clean)]


def public_blog_post(original):
    from sport_blog_previews import coerce_sections
    from blog_urls import with_public_url, article_description
    post = with_public_url(deepcopy(original))
    sections = coerce_sections(post)
    for key in ("title", "seo_title", "excerpt", "meta_description"):
        if key in post:
            post[key] = editorial_text(post[key])
    if _COMMERCIAL.search(post.get("title", "")):
        away, home = post.get("away"), post.get("home")
        post["title"] = f"{away} vs {home} — Game Preview" if away and home else "Prediction Lab Game Preview"
        post["seo_title"] = post["title"]
    post["body"] = paragraphs(post.get("body"))
    for key in ("intro", "why"):
        sections[key] = paragraphs(sections.get(key))
    takeaway = editorial_text(sections.get("takeaway"))
    sections["takeaway"] = "" if _COMMERCIAL.search(takeaway) else takeaway
    # Keep statistical score forecasts, not bookmaker prices or wagering lines.
    sections["markets"] = {key: value for key, value in sections.get("markets", {}).items()
                           if key in ("pl_proj", "xs_proj") and value}
    sections["disclaimer"] = (
        "" if post.get("kind") == "sports_news" else
        "Model probabilities are statistical estimates, not guarantees of a game's outcome."
    )
    if _COMMERCIAL.search(post.get("excerpt", "")):
        post["excerpt"] = " ".join(sections["intro"][:2]) or "Game information and statistical model forecasts."
    if _COMMERCIAL.search(post.get("meta_description", "")):
        post["meta_description"] = post["excerpt"][:155]
    post["meta_description"] = article_description(post)
    post["standfirst"] = post["meta_description"]
    for item in post.get("news_items") or []:
        item["topic"] = editorial_text(item.get("topic"))
    for item in post.get("related") or []:
        item["title"] = editorial_text(item.get("title"))
    return post
