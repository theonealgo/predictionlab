"""Bottom-of-results advertising image: ML / Spread / Total for Last Night + Last 7.

Used by team-sport results pages (and specialty sports that already render
daily-tally blocks). Builds a 9:16 JPEG matching the picks-page share chrome.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any


def _parse_pct(text: str) -> str:
    t = (text or "").strip()
    if not t or t in ("—", "-", "N/A", "n/a"):
        return "—"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", t)
    if not m:
        return "—"
    return f"{m.group(1)}%"


def _parse_rec(text: str) -> str:
    t = (text or "").strip()
    if not t or t in ("—", "-", "N/A", "n/a"):
        return "—"
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?", t)
    if not m:
        return "—"
    if m.group(3):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"{m.group(1)}-{m.group(2)}"


def _market_from_tally_dict(bucket: dict | None, *, pushes_key: str = "pushes") -> dict[str, str]:
    if not isinstance(bucket, dict):
        return {"acc": "—", "record": "—"}
    total = int(bucket.get("total") or 0)
    if total <= 0:
        return {"acc": "—", "record": "—"}
    correct = int(bucket.get("correct") or 0)
    pushes = int(bucket.get(pushes_key) or 0)
    losses = max(total - correct, 0)
    rec = f"{correct}-{losses}"
    if pushes:
        rec = f"{correct}-{losses}-{pushes}"
    acc = bucket.get("accuracy")
    if acc is None and total:
        acc = round(100.0 * correct / total, 1)
    return {
        "acc": f"{acc}%" if acc is not None else "—",
        "record": rec,
    }


def payload_from_tallies(
    *,
    sport_name: str,
    daily_tally: dict | None,
    daily_tally_date: str | None,
    daily_tally_games: int | None,
    weekly_tally: dict | None,
    weekly_tally_date_range: str | None,
    weekly_tally_games: int | None,
) -> dict[str, Any] | None:
    """Build share payload from compute_daily_model_tally / range tallies."""
    sport_name = (sport_name or "").strip() or "Results"
    if not daily_tally and not weekly_tally:
        return None

    def _window(tally, date_label, games):
        ens = (tally or {}).get("ensemble") or {}
        return {
            "label": date_label or "",
            "games": int(games or (tally or {}).get("games") or 0),
            "ml": _market_from_tally_dict(ens),
            "spread": _market_from_tally_dict((tally or {}).get("spread")),
            "total": _market_from_tally_dict((tally or {}).get("total_ou")),
        }

    return {
        "type": "results-summary",
        "sport_name": sport_name,
        "last_night": _window(daily_tally, daily_tally_date, daily_tally_games),
        "last_7": _window(weekly_tally, weekly_tally_date_range, weekly_tally_games),
    }


def _empty_window() -> dict[str, Any]:
    return {
        "label": "",
        "games": 0,
        "ml": {"acc": "—", "record": "—"},
        "spread": {"acc": "—", "record": "—"},
        "total": {"acc": "—", "record": "—"},
    }


def _extract_team_daily_tally_block(blob: str) -> dict[str, Any] | None:
    h2 = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", blob, flags=re.I)
    if not h2:
        return None
    title = re.sub(r"<[^>]+>", " ", h2.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    kind = None
    if re.search(r"Last\s+Night", title, re.I):
        kind = "last_night"
    elif re.search(r"Last\s+7", title, re.I):
        kind = "last_7"
    else:
        return None
    date_m = re.search(
        r"(?:—|-)\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:\s+to\s+[0-9]{4}-[0-9]{2}-[0-9]{2})?)",
        title,
        re.I,
    )
    games_m = re.search(r"\((\d+)\s+games?\)", title, re.I)
    ml_acc, ml_rec = "—", "—"
    m = re.search(
        r"Sharp Consensus</div>\s*"
        r'<div class="daily-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="daily-rec">([^<]*)</div>',
        blob,
        flags=re.I,
    )
    if m:
        ml_acc, ml_rec = _parse_pct(m.group(1)), _parse_rec(m.group(2))
    sp_acc, sp_rec = "—", "—"
    m = re.search(
        r"(?:📈\s*)?Spread</div>\s*"
        r'<div class="daily-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="daily-rec">([^<]*)</div>',
        blob,
        flags=re.I,
    )
    if m:
        sp_acc, sp_rec = _parse_pct(m.group(1)), _parse_rec(m.group(2))
    ou_acc, ou_rec = "—", "—"
    m = re.search(
        r"(?:🎲\s*)?(?:Over/Under|Total|Totals)</div>\s*"
        r'<div class="daily-acc"[^>]*>([^<]*)</div>\s*'
        r'<div class="daily-rec">([^<]*)</div>',
        blob,
        flags=re.I,
    )
    if m:
        ou_acc, ou_rec = _parse_pct(m.group(1)), _parse_rec(m.group(2))
    return {
        "kind": kind,
        "label": (date_m.group(1) if date_m else "").strip(),
        "games": int(games_m.group(1)) if games_m else 0,
        "ml": {"acc": ml_acc, "record": ml_rec},
        "spread": {"acc": sp_acc, "record": sp_rec},
        "total": {"acc": ou_acc, "record": ou_rec},
    }


def _extract_individual_tally_sections(html: str) -> dict[str, dict[str, Any]]:
    """Tennis/UFC-style <section class="tally ..."> Last Night / Last 7."""
    out: dict[str, dict[str, Any]] = {}
    for m in re.finditer(
        r'<section class="tally[^"]*"[^>]*>([\s\S]*?)</section>',
        html or "",
        flags=re.I,
    ):
        blob = m.group(1)
        h2 = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", blob, flags=re.I)
        if not h2:
            continue
        title = re.sub(r"<[^>]+>", " ", h2.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if re.search(r"Last\s+Night", title, re.I):
            kind = "last_night"
        elif re.search(r"Last\s+7", title, re.I):
            kind = "last_7"
        else:
            continue
        date_m = re.search(r"(20\d{2}-\d{2}-\d{2})", title)
        games_m = re.search(r"(\d+)\s+graded", title, re.I) or re.search(
            r"(\d+)\s+decisions", title, re.I
        )
        ml_acc, ml_rec = "—", "—"
        cm = re.search(
            r'<div class="mlabel">\s*Sharp Consensus\s*</div>\s*'
            r'<div class="acc[^"]*">([^<]*)</div>\s*'
            r'<div class="rec">([^<]*)</div>',
            blob,
            flags=re.I,
        )
        if cm:
            ml_acc = _parse_pct(cm.group(1))
            # "0-2 · -2.0u · 2 graded" → take W-L only
            ml_rec = _parse_rec(cm.group(2).split("·")[0])
        out[kind] = {
            "label": date_m.group(1) if date_m else "",
            "games": int(games_m.group(1)) if games_m else 0,
            "ml": {"acc": ml_acc, "record": ml_rec},
            "spread": {"acc": "—", "record": "—"},
            "total": {"acc": "—", "record": "—"},
        }
    return out


def payload_from_results_html(html: str, sport_name: str) -> dict[str, Any] | None:
    """Parse Last Night / Last 7 tally blocks already on the results page."""
    html = html or ""
    sport_name = (sport_name or "").strip() or "Results"
    last_night = last_7 = None

    blocks = list(
        re.finditer(
            r'<div class="daily-tally"[^>]*>([\s\S]*?)(?=<div class="daily-tally"|'
            r'<div class="date-nav"|<div class="share-strip"|</main>|$)',
            html,
            flags=re.I,
        )
    )
    for b in blocks:
        parsed = _extract_team_daily_tally_block(b.group(1))
        if not parsed:
            continue
        if parsed["kind"] == "last_night" and last_night is None:
            last_night = parsed
        elif parsed["kind"] == "last_7" and last_7 is None:
            last_7 = parsed

    if not last_night and not last_7:
        indiv = _extract_individual_tally_sections(html)
        last_night = indiv.get("last_night")
        last_7 = indiv.get("last_7")

    if not last_night and not last_7:
        return None
    empty = _empty_window()
    return {
        "type": "results-summary",
        "sport_name": sport_name,
        "last_night": {k: v for k, v in (last_night or empty).items() if k != "kind"},
        "last_7": {k: v for k, v in (last_7 or empty).items() if k != "kind"},
    }


def _get_font(size: int, bold: bool = True):
    from PIL import ImageFont

    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _pretty_date_label(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )

    def _one(iso: str) -> str:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso.strip())
        if not m:
            return iso.strip()
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return f"{months[mo - 1]} {d}"
        except Exception:
            return iso.strip()

    span = re.match(
        r"(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        raw,
        flags=re.I,
    )
    if span:
        return f"{_one(span.group(1))} – {_one(span.group(2))}"
    one = re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw)
    if one:
        return _one(raw)
    return raw


def render_results_summary_share_image(
    payload: dict,
    fmt: str = "jpg",
    *,
    max_width: int | None = None,
) -> tuple[bytes | None, str | None]:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None, None
    if not payload or payload.get("type") != "results-summary":
        return None, None

    width = 1080
    pad = 56
    title_font = _get_font(64, True)
    sub_font = _get_font(28, False)
    section_font = _get_font(36, True)
    meta_font = _get_font(26, False)
    label_font = _get_font(28, False)
    rec_font = _get_font(32, True)
    acc_font = _get_font(36, True)
    col_font = _get_font(22, True)
    foot_font = _get_font(24, False)

    # Measure first, then crop the canvas to the content.
    box_h = 292
    gap = 28
    header_h = 168
    footer_h = 72
    height = header_h + box_h * 2 + gap + footer_h + pad
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    sport = str(payload.get("sport_name") or "Results")
    y = 48
    draw.text((pad, y), f"{sport} Results", fill=(15, 23, 42), font=title_font)
    y += 78
    draw.text((pad, y), "predictionlab.io", fill=(0, 82, 155), font=sub_font)
    y += 52

    def _draw_window(title: str, window: dict | None) -> None:
        nonlocal y
        window = window or {}
        games = int(window.get("games") or 0)
        pretty = _pretty_date_label(str(window.get("label") or ""))
        if games == 1:
            meta = f"{pretty} · 1 game" if pretty else "1 game"
        elif games:
            meta = f"{pretty} · {games} games" if pretty else f"{games} games"
        else:
            meta = pretty

        box_top = y
        draw.rounded_rectangle(
            (pad, box_top, width - pad, box_top + box_h),
            radius=20,
            outline=(226, 232, 240),
            width=2,
            fill=(248, 250, 252),
        )
        draw.text((pad + 32, box_top + 22), title, fill=(15, 23, 42), font=section_font)
        if meta:
            draw.text((pad + 32, box_top + 68), meta, fill=(100, 116, 139), font=meta_font)

        col_rec_x = 620
        col_acc_x = width - pad - 36
        draw.text((pad + 32, box_top + 110), "MARKET", fill=(148, 163, 184), font=col_font)
        rec_lab = "REC"
        rec_bb = draw.textbbox((0, 0), rec_lab, font=col_font)
        draw.text((col_rec_x, box_top + 110), rec_lab, fill=(148, 163, 184), font=col_font)
        acc_lab = "ACC"
        acc_bb = draw.textbbox((0, 0), acc_lab, font=col_font)
        draw.text(
            (col_acc_x - (acc_bb[2] - acc_bb[0]), box_top + 110),
            acc_lab,
            fill=(148, 163, 184),
            font=col_font,
        )
        rows = (
            ("Moneyline", window.get("ml") or {}),
            ("Spread", window.get("spread") or {}),
            ("Total", window.get("total") or {}),
        )
        row_y = box_top + 148
        for name, mkt in rows:
            acc = str((mkt or {}).get("acc") or "—")
            rec = str((mkt or {}).get("record") or "—")
            draw.text((pad + 32, row_y), name, fill=(51, 65, 85), font=label_font)
            draw.text((col_rec_x, row_y - 2), rec, fill=(15, 23, 42), font=rec_font)
            acc_w = draw.textbbox((0, 0), acc, font=acc_font)
            draw.text(
                (col_acc_x - (acc_w[2] - acc_w[0]), row_y - 4),
                acc,
                fill=(15, 23, 42),
                font=acc_font,
            )
            row_y += 44
        y = box_top + box_h + gap

    _draw_window("Last Night", payload.get("last_night"))
    _draw_window("Last 7 Days", payload.get("last_7"))
    draw.text(
        (pad, y + 4),
        "Sharp Consensus  ·  moneyline, spread, total",
        fill=(148, 163, 184),
        font=foot_font,
    )

    # Always a 1080x1920 (9:16) canvas, the same as the Predictions image.
    # The content block sits in the vertical middle of the canvas.
    _canvas_h = 1920
    if height < _canvas_h:
        _canvas = Image.new("RGB", (width, _canvas_h), color=(255, 255, 255))
        _canvas.paste(image, (0, (_canvas_h - height) // 2))
        image, height = _canvas, _canvas_h
    try:
        mw = int(max_width) if max_width is not None else None
    except (TypeError, ValueError):
        mw = None
    if mw and 240 <= mw < width:
        new_h = max(1, int(round(height * (mw / float(width)))))
        try:
            _resample = Image.Resampling.LANCZOS
        except AttributeError:
            _resample = Image.LANCZOS
        image = image.resize((mw, new_h), _resample)

    out = io.BytesIO()
    out_fmt = "JPEG" if fmt in ("jpg", "jpeg") else "PNG"
    if out_fmt == "JPEG":
        q = 82 if (mw and mw < width) else 93
        image.save(out, format=out_fmt, quality=q, optimize=True, subsampling=0)
        mime = "image/jpeg"
    else:
        image.save(out, format=out_fmt, optimize=True)
        mime = "image/png"
    out.seek(0)
    return out.getvalue(), mime


_SOCIAL_EXPORT_CSS = """
.social-export-wrap{max-width:960px;margin:28px auto 8px;padding:0 12px;}
.social-export-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:12px;}
.social-export-title{font-size:0.9em;font-weight:800;color:#0f172a;letter-spacing:0.2px;}
.social-export-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.social-export-btn{border:1px solid #00529B;background:#fff;color:#00529B;border-radius:999px;padding:6px 12px;font-size:0.76em;font-weight:800;cursor:pointer;text-decoration:none;display:inline-flex;}
.social-export-btn.primary{background:#00529B;color:#fff;}
.social-image-link{display:block;max-width:400px;margin:0 auto;}
.social-image-link img{width:100%;height:auto;object-fit:contain;border-radius:16px;border:1px solid #e2e8f0;box-shadow:0 8px 24px rgba(15,23,42,0.08);display:block;background:#fff;}
.cfl-results-share{display:none!important;}
"""


def results_share_wrap_html(
    *,
    sport_name: str,
    share_src: str,
    view_url: str,
) -> str:
    sport = sport_name or "Results"
    preview = share_src  # full 1080x1920 image, same as the Predictions image
    return (
        f'<div class="social-export-wrap" data-results-share="1">'
        f'<div class="social-export-head">'
        f'<div class="social-export-title">{sport} Results Image</div>'
        f'<div class="social-export-actions">'
        f'<a class="social-export-btn" href="{share_src}" download="predictionlab-results.jpg">Download image</a>'
        f'<a class="social-export-btn primary" href="{view_url}" target="_blank" rel="nofollow noopener">Open fullscreen</a>'
        f"</div></div>"
        f'<a class="social-image-link" href="{view_url}" target="_blank" rel="nofollow noopener" '
        f'aria-label="Open {sport} results share image">'
        f'<img src="{preview}" alt="{sport} results share image" width="1080" height="1920" loading="lazy" decoding="async">'
        f"</a></div>"
    )



def _insert_after_share_strip(html: str, wrap_html: str) -> str:
    """Share bar first, results image second."""
    marker = '<div class="share-strip"'
    start = html.find(marker)
    if start < 0:
        return html + wrap_html
    pos = html.find(">", start)
    if pos < 0:
        return html
    pos += 1
    depth = 1
    while pos < len(html) and depth:
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close < 0:
            return html + wrap_html
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + len("</div>")
    return html[:pos] + "\n" + wrap_html + html[pos:]


def _full_share_strip() -> str:
    """Standard Share on social media strip (label + icons), same as the cards page."""
    from urllib.parse import quote

    try:
        from flask import request

        path = request.path or ""
    except Exception:
        path = ""
    page = quote("https://predictionlab.io" + path, safe="")
    links = (
        ("https://x.com/intent/post?url=" + page, "Share on X", "x.svg", "X"),
        ("https://www.facebook.com/sharer/sharer.php?u=" + page, "Share on Facebook", "facebook.svg", "Facebook"),
        ("https://instagram.com/predictionlab.io", "Instagram", "instagram.svg", "Instagram"),
        ("https://predictionlab.io", "TikTok", "tiktok.svg", "TikTok"),
        ("https://www.linkedin.com/sharing/share-offsite/?url=" + page, "Share on LinkedIn", "linkedin.svg", "LinkedIn"),
        ("https://www.reddit.com/submit?url=" + page, "Share on Reddit", "reddit.svg", "Reddit"),
        ("https://www.tumblr.com/widgets/share/tool?canonicalUrl=" + page, "Share on Tumblr", "tumblr.svg", "Tumblr"),
        ("https://api.whatsapp.com/send?text=" + page, "Share on WhatsApp", "whatsapp.svg", "WhatsApp"),
        ("https://telegram.me/share/url?url=" + page, "Share on Telegram", "telegram.svg", "Telegram"),
    )
    icons = "".join(
        f'<a class="share-icon" href="{href}" target="_blank" rel="noopener" aria-label="{label}">'
        f'<img src="/static/icons/social/{icon}" alt="{alt}"></a>'
        for href, label, icon, alt in links
    )
    return (
        '<style id="pl-share-strip-css">.share-strip{max-width:1200px;margin:12px auto 10px;padding:10px 16px;display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;background:rgba(244,247,249,.7);border:1px solid rgba(15,23,42,.1);border-radius:12px;box-sizing:border-box}.share-strip .share-strip-label{font-size:.82em;font-weight:800;color:#0f172a}.share-strip .share-icons{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.share-strip .share-icon{width:30px!important;height:30px!important;display:inline-flex!important;align-items:center;justify-content:center;border-radius:999px;border:1px solid rgba(15,23,42,.14);background:#fff}.share-strip .share-icon img{width:16px!important;height:16px!important;display:block}</style>'
        '<div class="share-strip"><span class="share-strip-label">Share on social media</span>'
        f'<div class="share-icons">{icons}</div></div>'
    )



def inject_results_share_block(html: str, wrap_html: str) -> str:
    """Insert results share chrome before the social share-strip; replace wrong picks image."""
    if not html or not wrap_html:
        return html
    # Drop specialty figure / wrong Predictions Image on results pages
    html = re.sub(
        r'<figure class="cfl-results-share"[^>]*>[\s\S]*?</figure>',
        "",
        html,
        flags=re.I,
    )
    # Cut any existing social-export-wrap that sits above the share-strip
    strip_i = html.find('class="share-strip"')
    if strip_i >= 0:
        export_i = html.rfind('class="social-export-wrap"', 0, strip_i)
        if export_i >= 0:
            start = html.rfind("<div", 0, export_i + 1)
            div_open = html.find("<div", strip_i - 20, strip_i + 5)
            # Prefer the exact share-strip opener
            m = re.search(r'<div class="share-strip"', html[max(0, strip_i - 40) :])
            end = (max(0, strip_i - 40) + m.start()) if m else strip_i
            if start >= 0 and end > start:
                html = html[:start] + html[end:]
    # Ensure CSS once
    if 'id="results-share-export-css"' not in html:
        style = f'<style id="results-share-export-css">{_SOCIAL_EXPORT_CSS}</style>'
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", style + "</head>", html, count=1, flags=re.I)
        else:
            html = style + html
    if 'data-results-share="1"' in html:
        return html
    if 'class="share-strip"' in html:
        return _insert_after_share_strip(html, wrap_html)
    share = '<div class="share-strip"><span class="share-strip-label">Share on social media</span></div>'
    if "</main>" in html:
        return html.replace("</main>", share + "\n" + wrap_html + "\n</main>", 1)
        return html + share + wrap_html


def recover_results_share_payload(token: str, sport_name: str = "") -> dict | None:
    """Rebuild a results image after its token file expired but the page still links it."""
    token = (token or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    root = Path(__file__).resolve().parent / ".cache"
    if not root.is_dir():
        return None
    for path in root.glob("served_*.html"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if token not in text:
            continue
        payload = payload_from_results_html(text, sport_name or "Results")
        if payload:
            return payload
    return None
