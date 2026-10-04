#!/usr/bin/env python3
"""Share image + social strip for Tennis/UFC sandbox pages (MLB chrome parity)."""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_SHARE_STRIP = """
<div class="share-strip">
  <span class="share-strip-label">Share on social media</span>
  <div class="share-icons">
    <a class="share-icon" href="https://x.com/intent/post?url={url}" target="_blank" rel="noopener" aria-label="Share on X"><img src="/static/icons/social/x.svg" alt="X"></a>
    <a class="share-icon" href="https://www.facebook.com/sharer/sharer.php?u={url}" target="_blank" rel="noopener" aria-label="Share on Facebook"><img src="/static/icons/social/facebook.svg" alt="Facebook"></a>
    <a class="share-icon" href="https://instagram.com/predictionlab.io" target="_blank" rel="noopener" aria-label="Instagram"><img src="/static/icons/social/instagram.svg" alt="Instagram"></a>
    <a class="share-icon" href="https://predictionlab.io" target="_blank" rel="noopener" aria-label="TikTok"><img src="/static/icons/social/tiktok.svg" alt="TikTok"></a>
    <a class="share-icon" href="https://www.linkedin.com/sharing/share-offsite/?url={url}" target="_blank" rel="noopener" aria-label="Share on LinkedIn"><img src="/static/icons/social/linkedin.svg" alt="LinkedIn"></a>
    <a class="share-icon" href="https://www.reddit.com/submit?url={url}" target="_blank" rel="noopener" aria-label="Share on Reddit"><img src="/static/icons/social/reddit.svg" alt="Reddit"></a>
    <a class="share-icon" href="https://www.tumblr.com/widgets/share/tool?canonicalUrl={url}" target="_blank" rel="noopener" aria-label="Share on Tumblr"><img src="/static/icons/social/tumblr.svg" alt="Tumblr"></a>
    <a class="share-icon" href="https://api.whatsapp.com/send?text={url}" target="_blank" rel="noopener" aria-label="Share on WhatsApp"><img src="/static/icons/social/whatsapp.svg" alt="WhatsApp"></a>
    <a class="share-icon" href="https://telegram.me/share/url?url={url}" target="_blank" rel="noopener" aria-label="Share on Telegram"><img src="/static/icons/social/telegram.svg" alt="Telegram"></a>
  </div>
</div>
"""


# Sizing lives in the live site's base.html; sandbox/isolation shells don't load it,
# so ship it inline or the raw SVGs render full-size.
_SHARE_CSS = """
<style id="share-social-chrome">
.share-strip{max-width:1200px;margin:0 auto 10px;padding:10px 16px;display:flex;align-items:center;
justify-content:center;gap:10px;flex-wrap:wrap;background:rgba(244,247,249,0.7);
border:1px solid rgba(15,23,42,0.1);border-radius:12px;}
.share-strip-label{font-size:0.82em;font-weight:800;color:#0f172a;letter-spacing:0.2px;}
.share-icons{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.share-icon{width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;
border-radius:999px;border:1px solid rgba(15,23,42,0.14);background:#fff;}
.share-icon img{width:16px;height:16px;display:block;}
.share-icon:hover{border-color:#00529B;background:rgba(0,82,155,0.08);}
.social-export-wrap{max-width:960px;margin:24px auto 0;padding:0 8px;}
.social-export-head{display:flex;align-items:center;justify-content:space-between;gap:10px;
flex-wrap:wrap;margin-bottom:10px;}
.social-export-title{font-size:0.9em;font-weight:800;color:#0f172a;letter-spacing:0.2px;}
.social-export-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.social-export-btn{border:1px solid #00529B;background:#fff;color:#00529B;border-radius:999px;
padding:6px 12px;font-size:0.76em;font-weight:800;cursor:pointer;text-decoration:none;}
.social-export-btn.primary{background:#00529B;color:#fff;}
.social-image-link{display:block;width:min(100%,min(420px,90vw));margin:0 auto;aspect-ratio:9/16;
border-radius:14px;overflow:hidden;border:1px solid rgba(15,23,42,0.14);
box-shadow:0 6px 18px rgba(15,23,42,0.08);background:#fff;}
.social-image-link img{display:block;width:100%;height:100%;object-fit:cover;}
</style>
"""


def _ensure_share_css(html: str) -> str:
    if not html or 'id="share-social-chrome"' in html or ".share-icon img" in html:
        return html
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", _SHARE_CSS + "</head>", html, count=1, flags=re.I)
    return _SHARE_CSS + html


def _share_export_wrap(sport_label: str, share_path: str) -> str:
    return (
        '<div class="social-export-wrap">'
        '<div class="social-export-head">'
        f'<div class="social-export-title">{sport_label} Predictions Image</div>'
        '<div class="social-export-actions">'
        f'<a class="social-export-btn" href="{share_path}" download="predictionlab-picks.jpg">Download image</a>'
        f'<a class="social-export-btn primary" href="{share_path}" target="_blank" rel="nofollow noopener">Open fullscreen</a>'
        "</div></div>"
        f'<a class="social-image-link" href="{share_path}" target="_blank" rel="nofollow noopener">'
        f'<img src="{share_path}" alt="{sport_label} predictions">'
        "</a></div>"
    )


def ensure_share_and_social_chrome(
    html: str,
    *,
    sport: str,
    page_path: str,
) -> str:
    """Ensure predictions image + share strip sit above the footer."""
    if not html:
        return html
    html = _ensure_share_css(html)
    # Strip any share block wrongly injected into pick cards (matched card-footer).
    html = re.sub(
        r'<div class="social-export-wrap">[\s\S]*?</div>\s*'
        r'(?=<footer class="card-footer"|<div class="card-details"|</div>\s*</div>\s*</div>)',
        "",
        html,
        flags=re.I,
    )
    # Also drop orphan share-strip left inside a card.
    html = re.sub(
        r'<div class="share-strip">[\s\S]*?</div>\s*(?=<footer class="card-footer"|<div class="card-details")',
        "",
        html,
        flags=re.I,
    )
    sport_l = (sport or "").strip().lower()
    label = "Tennis" if sport_l == "tennis" else "UFC" if sport_l == "ufc" else sport_l.upper()
    share_path = f"/{sport_l}/share.jpg"
    page_url = quote(f"http://127.0.0.1:5081{page_path}", safe="")
    strip = _SHARE_STRIP.format(url=page_url)
    export = _share_export_wrap(label, share_path)
    block = export + "\n" + strip

    # If a correct bottom share already exists (after finals / before site footer), retarget only.
    foot_i = html.lower().find("site-directory-footer")
    export_i = html.find('class="social-export-wrap"')
    if export_i >= 0 and foot_i > 0 and export_i > foot_i - 8000 and export_i < foot_i:
        html = re.sub(
            r'<div class="social-export-title">[^<]*</div>',
            f'<div class="social-export-title">{label} Predictions Image</div>',
            html,
            count=1,
            flags=re.I,
        )
        html = re.sub(
            r'(href|src)=([\'"])/share/predictions/[^\'"]+\2',
            rf"\1=\2{share_path}\2",
            html,
            flags=re.I,
        )
        html = re.sub(
            r'(href|src)=([\'"])/(?:cfl|tennis|ufc)/share\.jpg\2',
            rf"\1=\2{share_path}\2",
            html,
            flags=re.I,
        )
        return html

    # Remove any remaining misplaced export/share before re-inserting at bottom.
    while True:
        m = re.search(r'<div class="social-export-wrap"', html, flags=re.I)
        if not m:
            break
        # balanced div strip
        start = m.start()
        i = html.find(">", start) + 1
        depth = 1
        j = i
        while j < len(html) and depth:
            o = html.find("<div", j)
            c = html.find("</div>", j)
            if c < 0:
                break
            if o >= 0 and o < c:
                depth += 1
                j = o + 4
            else:
                depth -= 1
                j = c + 6
        html = html[:start] + html[j:]
    html = re.sub(r'<div class="share-strip">[\s\S]*?</div>', "", html, count=3, flags=re.I)

    if 'class="share-strip"' in html and 'class="social-export-wrap"' not in html:
        return html.replace('<div class="share-strip"', export + '\n<div class="share-strip"', 1)

    # Never match pick-card <footer class="card-footer"> — only the site footer.
    for pat in (
        r'(<div\b[^>]*\bclass="[^"]*\bsite-directory-footer\b[^"]*"[^>]*>)',
        r'(<footer\b(?![^>]*\bcard-footer)[^>]*class="[^"]*(?:site|pl2|research)[^"]*"[^>]*>)',
        r'(</main>)',
        r'(</body>)',
    ):
        m = re.search(pat, html, flags=re.I)
        if m:
            return html[: m.start()] + block + "\n" + html[m.start() :]
    return html + block


def _jpeg_from_rows(title: str, slate: str, rows: list[dict[str, Any]]) -> bytes | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    if not rows:
        return None
    width, height = 1080, 1920
    pad = 44
    cx = width // 2
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 92)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 56)
        vs_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 52)
        check_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 48)
        team_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 58)
    except Exception:
        title_font = sub_font = vs_font = check_font = team_font = ImageFont.load_default()
    draw.text((pad, 64), title, fill=(15, 23, 42), font=title_font)
    draw.text((pad, 162), slate or "", fill=(71, 85, 105), font=sub_font)
    header_bottom = 224
    available = max(200, height - header_bottom - 48)
    gap = 20
    n = len(rows)
    slot_height = max(380, min(560, (available - gap * (n - 1)) // n))
    row_top = header_bottom + max(0, (available - (n * slot_height + gap * (n - 1))) // 2)
    for idx, item in enumerate(rows):
        y1 = row_top + idx * (slot_height + gap)
        y2 = y1 + slot_height
        draw.rounded_rectangle(
            (pad, y1, width - pad, y2),
            radius=24,
            outline=(203, 213, 225),
            width=3,
            fill=(255, 255, 255),
        )
        away, home = item["away"], item["home"]
        away_bbox = draw.textbbox((0, 0), away, font=team_font)
        home_bbox = draw.textbbox((0, 0), home, font=team_font)
        vs_bbox = draw.textbbox((0, 0), "VS", font=vs_font)
        away_y = y1 + int(slot_height * 0.12)
        vs_y = y1 + int(slot_height * 0.42)
        home_y = y1 + int(slot_height * 0.66)
        draw.text(
            (cx - (away_bbox[2] - away_bbox[0]) // 2, away_y),
            away,
            fill=(15, 23, 42),
            font=team_font,
        )
        draw.text(
            (cx - (vs_bbox[2] - vs_bbox[0]) // 2, vs_y),
            "VS",
            fill=(100, 116, 139),
            font=vs_font,
        )
        draw.text(
            (cx - (home_bbox[2] - home_bbox[0]) // 2, home_y),
            home,
            fill=(15, 23, 42),
            font=team_font,
        )
        if item.get("pick_side") == "away":
            ax = cx - (away_bbox[2] - away_bbox[0]) // 2 - 54
            draw.rounded_rectangle((ax, away_y - 10, ax + 44, away_y + 38), radius=8, fill=(34, 197, 94))
            draw.text((ax + 9, away_y - 8), "✓", fill=(255, 255, 255), font=check_font)
        if item.get("pick_side") == "home":
            hx = cx - (home_bbox[2] - home_bbox[0]) // 2 - 54
            draw.rounded_rectangle((hx, home_y - 10, hx + 44, home_y + 38), radius=8, fill=(34, 197, 94))
            draw.text((hx + 9, home_y - 8), "✓", fill=(255, 255, 255), font=check_font)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=93, optimize=True, subsampling=0)
    return out.getvalue()


def build_tennis_share_jpeg() -> bytes | None:
    try:
        from tennis_page import build_tennis_picks_payload
    except Exception:
        return None
    data = build_tennis_picks_payload() or {}
    upcoming = list(data.get("upcoming") or [])
    rows: list[dict[str, Any]] = []
    slate = datetime.now(ET).strftime("%Y-%m-%d")
    for c in upcoming:
        a = str(c.get("player_a") or c.get("away") or "").strip()
        b = str(c.get("player_b") or c.get("home") or "").strip()
        if not a or not b:
            continue
        pick = str(c.get("pick") or "")
        try:
            conf = float(c.get("prob") or c.get("face_prob") or 50.0)
        except (TypeError, ValueError):
            conf = 50.0
        pick_side = "away" if pick == a else "home" if pick == b else ("away" if conf >= 50 else "home")
        day = str(c.get("game_date") or slate)[:10]
        if day:
            slate = day
        rows.append({"away": a, "home": b, "pick_side": pick_side, "confidence": conf})
    rows.sort(key=lambda r: (-float(r["confidence"]), r["away"], r["home"]))
    return _jpeg_from_rows("Tennis Predictions", slate, rows[:3])


def build_ufc_share_jpeg() -> bytes | None:
    try:
        from ufc_page import _render_mod
    except Exception:
        return None
    render = _render_mod(reload=False)
    cards = render.list_pick_cards() if hasattr(render, "list_pick_cards") else []
    rows: list[dict[str, Any]] = []
    slate = datetime.now(ET).strftime("%Y-%m-%d")
    for c in cards or []:
        away = str(c.get("away_fighter") or "").strip()
        home = str(c.get("home_fighter") or "").strip()
        if not away or not home:
            continue
        try:
            hp = float(c.get("home_win_prob") if c.get("home_win_prob") is not None else 0.5)
        except (TypeError, ValueError):
            hp = 0.5
        pick_side = "home" if hp >= 0.5 else "away"
        conf = round(max(hp, 1.0 - hp) * 100.0, 1)
        day = str(c.get("fight_date") or "")[:10]
        if day:
            slate = day
        rows.append({"away": away, "home": home, "pick_side": pick_side, "confidence": conf})
    rows.sort(key=lambda r: (-float(r["confidence"]), r["away"], r["home"]))
    return _jpeg_from_rows("UFC Predictions", slate, rows[:3])


def _esc(s: Any) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_units(units: Any) -> str:
    if isinstance(units, (int, float)):
        return f"{float(units):+.1f}u"
    return ""


def _count_tag(block: dict[str, Any]) -> str:
    w = int(block.get("w") or 0)
    l = int(block.get("l") or 0)
    graded = int(block.get("graded") if block.get("graded") is not None else (w + l))
    events = int(
        block.get("events")
        if block.get("events") is not None
        else (block.get("games") or graded or 0)
    )
    bits: list[str] = []
    if events:
        bits.append(f"{events} decision" if events == 1 else f"{events} decisions")
    bits.append(f"{graded} graded")
    date = block.get("date")
    if date:
        bits.append(str(date))
    return " · ".join(bits)


def build_ml_sport_performance_html(payload: dict[str, Any], *, sport: str) -> str:
    """Best Performing + Efficiency + Last Night / Last 7 / Season (cards view)."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return ""
    try:
        from team_tabbed_results import MODEL_ORDER
    except Exception:
        MODEL_ORDER = [
            "Grinder2",
            "Takedown",
            "Edge",
            "XSharp",
            "Sharp Consensus",
            "Efficiency",
        ]

    sport_l = (sport or "sport").strip().lower()
    prefix = f"{sport_l}-perf"
    analytics = payload.get("analytics") or {}
    tallies = payload.get("tallies") or {}
    order = list(payload.get("model_order") or MODEL_ORDER)
    best = analytics.get("best_performing") or {}
    eff = (analytics.get("efficiency_breakout") or {}).get("moneyline") or {}

    def best_card(label: str, row: dict[str, Any] | None) -> str:
        if not row:
            return (
                f'<div class="tally-card"><div class="mlabel">{_esc(label)}</div>'
                f'<div class="acc muted">—</div></div>'
            )
        u = _fmt_units(row.get("units"))
        rec = row.get("record") or ""
        sub = _esc(rec) + (f" · {_esc(u)}" if u else "")
        return (
            f'<div class="tally-card"><div class="mlabel">{_esc(label)}</div>'
            f'<div class="rec"><b>{_esc(row.get("name") or "—")}</b></div>'
            f'<div class="acc ok">{_esc(row.get("pct"))}%</div>'
            f'<div class="rec">{sub}</div></div>'
        )

    n_eff = int(eff.get("n") or eff.get("graded_games") or 0)
    acc = eff.get("accuracy")
    if n_eff > 0 and acc is None:
        acc_s = "0%"
    else:
        acc_s = f"{acc}%" if acc is not None else "—"
    rec_eff = eff.get("record") or ("—" if n_eff <= 0 else "0-0")
    u_eff = _fmt_units(eff.get("units")) or ("+0.0u" if n_eff > 0 else "—")
    games_s = f" · {n_eff} graded games" if n_eff > 0 else ""
    eff_label = eff.get("label") or "Efficiency Season · Moneyline"
    eff_card = (
        f'<div class="tally-card"><div class="mlabel">{_esc(eff_label)}</div>'
        f'<div class="acc">{_esc(acc_s)}</div>'
        f'<div class="rec">Accuracy {_esc(acc_s)} · Record {_esc(rec_eff)} · '
        f"Units {_esc(u_eff)}{_esc(games_s)}</div></div>"
    )

    def window_block(key: str, title: str) -> str:
        block = tallies.get(key) or {}
        models = block.get("models") or {}
        names = [n for n in order if n in models] or list(models.keys())
        cards = []
        for name in names:
            m = models.get(name) or {}
            n = int(m.get("n") or 0)
            pct = m.get("pct")
            rec = m.get("record") or f"{int(m.get('w') or 0)}-{int(m.get('l') or 0)}"
            u = _fmt_units(m.get("units"))
            if not u and n > 0:
                w = int(m.get("w") or 0)
                l = int(m.get("l") or 0)
                if w + l > 0:
                    units = round(w * (100 / 110) - l, 1)
                    u = f"{units:+.1f}u"
            if n > 0 and pct is None:
                w = int(m.get("w") or 0)
                l = int(m.get("l") or 0)
                if w + l > 0:
                    pct = round(1000 * w / (w + l)) / 10
            label = name
            if re.search(r"efficiency", name, flags=re.I) and re.search(
                r"season", title, flags=re.I
            ):
                label = "Efficiency Season"
            if n > 0 and pct is not None:
                cls = "ok" if float(pct) >= 52 else ("bad" if float(pct) < 40 else "")
                acc_html = f'<div class="acc {cls}">{_esc(pct)}%</div>'
            elif n > 0:
                acc_html = '<div class="acc">0%</div>'
            else:
                acc_html = '<div class="acc muted">—</div>'
            graded_n = int(m.get("graded") if m.get("graded") is not None else n)
            rec_line = _esc(rec)
            if not n:
                rec_line += " · no picks"
            if u:
                rec_line += f" · {_esc(u)}"
            if n:
                rec_line += f" · {graded_n} graded"
            cards.append(
                f'<div class="tally-card"><div class="mlabel">{_esc(label)}</div>'
                f'{acc_html}<div class="rec">{rec_line}</div></div>'
            )
        tag = _count_tag(block)
        return (
            f'<section class="tally {prefix}-window"><h2>{_esc(title)} '
            f'<span class="tag">({_esc(tag)})</span></h2>'
            f'<div class="tally-grid">{"".join(cards)}</div></section>'
        )

    return f"""
<section class="tally pl-analytics {prefix}-analytics" aria-label="{_esc(sport_l.upper())} model performance">
  <h2>Best Performing Model</h2>
  <div class="tally-grid">
    {best_card("Today", best.get("today"))}
    {best_card("Last 7", best.get("last_7"))}
    {best_card("Season", best.get("season"))}
  </div>
  <h2 style="margin-top:1.25rem">Efficiency by Market</h2>
  <div class="tally-grid">
    {eff_card}
  </div>
</section>
{window_block("last_night", "Last Night")}
{window_block("last_7", "Last 7")}
{window_block("season", "Season")}
<style id="{prefix}-tallies">
  .{prefix}-analytics,.{prefix}-window{{max-width:1100px;margin:16px auto 20px;padding:16px;
    border:1px solid rgba(15,23,42,.12);border-radius:12px;background:#f8fafc}}
  .{prefix}-analytics h2,.{prefix}-window h2{{margin:0 0 10px;font-size:1.05rem;color:#0f172a;text-align:center}}
  .{prefix}-analytics .tally-grid,.{prefix}-window .tally-grid{{display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
  .{prefix}-analytics .tally-card,.{prefix}-window .tally-card{{background:#fff;
    border:1px solid rgba(15,23,42,.1);border-radius:10px;padding:12px;text-align:center}}
  .{prefix}-analytics .mlabel,.{prefix}-window .mlabel{{font-size:.75rem;color:#475569;margin-bottom:4px}}
  .{prefix}-analytics .acc,.{prefix}-window .acc{{font-size:1.45rem;font-weight:800;color:#0f172a}}
  .{prefix}-analytics .acc.ok,.{prefix}-window .acc.ok{{color:#15803d}}
  .{prefix}-analytics .acc.bad,.{prefix}-window .acc.bad{{color:#b91c1c}}
  .{prefix}-analytics .acc.muted,.{prefix}-window .acc.muted{{color:#94a3b8}}
  .{prefix}-analytics .rec,.{prefix}-window .rec{{font-size:.8rem;color:#475569;margin-top:4px}}
  .{prefix}-window h2 .tag{{font-size:.78rem;font-weight:600;color:#64748b}}
  @media(max-width:720px){{
    .{prefix}-analytics .tally-grid,.{prefix}-window .tally-grid{{grid-template-columns:1fr}}
  }}
</style>
"""
