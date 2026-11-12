"""Original brand artwork and manual social handoff. No external image or Meta API calls."""
from functools import lru_cache
from io import BytesIO
import json
import zipfile

from PIL import Image, ImageDraw, ImageFont

FORMATS = {"facebook": (1200, 630), "instagram": (1080, 1080)}


def social_copy(post):
    from blog_urls import public_article_path
    url = "https://predictionlab.io" + public_article_path(post)
    return {
        "facebook": f"{post['title']}\n\n{post['body'][0]}\n\n{url}",
        "instagram": f"{post['title']}\n\n{post['body'][0]}\n\n"
                     f"Full roundup: predictionlab.io/blog\n\n#{post['sport_tag']} #PredictionLab",
        "article_url": url,
        "status": "prepared_for_manual_posting",
        "delivered": False,
    }


def _font(size):
    # Pillow's bundled scalable font: no downloaded/newsroom artwork or OS font dependency.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrapped(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


@lru_cache(maxsize=24)
def _render(title, sport, date, platform):
    width, height = FORMATS[platform]
    image = Image.new("RGB", (width, height), "#071c35")
    draw = ImageDraw.Draw(image)
    margin = 66
    draw.rectangle((0, 0, width, 14), fill="#08aeef")
    draw.text((margin, 49), "PredictionLab", fill="#39bdff", font=_font(38))
    draw.text((margin, 123), f"{sport}  /  {date}", fill="#a4b9d2", font=_font(24))
    size = 58 if platform == "facebook" else 64
    while True:
        font = _font(size)
        lines = _wrapped(draw, title, font, width - 2 * margin)
        if len(lines) * (size + 15) <= height - 300 or size <= 30:
            break
        size -= 2
    y = 189 if platform == "facebook" else 250
    for line in lines:
        draw.text((margin, y), line, fill="white", font=font)
        y += size + 15
    draw.line((margin, height - 116, width - margin, height - 116), fill="#245174", width=2)
    draw.text((margin, height - 86), "THE FINAL-SCORE ROUNDUP", fill="#a4b9d2", font=_font(23))
    draw.text((margin, height - 49), "predictionlab.io/blog", fill="#39bdff", font=_font(22))
    stream = BytesIO()
    image.save(stream, "PNG", optimize=True)
    return stream.getvalue()


def artwork(post, platform):
    if platform not in FORMATS:
        raise ValueError("Unsupported image format")
    return _render(post["title"], post["sport_tag"], post["game_date"], platform)


def package(post):
    copies = social_copy(post)
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for platform in FORMATS:
            archive.writestr(f"{platform}.png", artwork(post, platform))
            archive.writestr(f"{platform}-caption.txt", copies[platform])
        archive.writestr("article-link.txt", copies["article_url"])
        archive.writestr("package.json", json.dumps({
            "slug": post["slug"], "article_status": post["status"],
            "social_status": "prepared_for_manual_posting", "delivered": False,
            "note": "Review the article on the live site before sharing its link. "
                    "Downloading this package does not publish to any social account.",
        }, indent=2))
    stream.seek(0)
    return stream
