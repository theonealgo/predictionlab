"""Golf results image: the selected tournament's finish vs our pre-event rank.

Golf has no moneyline / spread / total records, so the team-sport results
image would be all dashes. Everything drawn here is read from the golf
results table already on the page.
"""
from __future__ import annotations

import html as _html
import io
import re
from typing import Any


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _place(text: str) -> int | None:
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else None


def golf_payload_from_results_html(html: str, sport_name: str = "Golf") -> dict[str, Any] | None:
    html = html or ""
    table = re.search(
        r'<table class="golf-table golf-results-table">([\s\S]*?)</table>', html, flags=re.I
    )
    if not table:
        return None
    heads = [_text(h).lower() for h in re.findall(r"<th\b[^>]*>([\s\S]*?)</th>", table.group(1), flags=re.I)]
    col = {h: i for i, h in enumerate(heads)}
    if "player" not in col or "finish" not in col:
        return None
    rows: list[dict[str, Any]] = []
    for tr in re.finditer(r"<tr>([\s\S]*?)</tr>", table.group(1), flags=re.I):
        cells = re.findall(r"<td\b[^>]*>([\s\S]*?)</td>", tr.group(1), flags=re.I)
        if len(cells) < len(heads):
            continue
        cell = cells[col["player"]]
        name_m = re.search(r"<strong>([\s\S]*?)</strong>", cell, flags=re.I)
        player = _text(name_m.group(1) if name_m else cell)
        finish_txt = _text(cells[col["finish"]])
        finish = _place(finish_txt)
        if not player or finish is None:
            continue
        rows.append(
            {
                "player": player,
                "rank": _place(_text(cells[col["our rank"]])) if "our rank" in col else None,
                "finish": finish,
                "finish_text": finish_txt,
                "score": (_text(cells[col["score"]]) if "score" in col else "") or "—",
            }
        )
    if not rows:
        return None
    rows.sort(key=lambda r: r["finish"])
    event, date, status = "", "", ""
    picker = re.search(r'<select id="golf-event"[\s\S]*?</select>', html, flags=re.I)
    sel = re.search(
        r'<option value="[^"]*" selected>([^<]+)</option>', picker.group(0) if picker else "", flags=re.I
    )
    if sel:
        label = _html.unescape(sel.group(1)).strip()
        m = re.match(r"(.+?)\s+—\s+(\d{4}-\d{2}-\d{2})\s*·\s*(.+)$", label)
        if m:
            event, date, status = m.group(1).strip(), m.group(2), m.group(3).strip()
        else:
            event = label
    hits = []
    for k in (10, 20):
        ranked = [r for r in rows if r["rank"] is not None and r["rank"] <= k]
        if ranked:
            hits.append(
                {"k": k, "n": len(ranked), "hit": sum(1 for r in ranked if r["finish"] <= k)}
            )
    empty = {
        "label": "",
        "games": 0,
        "ml": {"acc": "—", "record": "—"},
        "spread": {"acc": "—", "record": "—"},
        "total": {"acc": "—", "record": "—"},
    }
    return {
        "type": "results-summary",
        "sport_name": sport_name or "Golf",
        "last_night": dict(empty),
        "last_7": dict(empty),
        "golf": {
            "event": event,
            "date": date,
            "status": status,
            "top": rows[:5],
            "hits": hits,
        },
    }


def _pretty_date(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", (iso or "").strip())
    if not m:
        return iso or ""
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))}, {m.group(1)}"


def render_golf_results_image(payload: dict, fmt: str = "jpg", *, max_width: int | None = None):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None, None
    golf = (payload or {}).get("golf") or {}
    top = golf.get("top") or []
    if not top:
        return None, None

    def font(size: int, bold: bool = True):
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

    width, height, pad = 1080, 1920, 64
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    ink, muted, faint, blue = (15, 23, 42), (100, 116, 139), (148, 163, 184), (0, 82, 155)
    box_fill, box_line = (248, 250, 252), (226, 232, 240)

    def fit(text: str, size: int, max_w: int, bold: bool = True):
        while size > 22 and draw.textlength(text, font=font(size, bold)) > max_w:
            size -= 2
        return font(size, bold)

    def box(top_y: int, h: int) -> None:
        draw.rounded_rectangle(
            (pad, top_y, width - pad, top_y + h), radius=22, outline=box_line, width=2, fill=box_fill
        )

    sport = str(payload.get("sport_name") or "Golf")
    y = 120
    draw.text((pad, y), f"{sport} Results", fill=ink, font=fit(f"{sport} Results", 96, width - 2 * pad))
    y += 124
    draw.text((pad, y), "predictionlab.io", fill=blue, font=font(44, False))
    y += 90

    event = str(golf.get("event") or "")
    meta = " · ".join(x for x in (_pretty_date(str(golf.get("date") or "")), str(golf.get("status") or "")) if x)
    winner = top[0]
    box(y, 330)
    inner = width - 2 * pad - 64
    draw.text((pad + 32, y + 28), event or "Tournament", fill=ink, font=fit(event or "Tournament", 56, inner))
    if meta:
        draw.text((pad + 32, y + 98), meta, fill=muted, font=font(38, False))
    draw.text((pad + 32, y + 160), "WINNER", fill=faint, font=font(32, True))
    draw.text((pad + 32, y + 200), winner["player"], fill=ink, font=fit(winner["player"], 58, inner - 220))
    score_f = font(58, True)
    sw = draw.textlength(str(winner["score"]), font=score_f)
    draw.text((width - pad - 32 - sw, y + 200), str(winner["score"]), fill=ink, font=score_f)
    if winner.get("rank") is not None:
        draw.text((pad + 32, y + 268), f"Our pre-event rank #{winner['rank']}", fill=muted, font=font(36, False))
    y += 330 + 44

    rows_h = 170 + 70 * len(top)
    box(y, rows_h)
    draw.text((pad + 32, y + 26), "Top finishers", fill=ink, font=font(48, True))
    cols = (pad + 32, pad + 170, width - pad - 330, width - pad - 32)
    hy = y + 96
    col_f = font(30, True)
    draw.text((cols[0], hy), "FIN", fill=faint, font=col_f)
    draw.text((cols[1], hy), "PLAYER", fill=faint, font=col_f)
    draw.text((cols[2], hy), "SCORE", fill=faint, font=col_f)
    has_rank = any(r.get("rank") is not None for r in top)
    if has_rank:
        lab = "OUR RANK"
        draw.text((cols[3] - draw.textlength(lab, font=col_f), hy), lab, fill=faint, font=col_f)
    ry = hy + 52
    for r in top:
        row_f = font(42, False)
        draw.text((cols[0], ry), str(r.get("finish_text") or r["finish"]), fill=ink, font=font(42, True))
        draw.text((cols[1], ry), r["player"], fill=ink, font=fit(r["player"], 42, cols[2] - cols[1] - 24, False))
        draw.text((cols[2], ry), str(r["score"]), fill=ink, font=row_f)
        if has_rank:
            rk = f"#{r['rank']}" if r.get("rank") is not None else "—"
            draw.text((cols[3] - draw.textlength(rk, font=font(42, True)), ry), rk, fill=ink, font=font(42, True))
        ry += 70
    y += rows_h + 44

    hits = golf.get("hits") or []
    if hits:
        box(y, 70 + 80 * len(hits))
        hy = y + 34
        for h in hits:
            label = f"Our top {h['k']}"
            val = f"{h['hit']} of {h['n']} finished top {h['k']}"
            draw.text((pad + 32, hy), label, fill=ink, font=font(44, True))
            vf = fit(val, 42, inner - 300, False)
            draw.text((width - pad - 32 - draw.textlength(val, font=vf), hy + 2), val, fill=ink, font=vf)
            hy += 80
        y += 70 + 80 * len(hits) + 44

    foot = "Our pre-event rank vs the official finish" if has_rank else "Official finishing order"
    draw.text((pad, y), foot, fill=faint, font=font(36, False))

    try:
        mw = int(max_width) if max_width is not None else None
    except (TypeError, ValueError):
        mw = None
    if mw and 240 <= mw < width:
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS
        image = image.resize((mw, max(1, int(round(height * mw / float(width))))), resample)
    out = io.BytesIO()
    if fmt in ("jpg", "jpeg"):
        image.save(out, format="JPEG", quality=82 if mw else 93, optimize=True, subsampling=0)
        mime = "image/jpeg"
    else:
        image.save(out, format="PNG", optimize=True)
        mime = "image/png"
    return out.getvalue(), mime
