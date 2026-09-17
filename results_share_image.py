"""Bottom-of-results advertising image: ML / Spread / Total for Last Night + Last 7.

Used by team-sport results pages (and specialty sports that already render
daily-tally blocks). Builds a 9:16 JPEG matching the picks-page share chrome.
"""
from __future__ import annotations

import io
import re
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


_MONTHS = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _short_date_label(raw: str) -> str:
    """Turn ISO date ranges into short labels that fit the card width."""
    t = re.sub(r"\s+", " ", (raw or "").strip())
    if not t:
        return ""
    m = re.match(
        r"(20\d{2})-(\d{2})-(\d{2})\s+to\s+(20\d{2})-(\d{2})-(\d{2})$",
        t,
        flags=re.I,
    )
    if m:
        y1, mo1, d1, y2, mo2, d2 = m.groups()
        a = f"{_MONTHS[int(mo1)]} {int(d1)}"
        b = f"{_MONTHS[int(mo2)]} {int(d2)}"
        if y1 != y2:
            return f"{a}, {y1} – {b}, {y2}"
        return f"{a} – {b}"
    m = re.match(r"(20\d{2})-(\d{2})-(\d{2})$", t)
    if m:
        _y, mo, d = m.groups()
        return f"{_MONTHS[int(mo)]} {int(d)}"
    return t


def _fit_text(
    draw,
    text: str,
    font,
    max_width: int,
) -> str:
    """Shrink text with ellipsis so it never clips the card edge."""
    t = (text or "").strip()
    if not t:
        return ""
    if draw.textbbox((0, 0), t, font=font)[2] <= max_width:
        return t
    ell = "…"
    while t and draw.textbbox((0, 0), t + ell, font=font)[2] > max_width:
        t = t[:-1].rstrip(" —–-")
    return (t + ell) if t else ell


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

    width, height = 1080, 1920
    pad = 56
    inner = width - (pad * 2)
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    sport = str(payload.get("sport_name") or "Results")
    title_font = _get_font(72, True)
    sub_font = _get_font(34, True)
    section_font = _get_font(42, True)
    meta_font = _get_font(30, True)
    label_font = _get_font(36, True)
    rec_font = _get_font(38, True)
    val_font = _get_font(52, True)
    foot_font = _get_font(30, True)

    footer_h = 120
    header_h = 200
    avail = height - header_h - footer_h
    gap = 36
    # Fill the frame — large cards, minimal dead air under the header.
    box_h = (avail - gap) // 2
    y = header_h + 8

    draw.text((pad, 72), f"{sport} Results", fill=(15, 23, 42), font=title_font)
    draw.text((pad, 160), "predictionlab.io", fill=(0, 82, 155), font=sub_font)

    def _draw_window(title: str, window: dict | None) -> int:
        nonlocal y
        window = window or {}
        games = int(window.get("games") or 0)
        date_lbl = _short_date_label(str(window.get("label") or ""))
        meta_bits = []
        if date_lbl:
            meta_bits.append(date_lbl)
        if games:
            meta_bits.append(f"{games} games")
        meta = " · ".join(meta_bits)

        box_top = y
        draw.rounded_rectangle(
            (pad, box_top, width - pad, box_top + box_h),
            radius=28,
            outline=(203, 213, 225),
            width=3,
            fill=(248, 250, 252),
        )
        text_max = inner - 56
        head = _fit_text(draw, title, section_font, text_max)
        draw.text((pad + 28, box_top + 32), head, fill=(15, 23, 42), font=section_font)
        if meta:
            meta_fit = _fit_text(draw, meta, meta_font, text_max)
            draw.text(
                (pad + 28, box_top + 88),
                meta_fit,
                fill=(100, 116, 139),
                font=meta_font,
            )

        rows = (
            ("Moneyline", window.get("ml") or {}),
            ("Spread", window.get("spread") or {}),
            ("Total", window.get("total") or {}),
        )
        row_top = box_top + 150
        row_h = (box_h - 170) // 3
        for i, (name, mkt) in enumerate(rows):
            acc = str((mkt or {}).get("acc") or "—")
            rec = str((mkt or {}).get("record") or "—")
            row_y = row_top + i * row_h
            # One line: Market left, record center-left, % right — no cramped stack
            mid_y = row_y + max(0, (row_h - 52) // 2)
            draw.text((pad + 36, mid_y), name, fill=(51, 65, 85), font=label_font)
            rec_x = pad + 280
            draw.text((rec_x, mid_y), rec, fill=(71, 85, 105), font=rec_font)
            acc_bb = draw.textbbox((0, 0), acc, font=val_font)
            draw.text(
                (width - pad - 36 - (acc_bb[2] - acc_bb[0]), mid_y - 4),
                acc,
                fill=(15, 23, 42),
                font=val_font,
            )
            if i < 2:
                sep_y = row_y + row_h - 2
                draw.line(
                    (pad + 28, sep_y, width - pad - 28, sep_y),
                    fill=(226, 232, 240),
                    width=2,
                )
        y = box_top + box_h + gap
        return y

    _draw_window("Last Night", payload.get("last_night"))
    _draw_window("Last 7 Days", payload.get("last_7"))

    note = "Sharp Consensus · ML / Spread / Total"
    draw.text((pad, height - 110), note, fill=(100, 116, 139), font=foot_font)
    draw.text(
        (pad, height - 68),
        "Transparent results you can advertise",
        fill=(148, 163, 184),
        font=foot_font,
    )

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
.social-export-wrap{max-width:960px;margin:24px auto 0;padding:0 8px;}
.social-export-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px;}
.social-export-title{font-size:0.9em;font-weight:800;color:#0f172a;letter-spacing:0.2px;}
.social-export-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.social-export-btn{border:1px solid #00529B;background:#fff;color:#00529B;border-radius:999px;padding:6px 12px;font-size:0.76em;font-weight:800;cursor:pointer;text-decoration:none;display:inline-flex;}
.social-export-btn.primary{background:#00529B;color:#fff;}
.social-image-link{display:block;max-width:420px;margin:0 auto;}
.social-image-link img{width:100%;height:auto;border-radius:12px;border:1px solid #e2e8f0;display:block;}
.cfl-results-share{display:none!important;}
"""


def results_share_wrap_html(
    *,
    sport_name: str,
    share_src: str,
    view_url: str,
) -> str:
    sport = sport_name or "Results"
    preview = share_src
    if "w=" not in preview and "?" not in preview:
        preview = f"{preview}?w=640"
    elif "w=" not in preview:
        preview = f"{preview}&w=640"
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
        f'<img src="{preview}" alt="{sport} results share image" width="640" height="1138" loading="lazy" decoding="async">'
        f"</a></div>"
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
        return html.replace('<div class="share-strip"', wrap_html + '\n<div class="share-strip"', 1)
    if "</main>" in html:
        return html.replace("</main>", wrap_html + "\n</main>", 1)
    return html + wrap_html
