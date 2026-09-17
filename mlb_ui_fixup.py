#!/usr/bin/env python3
"""MLB UI SIGNED OFF — do not change without owner request.

# ============================================================
# MLB LOCK — DO NOT MODIFY
# MLB was previously fixed and verified.
# DO NOT change this logic unless the user explicitly says:
# "UNLOCK MLB"
# Changes to other sports must NOT modify MLB behavior.
# ============================================================

Locked 2026-08-10. See notes/MLB_LOCKED.md / qa/MLB_SIGNED_OFF.txt.

MLB publish-layer HTML fixups for the live duplicate (work2).

Ported from independent_sports/hub/sandbox_fixup.py (signed-off MLB UI).
Does NOT replace header/footer/nav — work2 chrome stays intact.
Does NOT invent vendor/IP labels in HTML.

MLB_FLIP_SPREAD: default ON (set MLB_FLIP_SPREAD=0 to disable).
Display-only invert of PL/XSharp run-line sides; books/ML/totals unchanged.
"""
from __future__ import annotations

import html as html_lib
import importlib.util
import os
import re
from pathlib import Path

# MLB spread display invert (publish layer only). Default OFF — a leftover
# invert was hiding Home −1.5 and fighting the home-centric model sign.
# Set MLB_FLIP_SPREAD=1 only as a diagnostic. Does not touch ML/totals or DB.
MLB_FLIP_SPREAD = os.environ.get("MLB_FLIP_SPREAD", "0").strip().lower() in (
    "1",
    "true",
    "on",
    "yes",
)


def mlb_et_today_str() -> str:
    """MLB game-day in America/New_York (not Render UTC)."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def mlb_slate_has_et_today(predictions) -> bool:
    """True when any card is dated today ET — yesterday-only cache is stale."""
    today = mlb_et_today_str()
    for pred in predictions or []:
        if not isinstance(pred, dict):
            continue
        if str(pred.get("game_date") or "")[:10] == today:
            return True
    return False


def mlb_html_has_et_today(html: str) -> bool:
    """True when rendered picks HTML has today's date section."""
    if not html:
        return False
    return f'id="date-{mlb_et_today_str()}"' in html


def ensure_pl2_header_css(html: str) -> str:
    """Put signed-off header CSS back if a stale page cache omitted it.

    pl2-header HTML without research-theme.css renders as a raw vertical link
    list. Does not change pick cards, models, or grades.
    """
    if not html or "pl2-header" not in html:
        return html
    bits: list[str] = []
    if "research-theme.css" not in html:
        bits.append('<link rel="stylesheet" href="/static/css/research-theme.css">')
    if "picks-nav-overrides.css" not in html:
        bits.append('<link rel="stylesheet" href="/static/css/picks-nav-overrides.css">')
    if not bits:
        return html
    inject = "\n".join(bits)
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", inject + "\n</head>", html, count=1, flags=re.I)
    return inject + html


def _balanced_div_at(html: str, start: int) -> tuple[str, int]:
    tag_end = html.find(">", start)
    if tag_end < 0:
        return "", -1
    j = tag_end + 1
    depth = 1
    while j < len(html) and depth > 0:
        no, nc = html.find("<div", j), html.find("</div>", j)
        if nc < 0:
            return "", -1
        if no >= 0 and no < nc:
            depth += 1
            j = no + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start : nc + 6], nc + 6
            j = nc + 6
    return "", -1


def dedupe_game_card_stacks(html: str) -> str:
    """Keep first game-card-stack per away+home+kickoff; drop identical duplicates."""
    if not html or "game-card-stack" not in html:
        return html
    parts: list[str] = []
    pos = 0
    seen: set[str] = set()
    while True:
        m = re.search(r'<div class="game-card-stack\b', html[pos:], flags=re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        box, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        head = box[:1200]

        def _attr(k: str) -> str:
            am = re.search(rf'data-{k}=["\']([^"\']*)["\']', head, re.I)
            return (am.group(1) if am else "").strip().lower()

        key = f"{_attr('away')}|{_attr('home')}|{_attr('time')}"
        if key == "||" or key not in seen:
            if key != "||":
                seen.add(key)
            parts.append(box)
        pos = end
    return "".join(parts)


def _proj_row_html(model_cls: str, label: str, scoreline: str) -> str:
    return (
        '<div class="proj-row">'
        f'<span class="proj-model {html_lib.escape(model_cls)}">'
        f"{html_lib.escape(label)}</span> "
        f'<span class="proj-val">{html_lib.escape(scoreline)}</span>'
        "</div>"
    )


def _has_proj_model_row(html: str, model_cls: str, label: str) -> bool:
    cls_re = re.escape(model_cls)
    lab_re = re.escape(label)
    if re.search(
        rf'<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*\b{cls_re}\b[^"]*"',
        html,
        flags=re.I,
    ):
        return True
    return bool(
        re.search(
            rf'<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*"[^>]*>\s*{lab_re}',
            html,
            flags=re.I,
        )
    )


def _fill_blank_proj_val(html: str, model_cls: str, label: str, scoreline: str) -> str:
    """Replace an existing Projected Score dash with the real model scoreline."""
    if not html or not scoreline:
        return html
    esc = (
        scoreline.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    def _patch_row(m: re.Match[str]) -> str:
        block = m.group(0)
        model_m = re.search(
            r'<span\b[^>]*\bclass="([^"]*\bproj-model\b[^"]*)"[^>]*>([^<]*)</span>',
            block,
            flags=re.I,
        )
        if not model_m:
            return block
        cls = (model_m.group(1) or "").lower()
        lab = (model_m.group(2) or "").strip()
        if model_cls.lower() not in cls and lab.lower() != label.lower():
            return block
        val_m = re.search(
            r'(<span\b[^>]*\bclass="[^"]*\bproj-val\b[^"]*"[^>]*>)([^<]*)(</span>)',
            block,
            flags=re.I,
        )
        if not val_m:
            return block
        val = (val_m.group(2) or "").strip().replace("&mdash;", "—").replace("&ndash;", "–")
        if val and val not in ("—", "–", "-", "N/A", "n/a"):
            return block
        return block[: val_m.start()] + val_m.group(1) + esc + val_m.group(3) + block[val_m.end() :]

    return re.sub(
        r'<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>[\s\S]*?</div>',
        _patch_row,
        html,
        flags=re.I,
    )


def _inject_projected_score_rows(rest: str, pl_proj: str, xs_proj: str) -> str:
    """Fill missing View Details PL/XSharp projected-score rows.

    Ported from iso_hub/sandbox_fixup.py — live mlb_ui_fixup previously only
    patched chart data-* attrs, so cards with a suppressed model stayed blank.
    """
    if not rest or (not pl_proj and not xs_proj):
        return rest
    rest2 = rest
    if pl_proj:
        rest2 = _fill_blank_proj_val(rest2, "pl", "Prediction Lab", pl_proj)
    if xs_proj:
        rest2 = _fill_blank_proj_val(rest2, "xs", "XSharp", xs_proj)
    missing_pl = bool(pl_proj) and not _has_proj_model_row(rest2, "pl", "Prediction Lab")
    missing_xs = bool(xs_proj) and not _has_proj_model_row(rest2, "xs", "XSharp")
    if not missing_pl and not missing_xs:
        return rest2

    title_m = re.search(
        r'(<div\b[^>]*\bclass="[^"]*\bproj-score-title\b[^"]*"[^>]*>'
        r"\s*Projected Score\s*</div>)",
        rest2,
        flags=re.I,
    )
    if title_m:
        # Insert PL immediately after the title if missing.
        cursor = title_m.end()
        if missing_pl:
            chunk = "\n            " + _proj_row_html("pl", "Prediction Lab", pl_proj)
            rest2 = rest2[:cursor] + chunk + rest2[cursor:]
            cursor += len(chunk)
        if missing_xs:
            last_row = None
            for m in re.finditer(
                r'<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>[\s\S]*?</div>',
                rest2,
                flags=re.I,
            ):
                last_row = m
            if last_row:
                rest2 = (
                    rest2[: last_row.end()]
                    + "\n            "
                    + _proj_row_html("xs", "XSharp", xs_proj)
                    + rest2[last_row.end() :]
                )
            else:
                rest2 = (
                    rest2[:cursor]
                    + "\n            "
                    + _proj_row_html("xs", "XSharp", xs_proj)
                    + rest2[cursor:]
                )
        return rest2

    box = (
        '<div class="proj-score-box">'
        '<div class="proj-score-title">Projected Score</div>'
    )
    if pl_proj:
        box += _proj_row_html("pl", "Prediction Lab", pl_proj)
    if xs_proj:
        box += _proj_row_html("xs", "XSharp", xs_proj)
    box += "</div>"
    conf_m = re.search(
        r'<div\b[^>]*\bclass="[^"]*\bpick-conf-bar\b[^"]*"',
        rest2,
        flags=re.I,
    )
    if conf_m:
        return rest2[: conf_m.start()] + box + rest2[conf_m.start() :]
    return rest2


def enrich_mlb_chart_data_attrs(html: str) -> str:
    """Backfill chart data-* attrs from View Details / face chips.

    Fills data-pl-spread / data-xs-spread / data-xs-proj when Odds & Lines or
    Projected Score rows are present, and data-total-ev from the card Total EV
    chip (Totals chart must not reuse moneyline data-edge).
    """
    if not html or "data-pick-card" not in html:
        return html

    def _cell(tr: str, cls: str) -> str:
        m = re.search(
            rf'<td\b[^>]*\bclass="[^"]*\b{cls}\b[^"]*"[^>]*>([^<]*)</td>',
            tr,
            flags=re.I,
        )
        return (m.group(1) if m else "").strip()

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]

        # Run Line row from Odds & Lines
        rl_tr = re.search(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*(?:Run Line|Spread|Puck Line)\s*</td>"
            r"([\s\S]*?)</tr>",
            rest,
            flags=re.I,
        )
        pl_rl = xs_rl = ""
        if rl_tr:
            pl_rl = _cell(rl_tr.group(0), "val-pl")
            xs_rl = _cell(rl_tr.group(0), "val-xs")

        # Projected score lines (View Details → Projected Score)
        # Keep labeled scoreline ("Away 4 – Home 5") when present.
        pl_proj = ""
        xs_proj = ""
        for m in re.finditer(
            r'<div\b[^>]*\bclass="[^"]*\bproj-row\b[^"]*"[^>]*>'
            r"([\s\S]*?)</div>",
            rest,
            flags=re.I,
        ):
            block = m.group(1)
            model_m = re.search(
                r'<span\b[^>]*\bclass="([^"]*\bproj-model\b[^"]*)"[^>]*>([^<]*)</span>',
                block,
                flags=re.I,
            )
            if not model_m:
                continue
            cls = (model_m.group(1) or "").lower()
            label = (model_m.group(2) or "").lower()
            val_m = re.search(
                r'<span\b[^>]*\bclass="[^"]*\bproj-val\b[^"]*"[^>]*>([^<]+)</span>',
                block,
                flags=re.I,
            )
            if not val_m:
                continue
            val = (val_m.group(1) or "").strip()
            if not val:
                continue
            nums = re.findall(r"(\d+(?:\.\d+)?)", val)
            if len(nums) < 2:
                continue
            is_pl = "prediction lab" in label or re.search(r"\bpl\b", cls)
            is_xs = "xsharp" in label or re.search(r"\bxs\b", cls)
            if is_pl and not pl_proj:
                pl_proj = val
            if is_xs and not xs_proj:
                xs_proj = val

        # Card face Total EV (e.g. -1.7%) — Totals chart column; not ML Edge
        total_ev = ""
        tev = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*Total\s*EV\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            rest,
            flags=re.I,
        )
        if tev:
            raw = (tev.group(1) or "").strip()
            raw = re.sub(r"[%\s]+$", "", raw).strip()
            if raw and raw not in ("—", "-", "N/A", "n/a"):
                total_ev = raw

        # Odds Total row (for xsproj3 when XSharp scoreline missing)
        tot_tr = re.search(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*Total\s*</td>"
            r"([\s\S]*?)</tr>",
            rest,
            flags=re.I,
        )
        xs_tot = pl_tot = books_tot = ""
        if tot_tr:
            books_tot = _cell(tot_tr.group(0), "val-books")
            pl_tot = _cell(tot_tr.group(0), "val-pl")
            xs_tot = _cell(tot_tr.group(0), "val-xs")
        if not books_tot:
            bm = re.search(r'\bdata-books-total="([^"]*)"', open_tag, flags=re.I)
            books_tot = (bm.group(1) if bm else "").strip()

        def _parse_total(raw: str) -> float | None:
            m = re.search(r"(\d+(?:\.\d+)?)", raw or "")
            if not m:
                return None
            try:
                n = float(m.group(1))
            except ValueError:
                return None
            return n if n > 0 else None

        def _round_half(n: float) -> float:
            return round(n * 2) / 2.0

        def _fmt_half(n: float) -> str:
            r = _round_half(n)
            return str(int(r)) if r == int(r) else str(r)

        # Prefer existing attr scorelines when enrich runs on already-filled tags
        if not pl_proj:
            am = re.search(r'\bdata-pl-proj="([^"]*)"', open_tag, flags=re.I)
            if am and (am.group(1) or "").strip():
                pl_proj = am.group(1).strip()
        if not xs_proj:
            am = re.search(r'\bdata-xs-proj="([^"]*)"', open_tag, flags=re.I)
            if am and (am.group(1) or "").strip():
                cand = am.group(1).strip()
                if len(re.findall(r"(\d+(?:\.\d+)?)", cand)) >= 2:
                    xs_proj = cand

        away_m = re.search(r'\bdata-away="([^"]*)"', open_tag, flags=re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', open_tag, flags=re.I)
        away_n = (away_m.group(1) if away_m else "").strip()
        home_n = (home_m.group(1) if home_m else "").strip()

        def _home_spread_from_label(text: str) -> float | None:
            raw = (text or "").strip()
            if not raw or raw in ("—", "-", "N/A"):
                return None
            t = (
                raw.replace("\u2212", "-")
                .replace("\u2013", "-")
                .replace("\u2014", "-")
            )
            m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*$", t)
            if not m:
                return None
            try:
                n = float(m.group(1))
            except ValueError:
                return None
            prefix = t[: m.start()].strip().lower()
            if home_n and home_n.lower() in prefix:
                return n
            if away_n and away_n.lower() in prefix:
                return -n
            return n  # bare / unknown: treat as home-centric

        def _labeled(a: float, h: float) -> str:
            if away_n and home_n:
                return f"{away_n} {_fmt_half(a)} – {home_n} {_fmt_half(h)}"
            return f"{_fmt_half(a)}–{_fmt_half(h)}"

        def _ensure_labeled(scoreline: str) -> str:
            """Prefer Away N – Home M so Totals chart never shows bare 5–4."""
            raw = (scoreline or "").strip()
            if not raw:
                return ""
            if re.search(r"[A-Za-z]", raw) and len(re.findall(r"(\d+(?:\.\d+)?)", raw)) >= 2:
                return raw
            nums = re.findall(r"(\d+(?:\.\d+)?)", raw)
            if len(nums) >= 2 and away_n and home_n:
                try:
                    return _labeled(float(nums[-2]), float(nums[-1]))
                except ValueError:
                    return raw
            return raw

        # Derive PL proj from Odds PL run line + PL total when Projected Score omitted
        if not pl_proj:
            pl_spread_src = pl_rl
            if not pl_spread_src:
                am = re.search(r'\bdata-pl-spread="([^"]*)"', open_tag, flags=re.I)
                pl_spread_src = (am.group(1) if am else "").strip()
            hs = _home_spread_from_label(pl_spread_src)
            pt = _parse_total(pl_tot)
            if hs is not None and pt is not None:
                home = _round_half((pt + hs) / 2.0)
                away = _round_half(pt - home)
                pl_proj = _labeled(away, home)

        # Derive XSharp from its own run line + total (not a scaled copy of PL)
        if not xs_proj:
            xs_spread_src = xs_rl
            if not xs_spread_src:
                am = re.search(r'\bdata-xs-spread="([^"]*)"', open_tag, flags=re.I)
                xs_spread_src = (am.group(1) if am else "").strip()
            hs = _home_spread_from_label(xs_spread_src)
            xt = _parse_total(xs_tot)
            if hs is not None and xt is not None:
                home = _round_half((xt + hs) / 2.0)
                away = _round_half(xt - home)
                xs_proj = _labeled(away, home)

        # Last resort: scale PL split to XSharp (or books) total when XS line missing.
        # Never invent an MLB XSharp face from PL/books — that is a different model.
        is_mlb = bool(re.search(r'\bdata-sport="MLB"', open_tag, flags=re.I))
        if not xs_proj and not is_mlb:
            T = _parse_total(xs_tot) or _parse_total(books_tot)
            nums = re.findall(r"(\d+(?:\.\d+)?)", pl_proj or "")
            if T is not None and len(nums) >= 2:
                try:
                    pl_a = float(nums[-2])
                    pl_h = float(nums[-1])
                except ValueError:
                    pl_a = pl_h = 0.0
                if pl_a + pl_h > 0:
                    away = _round_half((pl_a / (pl_a + pl_h)) * T)
                    home = _round_half(T - away)
                    xs_proj = _labeled(away, home)

        def _set_attr(tag: str, name: str, value: str, *, overwrite_empty: bool = True) -> str:
            if not value or value in ("—", "-", "N/A"):
                return tag
            esc = (
                value.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
            )
            m_ex = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
            if m_ex:
                existing = (m_ex.group(1) or "").strip()
                # Prefer richer labeled scoreline over compact "4–5" / empty
                if existing and not overwrite_empty:
                    return tag
                if (
                    name in ("data-xs-proj", "data-pl-proj")
                    and existing
                    and re.search(r"[A-Za-z]", existing)
                    and not re.search(r"[A-Za-z]", value)
                ):
                    return tag
                return re.sub(
                    rf'\b{name}="[^"]*"',
                    f'{name}="{esc}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            return tag[:-1] + f' {name}="{esc}">'

        pl_proj = _ensure_labeled(pl_proj)
        xs_proj = _ensure_labeled(xs_proj)

        rest2 = _inject_projected_score_rows(rest, pl_proj, xs_proj)

        open2 = open_tag
        open2 = _set_attr(open2, "data-pl-spread", pl_rl)
        open2 = _set_attr(open2, "data-xs-spread", xs_rl)
        open2 = _set_attr(open2, "data-pl-proj", pl_proj)
        open2 = _set_attr(open2, "data-xs-proj", xs_proj)
        open2 = _set_attr(open2, "data-total-ev", total_ev)
        if open2 == open_tag and rest2 == rest:
            return stack
        return open2 + rest2

    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def _flip_spread_side_text(text: str, home: str, away: str) -> str:
    """Invert favorite↔dog spread display. 'Orioles -1.5' → 'Angels -1.5'.

    Home-centric: negate the implied home spread, then reformat as fav −line.
    Leaves bare empties / N/A alone. ML and totals are never passed here.
    """
    raw = (text or "").strip()
    if not raw or raw in ("—", "–", "-", "‒", "N/A", "n/a"):
        return text
    home = (home or "").strip()
    away = (away or "").strip()
    t = (
        raw.replace("\u2212", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("&minus;", "-")
    )
    m = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*$", t)
    if not m:
        return text
    try:
        n = float(m.group(1))
    except ValueError:
        return text
    mag_s = m.group(1).lstrip("+-")
    prefix = t[: m.start()].strip()
    # Home-centric spread from favorite-centric label
    if home and home.lower() in prefix.lower():
        home_spread = n  # "Home -1.5" => -1.5; "Home +1.5" => +1.5
    elif away and away.lower() in prefix.lower():
        home_spread = -n  # "Away -1.5" => home +1.5
    elif prefix:
        # Unknown team token — flip sign on the same label
        flipped = f"+{mag_s}" if n < 0 else f"-{mag_s}"
        return f"{prefix} {flipped}".strip()
    else:
        # Bare number: negate
        return f"+{mag_s}" if n < 0 else f"-{mag_s}"

    flipped_home = -home_spread
    # Favorite-centric label (negative number on the favored side)
    if flipped_home <= 0:
        side = home or prefix
        return f"{side} -{mag_s}".replace("--", "-") if side else f"-{mag_s}"
    side = away or prefix
    return f"{side} -{mag_s}".replace("--", "-") if side else f"-{mag_s}"


def flip_mlb_model_spread_display(html: str) -> str:
    """After cards are built: invert displayed PL/XSharp run-line sides only.

    Books run line / data-books-spread stay market. ML + totals unchanged.
    Display/publish layer for current slate — does not rewrite graded results DB.
    Controlled by MLB_FLIP_SPREAD (default OFF).
    """
    if not MLB_FLIP_SPREAD or not html or "data-pick-card" not in html:
        return html

    def _cell_repl(tr: str, cls: str, home: str, away: str) -> str:
        def _sub(m: re.Match[str]) -> str:
            inner = m.group(2)
            flipped = _flip_spread_side_text(inner, home, away)
            return f"{m.group(1)}{flipped}{m.group(3)}"

        return re.sub(
            rf'(<td\b[^>]*\bclass="[^"]*\b{cls}\b[^"]*"[^>]*>)([^<]*)(</td>)',
            _sub,
            tr,
            count=1,
            flags=re.I,
        )

    def _patch_stack(stack: str) -> str:
        home_m = re.search(r'\bdata-home="([^"]*)"', stack, re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', stack, re.I)
        home = (home_m.group(1) if home_m else "").strip()
        away = (away_m.group(1) if away_m else "").strip()
        if not home or not away:
            names = re.findall(
                r'<div class="team-name">\s*([^<]+?)\s*</div>', stack, flags=re.I
            )
            if len(names) >= 2:
                away = away or names[0].strip()
                home = home or names[1].strip()
        if not home or not away:
            return stack

        def _flip_rl_row(tr: str) -> str:
            # Keep Books; flip model PL + XSharp only
            tr2 = _cell_repl(tr, "val-pl", home, away)
            tr2 = _cell_repl(tr2, "val-xs", home, away)
            return tr2

        def _rl_row_sub(m: re.Match[str]) -> str:
            return _flip_rl_row(m.group(0))

        stack2 = re.sub(
            r"<tr>\s*<td\b[^>]*\bclass=\"[^\"]*\bmarket-k\b[^\"]*\"[^>]*>"
            r"\s*(?:Run Line|Spread|Puck Line)\s*</td>"
            r"[\s\S]*?</tr>",
            _rl_row_sub,
            stack,
            flags=re.I,
        )

        def _attr_flip(tag: str, name: str) -> str:
            m = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
            if not m:
                return tag
            flipped = _flip_spread_side_text(m.group(1), home, away)
            if flipped == m.group(1):
                return tag
            esc = (
                flipped.replace("&", "&amp;")
                .replace('"', "&quot;")
                .replace("<", "&lt;")
            )
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )

        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack2, flags=re.I)
        if open_m:
            open_tag = open_m.group(1)
            open2 = _attr_flip(open_tag, "data-pl-spread")
            open2 = _attr_flip(open2, "data-xs-spread")
            # Never flip books
            if open2 != open_tag:
                stack2 = open2 + stack2[open_m.end() :]
        return stack2

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def rewrite_mlb_edge_chip_to_consensus(html: str) -> str:
    """Replace picks-card Edge chip (market 0.0%) with model consensus n/6 · pct%.

    Tip: Edge calculated using Model Consensus.
    """
    if not html or "edge-chip" not in html:
        return html

    tip = html_lib.escape("Edge calculated using Model Consensus.", quote=True)

    def _pct(stack: str) -> float | None:
        for pat in (
            r'\bdata-m-consensus="([^"]*)"',
            r'\bdata-conf="([^"]*)"',
        ):
            m = re.search(pat, stack, re.I)
            if not m:
                continue
            try:
                v = float(str(m.group(1)).strip().replace("%", ""))
                if v <= 1.5:
                    v *= 100.0
                return round(v, 1)
            except ValueError:
                continue
        return None

    def _agree_n(stack: str) -> int | None:
        sides = re.findall(
            r'<div class="pc-side\s+(home|away)[^"]*"',
            stack,
            re.I,
        )
        if len(sides) >= 3:
            home_n = sum(1 for s in sides if s.lower() == "home")
            away_n = len(sides) - home_n
            return max(home_n, away_n)
        pick_m = re.search(r'\bdata-pick="([^"]*)"', stack, re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', stack, re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', stack, re.I)
        if not pick_m or not home_m or not away_m:
            return None
        pick = (pick_m.group(1) or "").strip().lower()
        home = (home_m.group(1) or "").strip().lower()
        away = (away_m.group(1) or "").strip().lower()
        if not ((home and pick in home) or (away and pick in away)):
            return None
        keys = (
            "data-m-grinder2",
            "data-m-takedown",
            "data-m-edge",
            "data-m-xsharp",
            "data-m-efficiency",
            "data-m-consensus",
        )
        n = 0
        found = 0
        for k in keys:
            m = re.search(rf'\b{k}="([^"]*)"', stack, re.I)
            if not m:
                continue
            try:
                v = float(str(m.group(1)).strip().replace("%", ""))
            except ValueError:
                continue
            if v <= 1.5:
                v *= 100.0
            found += 1
            if v >= 50.0:
                n += 1
        return n if found >= 3 else None

    def _patch_stack(stack: str) -> str:
        if "edge-chip" not in stack:
            return stack
        agree = _agree_n(stack)
        pct = _pct(stack)
        if agree is None or pct is None:
            val = "—"
        else:
            val = f"{int(agree)}/6 · {pct:g}%"
        label = (
            "Edge "
            '<button type="button" class="h2h-info-btn pct-info-btn edge-cons-info" '
            f'data-tip="{tip}" aria-label="What is Edge?" '
            'aria-expanded="false" aria-haspopup="true">i</button>'
        )
        return re.sub(
            r'(<div class="line-chip edge-chip[^"]*"\s*>\s*)'
            r'<div class="line-chip-label">\s*Edge\s*</div>\s*'
            r'<div class="line-chip-val">[^<]*</div>',
            rf'\1<div class="line-chip-label">{label}</div>'
            rf'<div class="line-chip-val">{html_lib.escape(val)}</div>',
            stack,
            count=1,
            flags=re.I,
        )

    parts = re.split(r'(?=<div class="game-card-stack\b)', html)
    if len(parts) <= 1:
        parts = re.split(r'(?=<div class="game-card\b)', html)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def inject_mlb_run_line_confidence(html: str) -> str:
    """Add user-facing Run Line Confidence chips on MLB pick cards (sandbox only).

    Uses card data-* attrs + Books run line / Edge chips only.
    Never invents SP/bullpen/weather stats. No methodology dump in HTML.
    Loads run_line_v2 by file path (avoids mlb.optimization package init / pandas).
    Skipped for anonymous/paywalled pages so chart tabs cannot scrape RL confidence.
    """
    if not html or ("game-card-stack" not in html and "game-card" not in html):
        return html
    # Live paywall: anon pages lock View Details — do not inject RL confidence teaser.
    if "odds-pricing-locked" in html:
        return html
    if html.count("Run Line Confidence") >= 3:
        return html
    try:
        import importlib.util
        from pathlib import Path

        rl_path = Path(__file__).resolve().parent / "mlb_run_line_v2.py"
        spec = importlib.util.spec_from_file_location("mlb_run_line_v2_sandbox", rl_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {rl_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        projected_run_margin = mod.projected_run_margin
        run_line_confidence = mod.run_line_confidence
    except Exception as e:
        print(f"[mlb_ui_fixup] run_line_v2 import: {e}", flush=True)
        return html

    def _f(raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            return float(str(raw).strip().replace("%", ""))
        except ValueError:
            return None

    def _patch_stack(stack: str) -> str:
        if "Run Line Confidence" in stack:
            return stack
        # Prefer structured data attrs on game-card-stack
        home = re.search(r'\bdata-home="([^"]*)"', stack, re.I)
        away = re.search(r'\bdata-away="([^"]*)"', stack, re.I)
        conf_raw = re.search(r'\bdata-conf="([^"]*)"', stack, re.I)
        edge_raw = re.search(r'\bdata-edge="([^"]*)"', stack, re.I)
        books_spread = re.search(r'\bdata-books-spread="([^"]*)"', stack, re.I)
        pick = re.search(r'\bdata-pick="([^"]*)"', stack, re.I)

        home_name = (home.group(1) if home else "").strip()
        away_name = (away.group(1) if away else "").strip()
        conf_pct = _f(conf_raw.group(1) if conf_raw else None)
        market_edge = _f(edge_raw.group(1) if edge_raw else None)
        if market_edge is not None and abs(market_edge) > 1.5:
            market_edge = market_edge / 100.0

        home_win = None
        if conf_pct is not None:
            p = conf_pct / 100.0 if conf_pct > 1.5 else conf_pct
            pick_name = (pick.group(1) if pick else "").strip().lower()
            if home_name and pick_name and pick_name in home_name.lower():
                home_win = p
            elif away_name and pick_name and pick_name in away_name.lower():
                home_win = 1.0 - p
            else:
                home_win = p  # assume pick-side conf maps to home if unknown

        book_spread = None
        spread_txt = books_spread.group(1) if books_spread else ""
        if not spread_txt:
            rl_probe = re.search(
                r'line-chip-label">\s*Books run line\s*</div>\s*<div class="line-chip-val[^"]*">([^<]+)</div>',
                stack,
                re.I,
            )
            if rl_probe:
                spread_txt = rl_probe.group(1) or ""
        sm = re.search(r"([+\-]?\d+(?:\.\d+)?)", spread_txt)
        if sm:
            try:
                mag = abs(float(sm.group(1)))
                # Home-centric: negative if home is favorite on the run line
                if home_name and home_name.lower() in spread_txt.lower():
                    book_spread = -mag
                elif away_name and away_name.lower() in spread_txt.lower():
                    book_spread = mag
                else:
                    book_spread = -mag
            except ValueError:
                book_spread = None

        # Model agreement from pc-boxes when present
        agree_n = None
        models = list(
            re.finditer(
                r'<div class="pc-name">([^<]+)</div>\s*'
                r'<div class="pc-val">([^<]*)</div>\s*'
                r'<div class="pc-side[^"]*"',
                stack,
                re.I,
            )
        )
        if models and home_win is not None:
            agree_n = 0
            pick_home = home_win >= 0.5
            for m in re.finditer(
                r'<div class="pc-side\s+(home|away)[^"]*"[^>]*>',
                stack,
                re.I,
            ):
                lean_home = m.group(1).lower() == "home"
                if lean_home == pick_home:
                    agree_n += 1

        margin = projected_run_margin(home_win_prob=home_win, book_spread=book_spread)
        conf = run_line_confidence(
            margin,
            models_agree_n=agree_n,
            market_edge=market_edge,
        )
        _rl_tip = (
            "Run Line Confidence is how strongly our model favors its run-line "
            "side, from 0 to 100. It is the confidence behind that run-line lean "
            "— not a moneyline or totals pick."
        )
        _rl_tip_esc = html_lib.escape(_rl_tip, quote=True)
        chip = (
            '<div class="line-chip rl-confidence-chip">'
            '<div class="line-chip-label">Run Line Confidence '
            '<button type="button" class="h2h-info-btn pct-info-btn rl-conf-info" '
            f'data-tip="{_rl_tip_esc}" aria-label="What is Run Line Confidence?" '
            'aria-expanded="false" aria-haspopup="true">i</button></div>'
            f'<div class="line-chip-val">{conf:.0f}</div>'
            "</div>"
        )
        rl = re.search(
            r'(<div class="line-chip">\s*'
            r'<div class="line-chip-label">\s*Books run line\s*</div>\s*'
            r'<div class="line-chip-val[^"]*">[^<]*</div>\s*</div>)',
            stack,
            re.I,
        )
        if rl:
            return stack.replace(rl.group(1), rl.group(1) + "\n    " + chip, 1)
        # Face no longer shows Books run line — put RL confidence first in strip.
        ls = re.search(r'(<div class="lines-strip">)', stack, re.I)
        if ls:
            return stack.replace(ls.group(1), ls.group(1) + "\n    " + chip, 1)
        return stack

    parts = re.split(r'(?=<div class="game-card-stack\b)', html)
    if len(parts) <= 1:
        parts = re.split(r'(?=<div class="game-card\b)', html)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


_MLB_BODY = 'body.sport-mlb,body[data-sport="MLB"],body[data-sport="mlb"]'


def _mlb_sel(suffix: str) -> str:
    """Expand comma body selectors so each gets the descendant suffix.

    ``body.a,body.b .x`` wrongly styles body.a. Need ``body.a .x,body.b .x``.
    """
    suf = (suffix or "").strip()
    parts = [p.strip() for p in _MLB_BODY.split(",") if p.strip()]
    if not suf:
        return ",".join(parts)
    if not suf.startswith((" ", ">", "+", "~", ":")):
        suf = " " + suf
    return ",".join(p + suf for p in parts)


def strip_mlb_picks_chart_total_ev(html: str) -> str:
    """Remove Total EV column from MLB picks chart (inline template JS)."""
    if not html or "picksChartMarket" not in html:
        return html
    html = re.sub(r"\+_infoTh\('Total EV',\s*TOTAL_EV_TIP\);", "", html, flags=re.I)
    html = re.sub(r'\+_pcInfoTh\("Total EV",\s*TOTAL_EV_TIP\);', "", html, flags=re.I)
    html = re.sub(
        r"const TOTAL_EV_TIP\s*=\s*MLB_CHART[\s\S]*?Not moneyline Edge\.';\s*",
        "",
        html,
        count=1,
    )
    html = re.sub(
        r"const tev = IS_PREMIUM \? _esc\(_totalEvTxt\(st\)\) : '🔒';\s*",
        "",
        html,
    )
    html = re.sub(r'<td class="num">\'\+tev\+\'</td>', "", html)
    html = re.sub(
        r'<td class="num">\'\s*\+\s*_pcTotalEvTxt\(st\)\s*\+\s*\'</td>',
        "",
        html,
        flags=re.I,
    )
    return html


def ensure_mlb_pick_conf_no_scroll(html: str) -> str:
    """MLB picks: same 3-up card size as NFL; Pick Confidence 3×2, no 520px blowup."""
    if not html:
        return html
    html = re.sub(
        r'<style id="mlb-pick-conf-no-scroll">.*?</style>',
        "",
        html,
        count=1,
        flags=re.I | re.S,
    )
    # Keep CSS braces in non-f-string fragments so we do not emit `}}`.
    css = (
        '<style id="mlb-pick-conf-no-scroll">'
        f"{_mlb_sel('.games-grid')}{{display:grid!important;"
        "grid-template-columns:repeat(3,minmax(0,1fr))!important;"
        "gap:12px!important;align-items:start!important;}"
        f"{_mlb_sel('.games-grid>.game-card-stack')}{{max-width:none!important;"
        "width:100%!important;min-width:0!important;margin:0!important;"
        "overflow:visible!important;}"
        f"{_mlb_sel('.game-card.pick-card')}{{overflow:visible!important;}}"
        f"{_mlb_sel('.pick-conf-bar')}{{overflow:visible!important;"
        "max-width:100%!important;padding-bottom:14px!important;}"
        f"{_mlb_sel('.pick-conf-grid')}{{display:grid!important;"
        "grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:8px!important;"
        "min-width:0!important;width:100%!important;}"
        f"{_mlb_sel('.pc-box')}{{min-width:0!important;width:100%!important;"
        "box-sizing:border-box!important;overflow:visible!important;"
        "padding:8px 6px!important;min-height:88px!important;}"
        f"{_mlb_sel('.pc-name')},{_mlb_sel('.pc-side')}{{word-break:normal!important;"
        "overflow-wrap:break-word!important;hyphens:none!important;}"
        f"{_mlb_sel('.pc-name')}{{font-size:0.7em!important;line-height:1.2!important;}}"
        f"{_mlb_sel('.pc-side')}{{font-size:0.62em!important;padding:2px 4px!important;}}"
        f"{_mlb_sel('.pc-val')}{{font-size:0.95em!important;}}"
        "@media(max-width:1100px){"
        f"{_mlb_sel('.games-grid')}{{grid-template-columns:repeat(2,minmax(0,1fr))!important;}}"
        "}"
        "@media(max-width:768px){"
        f"{_mlb_sel('.games-grid')}{{grid-template-columns:1fr!important;}}"
        f"{_mlb_sel('.pick-conf-grid')}{{grid-template-columns:repeat(3,minmax(0,1fr))!important;}}"
        "}"
        "</style>"
    )
    if re.search(r"</head\s*>", html, re.I):
        return re.sub(r"</head\s*>", css + "</head>", html, count=1, flags=re.I)
    if re.search(r"</body\s*>", html, re.I):
        return re.sub(r"</body\s*>", css + "</body>", html, count=1, flags=re.I)
    return html + css


def _shorten_mlb_results_pc_sides(html: str) -> str:
    if not html or "pc-side" not in html:
        return html

    def _repl(m: re.Match[str]) -> str:
        body = m.group(2)
        mark = "✅" if "✅" in body else ("❌" if "❌" in body else "")
        if not mark:
            return m.group(0)
        return f"{m.group(1)}{mark}{m.group(3)}"

    return re.sub(
        r'(<div class="pc-side[^"]*"[^>]*>)([^<]*)(</div>)',
        _repl,
        html,
        flags=re.I,
    )


def ensure_mlb_results_card_layout(html: str) -> str:
    """MLB results cards: 3-up grid, pick-conf 3×2, tallies 3-col."""
    if not html:
        return html
    html = _shorten_mlb_results_pc_sides(html)
    if 'id="mlb-results-card-layout"' in html:
        return html
    css = (
        '<style id="mlb-results-card-layout">'
        f"{_mlb_sel('.games-grid')},{_mlb_sel('.results-grid')}{{display:grid!important;"
        "grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:16px!important;}"
        f"{_mlb_sel('.games-grid>.game-card')},{_mlb_sel('.games-grid>.game-card-stack')}{{"
        "width:100%!important;min-width:0!important;overflow:hidden!important;}"
        f"{_mlb_sel('.pick-conf-grid')}{{display:grid!important;"
        "grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:8px!important;}"
        f"{_mlb_sel('.daily-tally-grid')}{{display:grid!important;"
        "grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:10px!important;}"
        f"{_mlb_sel('.model-grid:has(>.model-card)')}{{display:grid!important;"
        "grid-template-columns:repeat(6,minmax(0,1fr))!important;gap:10px!important;}"
        "</style>"
    )
    if re.search(r"</body\s*>", html, re.I):
        return re.sub(r"</body\s*>", css + "</body>", html, count=1, flags=re.I)
    return html + css


def apply_mlb_picks_pagespeed_a11y(html: str) -> str:
    """PageSpeed + a11y fixups for /mlb-picks only (owner 2026-09-14 unlock).

    Does not restyle card layout or invent model numbers. Keeps Google tags;
    defers chart CSS and shrinks ESPN logo / share preview bytes.
    """
    if not html:
        return html

    # ESPN logos: many /100/ assets 404 (sf, kc, mia, sd, tb, wsh, …).
    # Keep a 100px face via the combiner resizing a working /500/ source.
    def _mlb_logo_to_combiner(m: re.Match[str]) -> str:
        abbr = m.group(1).lower()
        return (
            "https://a.espncdn.com/combiner/i?"
            f"img=/i/teamlogos/mlb/500/{abbr}.png&h=100&w=100"
        )

    html = re.sub(
        r"https://a\.espncdn\.com/i/teamlogos/mlb/(?:500|100)/([a-z0-9]+)\.png",
        _mlb_logo_to_combiner,
        html,
        flags=re.I,
    )

    # If an old sync Ads tag snuck into HTML, strip it — base include already
    # loads AW-18345189026 after idle (do not remove the tag from the site).
    html = re.sub(
        r'<script\b[^>]*src="https://www\.googletagmanager\.com/gtag/js\?id=AW-[^"]*"[^>]*>\s*</script>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"<script>\s*window\.dataLayer\s*=\s*window\.dataLayer\s*\|\|\s*\[\];\s*"
        r"function\s+gtag\(\)\s*\{[^}]*\}\s*gtag\('js',\s*new Date\(\)\);\s*"
        r"gtag\('config',\s*'AW-[^']+'\);\s*</script>\s*",
        "",
        html,
        flags=re.I,
    )

    # Share preview: request a downscaled JPEG for the in-page thumb; keep
    # download/fullscreen links on the full asset.
    def _share_preview_img(m: re.Match[str]) -> str:
        tag = m.group(0)
        src_m = re.search(r'\bsrc="([^"]+)"', tag, flags=re.I)
        if not src_m:
            return tag
        src = src_m.group(1)
        if "w=" not in src and "/share/predictions/" in src:
            joiner = "&" if "?" in src else "?"
            src = f"{src}{joiner}w=640"
        tag = re.sub(r'\bsrc="[^"]+"', f'src="{src}"', tag, count=1, flags=re.I)
        if re.search(r'\balt="[^"]*"', tag, flags=re.I):
            tag = re.sub(r'\balt="[^"]*"', 'alt="MLB predictions share image"', tag, count=1, flags=re.I)
        else:
            tag = tag.replace("<img ", '<img alt="MLB predictions share image" ', 1)
        if "loading=" not in tag.lower():
            tag = tag.replace("<img ", '<img loading="lazy" decoding="async" width="420" height="747" ', 1)
        return tag

    html = re.sub(
        r'<img\b[^>]*src="[^"]*/share/predictions/[^"]+"[^>]*>',
        _share_preview_img,
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<a\b[^>]*\bclass="[^"]*\bsocial-image-link\b[^"]*"[^>]*)(>)',
        lambda m: (
            m.group(1)
            if re.search(r"\baria-label=", m.group(1), flags=re.I)
            else m.group(1) + ' aria-label="Open MLB predictions share image"'
        )
        + m.group(2),
        html,
        count=1,
        flags=re.I,
    )

    # Cards|Chart tablist requires role=tab children.
    def _tab_btn(m: re.Match[str]) -> str:
        tag = m.group(0)
        active = bool(re.search(r'\bclass="[^"]*\bactive\b', tag, flags=re.I))
        if not re.search(r'\brole="tab"', tag, flags=re.I):
            tag = tag.replace("<button ", '<button role="tab" ', 1)
        if re.search(r"\baria-selected=", tag, flags=re.I):
            tag = re.sub(
                r'\baria-selected="[^"]*"',
                f'aria-selected="{"true" if active else "false"}"',
                tag,
                count=1,
                flags=re.I,
            )
        else:
            tag = tag.replace(
                "<button ",
                f'<button aria-selected="{"true" if active else "false"}" ',
                1,
            )
        return tag

    html = re.sub(
        r'<button\b[^>]*\bid="pv(?:Cards|Chart)Btn"[^>]*>',
        _tab_btn,
        html,
        flags=re.I,
    )
    if 'id="mlb-pv-aria-sync"' not in html:
        html += (
            '<script id="mlb-pv-aria-sync">'
            "(function(){function sync(){var c=document.getElementById('pvCardsBtn'),"
            "h=document.getElementById('pvChartBtn');if(!c||!h)return;"
            "var chart=h.classList.contains('active');"
            "c.setAttribute('aria-selected',chart?'false':'true');"
            "h.setAttribute('aria-selected',chart?'true':'false');}"
            "var _s=window.setPicksView;if(typeof _s==='function'){"
            "window.setPicksView=function(mode){_s(mode);sync();};}"
            "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',sync);"
            "else sync();})();</script>"
        )

    # html lang + main landmark
    if re.search(r"<html\b", html, flags=re.I) and not re.search(
        r"<html\b[^>]*\blang=", html, flags=re.I
    ):
        html = re.sub(r"<html\b", '<html lang="en"', html, count=1, flags=re.I)
    if not re.search(r"<main\b", html, flags=re.I) and not re.search(
        r'\brole=["\']main["\']', html, flags=re.I
    ):
        # Prefer wrapping the picks board; fall back to body child marker.
        if 'class="picks-view-controls"' in html:
            html = html.replace(
                '<div class="picks-view-controls"',
                '<main id="main-content"><div class="picks-view-controls"',
                1,
            )
            if re.search(r"</body\s*>", html, flags=re.I):
                html = re.sub(r"</body\s*>", "</main></body>", html, count=1, flags=re.I)
        elif re.search(r"<body\b[^>]*>", html, flags=re.I):
            html = re.sub(
                r"(<body\b[^>]*>)",
                r'\1<main id="main-content">',
                html,
                count=1,
                flags=re.I,
            )
            html = re.sub(r"</body\s*>", "</main></body>", html, count=1, flags=re.I)

    # Drop unused Google Fonts preconnect/Oswald on MLB picks (system fonts).
    html = re.sub(
        r'<link[^>]+href="https://fonts\.googleapis\.com[^"]*"[^>]*>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<link[^>]+href="https://fonts\.gstatic\.com[^"]*"[^>]*>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"<noscript>\s*<link[^>]+fonts\.googleapis\.com[^>]*>\s*</noscript>\s*",
        "",
        html,
        flags=re.I,
    )

    # Defer chart-only CSS (not needed for Cards LCP).
    def _defer_css(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "onload=" in tag.lower() or "media=" in tag.lower():
            return tag
        return tag.replace(
            'rel="stylesheet"',
            'rel="stylesheet" media="print" onload="this.media=\'all\'"',
            1,
        )

    html = re.sub(
        r'<link[^>]+href="[^"]*(?:mlb-picks-chart|picks-chart|pl-info-tips)\.css[^"]*"[^>]*>',
        _defer_css,
        html,
        flags=re.I,
    )

    if 'id="mlb-psi-a11y-css"' not in html:
        # Contrast: keep card chrome, darken low-contrast text PSI flagged.
        css = (
            '<style id="mlb-psi-a11y-css">'
            "body.sport-mlb .win-pct{color:#0f172a!important;}"
            "body.sport-mlb .model-tag{color:#0f172a!important;}"
            "body.sport-mlb .ml-num{color:#0f172a!important;}"
            "body.sport-mlb .ml-num.fav{color:#14532d!important;}"
            "body.sport-mlb .analysis-toggle{color:#1e293b!important;}"
            "body.sport-mlb .team-slot span[style*='opacity']{opacity:1!important;color:#475569!important;}"
            "body.sport-mlb .date-bubble.today.today-bubble,"
            "body.sport-mlb .date-bubble.today{"
            "background:#047857!important;color:#fff!important;}"
            "body.sport-mlb .date-bubble.today span{"
            "background:#047857!important;color:#fff!important;}"
            "body.sport-mlb .date-section{min-height:120px;}"
            "body.sport-mlb .social-image-link{min-height:320px;}"
            "</style>"
        )
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(r"</head\s*>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    return html


def _mlb_frozen_results_snapshot() -> str:
    sandbox = Path("/Users/nimamesghali/Sports Sandbox")
    candidates = (
        sandbox / "mlb_FROZEN_SIGNED_OFF_20260828" / "mlb-results.snapshot.html",
        sandbox
        / "independent_sports"
        / "_archives"
        / "mlb_DONE_20260828"
        / "mlb-results.snapshot.html",
        sandbox / "mlb_DONE_premerge_20260828" / "mlb-results.snapshot.html",
    )
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 100_000:
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _mlb_merged_consensus_finals(live_html: str = "") -> list:
    """Live results HTML + frozen snapshot — same merge as results consensus."""
    try:
        from mlb_consensus_hub import (
            _dedupe_finals_by_game,
            _extract_raw_mlb_finals_from_html,
            _merge_consensus_finals,
        )
    except Exception:
        return []
    live = _extract_raw_mlb_finals_from_html(live_html or "", limit=800) or []
    if not live:
        try:
            import sys

            mod = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
            cache = getattr(mod, "_SPORT_RESULTS_CACHE", None) or {}
            for key in (
                "MLB_daily_results_html_v5",
                "MLB_daily_results_html_v4",
                "MLB_daily_results_html_v3",
            ):
                entry = cache.get(key)
                if isinstance(entry, dict):
                    src = entry.get("html") or ""
                    if src and len(src) > 500:
                        live = _extract_raw_mlb_finals_from_html(src, limit=800) or []
                        if live:
                            break
        except Exception:
            pass
    snap = _mlb_frozen_results_snapshot()
    snap_finals = (
        _extract_raw_mlb_finals_from_html(snap, limit=800) if snap else []
    ) or []
    return _dedupe_finals_by_game(_merge_consensus_finals(live, snap_finals))


def strip_mlb_face_books_run_total(html: str) -> str:
    """Remove Books run line / Books total from the pick-card face strip.

    Odds & Lines table in details still shows book lines.
    """
    if not html:
        return html
    html = re.sub(
        r'<div class="line-chip">\s*'
        r'<div class="line-chip-label">\s*Books run line\s*</div>\s*'
        r'<div class="line-chip-val[^"]*">[\s\S]*?</div>\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="line-chip">\s*'
        r'<div class="line-chip-label">\s*Books total\s*</div>\s*'
        r'<div class="line-chip-val[^"]*">[\s\S]*?</div>\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    return html


def _parse_amer_ml(raw: str) -> int | None:
    s = (raw or "").strip().replace(",", "").replace("−", "-")
    if not s or s in {"—", "–", "-", "N/A", "n/a"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _face_ml_favorite_side(stack: str, *, which: str) -> str | None:
    """HOME/AWAY from face Books or Prediction Lab moneylines (more negative = fav)."""
    cls = "face-books-ml" if which == "books" else "face-pl-ml"
    nums = re.findall(
        rf'<div class="ml-line {cls}">\s*'
        r'<span class="ml-src[^"]*">[^<]*</span>\s*'
        r'<span class="ml-num[^"]*">\s*([^<]+?)\s*</span>',
        stack,
        flags=re.I,
    )
    if len(nums) < 2:
        return None
    away_ml = _parse_amer_ml(nums[0])
    home_ml = _parse_amer_ml(nums[1])
    if away_ml is None or home_ml is None or away_ml == home_ml:
        return None
    return "HOME" if home_ml < away_ml else "AWAY"


def _wl_pct(grades: list) -> tuple[int, int, float | None]:
    w = sum(1 for g in grades if g == "WIN")
    l = sum(1 for g in grades if g == "LOSS")
    if w + l <= 0:
        return 0, 0, None
    return w, l, round(100.0 * w / (w + l), 1)


def inject_mlb_consensus_and_pl_vs_books_chips(html: str) -> str:
    """Face chips: Consensus Historical Record + PL vs Books (agree/disagree L7).

    Replaces the room left by removing Books run line / Books total.
    """
    if not html or "data-pick-card" not in html or "lines-strip" not in html:
        return html
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from mlb_consensus_hub import (
            _pl_vs_books_rows_from_finals,
            _pl_vs_books_slices,
        )
        from team_results_charts import (
            _inject_consensus_hist_chips,
            set_results_chart_source,
        )
    except Exception as e:
        print(f"[mlb_ui_fixup] consensus/pl-vs import: {e}", flush=True)
        return html

    finals = _mlb_merged_consensus_finals("")
    if finals:
        # Seed chart source so consensus hist can grade patterns from MLB finals.
        try:
            # Minimal HTML seed is not needed — patch lookup via inject + models.
            pass
        except Exception:
            pass

    # Consensus Historical Record (same injector as NCAAF/CFL; MLB finals below).
    try:
        from team_results_charts import _consensus_d7_combo_lookup as _orig_lookup
        import team_results_charts as trc

        mlb_models = (
            "Grinder2",
            "Takedown",
            "Edge",
            "XSharp",
            "Sharp Consensus",
            "Efficiency",
        )

        def _mlb_lookup(sport: str):
            sport_u = trc._sport_key(sport)
            if sport_u != "MLB":
                return _orig_lookup(sport)
            try:
                from mlb_consensus_hub import (
                    _consensus_combo_period_data,
                    _consensus_wl,
                )
            except Exception:
                return {}
            if not finals:
                return {}
            data = _consensus_combo_period_data(finals, sport="mlb")
            if not data:
                return {}
            out: dict = {}
            filter_combo = data["filter_combo"]
            d7_items = list(data.get("d7") or [])
            if not d7_items:
                ln_key = str(data.get("ln_key") or "")[:10]
                if len(ln_key) == 10 and ln_key[4] == "-":
                    try:
                        ln_dt = datetime.strptime(ln_key, "%Y-%m-%d")
                        cut = (ln_dt - timedelta(days=6)).strftime("%Y-%m-%d")
                        pool = list(data.get("d30") or []) or list(data.get("ln") or [])
                        d7_items = [
                            a
                            for a in pool
                            if cut <= str(a.get("game_date") or "")[:10] <= ln_key
                        ]
                        if not d7_items and data.get("ln"):
                            d7_items = list(data.get("ln") or [])
                    except ValueError:
                        pass
            for folded, keys in (data.get("combo_keys") or {}).items():
                for key in keys:
                    items = filter_combo(d7_items, folded=folded, key=key)
                    w, l, _p, pct = _consensus_wl(items)
                    out[(int(folded), tuple(key))] = (w, l, pct)
            return out

        trc._consensus_d7_combo_lookup = _mlb_lookup  # type: ignore[assignment]
        try:
            html = _inject_consensus_hist_chips(
                html, sport="MLB", models=mlb_models
            )
        finally:
            trc._consensus_d7_combo_lookup = _orig_lookup  # type: ignore[assignment]
    except Exception as e:
        print(f"[mlb_ui_fixup] consensus hist inject: {e}", flush=True)

    # PL vs Books agree/disagree Last 7 record for this card's ML lean.
    try:
        rows = _pl_vs_books_rows_from_finals(finals) if finals else []
        now = datetime.now(ZoneInfo("America/New_York"))
        today = now.strftime("%Y-%m-%d")
        cut7 = (now.date() - timedelta(days=7)).strftime("%Y-%m-%d")
        d7 = [
            r
            for r in rows
            if cut7 <= str(r.get("game_date") or "")[:10] < today
        ]
        if not d7 and rows:
            # Mid-gap fallback: last calendar night's cluster.
            past = sorted(
                {
                    str(r.get("game_date") or "")[:10]
                    for r in rows
                    if str(r.get("game_date") or "")[:10] < today
                }
            )
            if past:
                ln = past[-1]
                cut = (
                    datetime.strptime(ln, "%Y-%m-%d").date() - timedelta(days=6)
                ).strftime("%Y-%m-%d")
                d7 = [
                    r
                    for r in rows
                    if cut <= str(r.get("game_date") or "")[:10] <= ln
                ]
        slices = _pl_vs_books_slices(d7) if d7 else {
            "books_pl_agree": [],
            "books_pl_disagree": [],
        }
        aw, al, ap = _wl_pct(slices.get("books_pl_agree") or [])
        dw, dl, dp = _wl_pct(slices.get("books_pl_disagree") or [])

        def _pl_chip(stack: str) -> str:
            if "pl-vs-books-chip" in stack or "lines-strip" not in stack:
                return stack
            book = _face_ml_favorite_side(stack, which="books")
            pl = _face_ml_favorite_side(stack, which="pl")
            if book and pl and book == pl:
                rec = f"{aw}-{al}"
                pct_s = f"{ap:.0f}%" if ap is not None else "—"
                val = f"Agree: {rec} ({pct_s}) — Last 7 Days"
            elif book and pl:
                rec = f"{dw}-{dl}"
                pct_s = f"{dp:.0f}%" if dp is not None else "—"
                val = f"Disagree: {rec} ({pct_s}) — Last 7 Days"
            else:
                val = "—"
            tip = html_lib.escape(
                "PL vs Books uses this game's moneyline favorites. "
                "Agree = same side; Disagree = split. Record is Last 7 Days "
                "from graded finals (Disagree grades the PL favorite).",
                quote=True,
            )
            chip = (
                '<div class="line-chip pl-vs-books-chip">'
                '<div class="line-chip-label">PL vs Books '
                '<button type="button" class="h2h-info-btn pct-info-btn pl-vs-info" '
                f'data-tip="{tip}" aria-label="What is PL vs Books?" '
                'aria-expanded="false" aria-haspopup="true">i</button></div>'
                f'<div class="line-chip-val">{html_lib.escape(val)}</div></div>'
            )
            # Prefer after consensus hist; else after RL confidence; else strip start.
            for anchor in (
                r'(<div class="line-chip consensus-hist-chip">[\s\S]*?</div>\s*</div>)',
                r'(<div class="line-chip rl-confidence-chip">[\s\S]*?</div>\s*</div>)',
            ):
                m = re.search(anchor, stack, flags=re.I)
                if m:
                    return stack.replace(m.group(1), m.group(1) + "\n    " + chip, 1)
            m = re.search(r'(<div class="lines-strip">)', stack, flags=re.I)
            if m:
                return stack.replace(m.group(1), m.group(1) + "\n    " + chip, 1)
            return stack

        parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
        if len(parts) >= 2:
            html = parts[0] + "".join(_pl_chip(p) for p in parts[1:])
    except Exception as e:
        print(f"[mlb_ui_fixup] pl vs books inject: {e}", flush=True)

    return html


def fill_mlb_card_clocks(html: str) -> str:
    """Fill Upcoming kickoff clocks from ESPN when cards lack a real time."""
    if not html or "data-pick-card" not in html:
        return html
    if "Upcoming" not in html and "TBD" not in html:
        return html
    dates = sorted(set(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html)))
    if not dates:
        dates = sorted(set(re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"', html)))
    clocks: dict[tuple[str, str], str] = {}
    try:
        import json
        import urllib.request
        from datetime import datetime
        from zoneinfo import ZoneInfo

        def _fmt(raw: str) -> str:
            s = (raw or "").strip()
            if not s:
                return ""
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            local = dt.astimezone(ZoneInfo("America/New_York"))
            return f"{local.strftime('%I:%M %p').lstrip('0')} ET"

        def _key(name: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", (name or "").lower())

        for gd in dates or []:
            ds = gd.replace("-", "")
            url = (
                "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/"
                f"scoreboard?dates={ds}&limit=50"
            )
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
            for ev in data.get("events") or []:
                clock = _fmt(str(ev.get("date") or ""))
                if not clock:
                    continue
                comps = (ev.get("competitions") or [{}])[0] or {}
                home = away = ""
                for c in comps.get("competitors") or []:
                    team = c.get("team") or {}
                    name = (team.get("displayName") or team.get("name") or "").strip()
                    if str(c.get("homeAway") or "") == "home":
                        home = name
                    elif str(c.get("homeAway") or "") == "away":
                        away = name
                if home and away:
                    clocks[(_key(away), _key(home))] = clock
    except Exception as e:
        print(f"[mlb_ui_fixup] espn clocks: {e}", flush=True)
        return html
    if not clocks:
        return html

    def _attr(open_tag: str, *names: str) -> str:
        for name in names:
            m = re.search(rf'\b{name}="([^"]*)"', open_tag, flags=re.I)
            if m:
                return html_lib.unescape((m.group(1) or "").strip())
        return ""

    def _set_attr(tag: str, name: str, value: str) -> str:
        if re.search(rf'\b{name}="', tag, flags=re.I):
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{html_lib.escape(value, quote=True)}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{html_lib.escape(value, quote=True)}">'

    def _patch(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        cur = _attr(open_tag, "data-time")
        cur_low = re.sub(r"\s+", " ", cur).strip().lower()
        if cur and cur_low not in {"upcoming", "tbd", "tba", "", "—", "–", "-"} and re.search(
            r"\d", cur
        ):
            return stack
        if cur_low in {"final", "live"} or cur_low.startswith("final") or cur_low.startswith("live"):
            return stack
        home = _attr(open_tag, "data-home-full", "data-home")
        away = _attr(open_tag, "data-away-full", "data-away")
        clock = clocks.get(
            (
                re.sub(r"[^a-z0-9]+", "", away.lower()),
                re.sub(r"[^a-z0-9]+", "", home.lower()),
            ),
            "",
        )
        if not clock:
            return stack
        open_tag = _set_attr(open_tag, "data-time", clock)
        rest = re.sub(
            r'(class="game-time">)(?:Upcoming|TBD|TBA|—|–|-)?(</span>)',
            rf"\g<1>{html_lib.escape(clock)}\2",
            rest,
            count=1,
            flags=re.I,
        )
        return open_tag + rest

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch(p) for p in parts[1:])


def apply_mlb_picks_fixups(html: str) -> str:
    """Signed-off MLB picks publish layer (no chrome swap).

    Order matches sandbox: flip model run-line display, enrich chart attrs,
    inject Run Line Confidence chips. Chart market tabs live in the template
    (espn_predictions_template.html) — do not inject sandbox picks-chart.js
    when setPicksChartMarket is already present.
    """
    if not html:
        return html
    html = ensure_pl2_header_css(html)
    if "data-pick-card" not in html:
        return html
    try:
        html = dedupe_game_card_stacks(html)
    except Exception as e:
        print(f"[mlb_ui_fixup] dedupe: {e}", flush=True)
    try:
        html = fill_mlb_card_clocks(html)
    except Exception as e:
        print(f"[mlb_ui_fixup] clocks: {e}", flush=True)
    html = flip_mlb_model_spread_display(html)
    html = enrich_mlb_chart_data_attrs(html)
    html = strip_mlb_face_books_run_total(html)
    html = inject_mlb_run_line_confidence(html)
    html = rewrite_mlb_edge_chip_to_consensus(html)
    html = inject_mlb_consensus_and_pl_vs_books_chips(html)
    # Owner requirement: prediction cards stay expanded (Odds & Lines + Pick Confidence).
    html = open_all_pick_details_html(html)
    try:
        html = ensure_mlb_pick_conf_no_scroll(html)
    except Exception as e:
        print(f"[mlb_ui_fixup] pick-conf layout: {e}", flush=True)
    try:
        html = apply_mlb_picks_pagespeed_a11y(html)
    except Exception as e:
        print(f"[mlb_ui_fixup] pagespeed/a11y: {e}", flush=True)
    return html


def open_all_pick_details_html(html: str) -> str:
    """Server-side: expand all pick/result card details (do not rely on JS alone)."""
    if not html or "card-details" not in html:
        return html
    # Remove hidden= / hidden attribute anywhere on card-details tags
    def _unhide_details(m: re.Match[str]) -> str:
        tag = m.group(0)
        tag = re.sub(r"\s*\bhidden\b(?:=(['\"][^'\"]*['\"]))?", "", tag, flags=re.I)
        return tag

    html = re.sub(
        r"<div\b[^>]*\bclass=\"[^\"]*\bcard-details\b[^\"]*\"[^>]*>",
        _unhide_details,
        html,
        flags=re.I,
    )
    html = re.sub(
        r"(<(?:button|a)\b[^>]*\bview-details-btn\b[^>]*\baria-expanded=\")false(\")",
        r"\1true\2",
        html,
        flags=re.I,
    )
    # Cover "View Details" and "View details" / chevron variants.
    html = re.sub(
        r"(<(?:button|a)\b[^>]*\bview-details-btn\b[^>]*>)\s*View\s+Details\s*",
        r"\1Less details ",
        html,
        flags=re.I,
    )

    def _expand_tag(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "is-expanded" in tag:
            return tag
        return re.sub(
            r'\bclass="([^"]*)"',
            lambda cm: f'class="{cm.group(1)} is-expanded"',
            tag,
            count=1,
        )

    # Expand stacks and individual cards so the first card cannot stay closed.
    html = re.sub(
        r'<div\b[^>]*\bclass="[^"]*\b(?:game-card-stack|game-card|pick-card)\b[^"]*"[^>]*>',
        _expand_tag,
        html,
        flags=re.I,
    )
    # CSS insurance if live JS re-collapses
    if 'id="mlb-open-pick-details"' not in html:
        css = (
            '<style id="mlb-open-pick-details">'
            ".card-details[hidden]{display:none!important;}"
            ".game-card-stack.is-expanded .card-details:not([hidden]),"
            ".game-card.is-expanded .card-details:not([hidden]),"
            ".pick-card.is-expanded .card-details:not([hidden]){display:block!important;}"
            ".view-details-btn[aria-expanded='true'] .chevron{transform:rotate(-180deg);}"
            "</style>"
        )
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    return html


def apply_mlb_results_fixups(html: str, market: str | None = None) -> str:
    """MLB results publish layer: season efficiency fill + analytics cards + Cards|Chart."""
    if not html:
        return html
    if not market:
        try:
            from flask import has_request_context, request

            if has_request_context():
                market = (request.args.get("market") or "").strip().lower()
        except Exception:
            market = None
    if market not in ("moneyline", "spread", "totals"):
        market = "moneyline"
    html = ensure_pl2_header_css(html)
    try:
        from mlb_results_ui import (
            fix_mlb_results_display,
            inject_mlb_results_view_toggle,
            strip_inert_results_market_toggle,
        )
        html = fix_mlb_results_display(html)
        html = ensure_mlb_results_card_layout(html)
        html = strip_inert_results_market_toggle(html)
        html = inject_mlb_results_view_toggle(html, active="normal")
        try:
            import re
            from datetime import datetime
            from pathlib import Path
            from zoneinfo import ZoneInfo

            # Staging sign-off: mlb_consensus_hub (+ mlb_three_way_consensus).
            # Same live+frozen merge as hub/_ensure_mlb_results_consensus.
            # Do not replace iso_hub/team_tabbed_results.py.
            from mlb_consensus_hub import (
                _dedupe_finals_by_game,
                _extract_raw_mlb_finals_from_html,
                _merge_consensus_finals,
                inject_consensus_records_html,
            )

            def _frozen_results_snapshot() -> str:
                sandbox = Path("/Users/nimamesghali/Sports Sandbox")
                candidates = (
                    sandbox
                    / "mlb_FROZEN_SIGNED_OFF_20260828"
                    / "mlb-results.snapshot.html",
                    sandbox
                    / "independent_sports"
                    / "_archives"
                    / "mlb_DONE_20260828"
                    / "mlb-results.snapshot.html",
                    sandbox
                    / "mlb_DONE_premerge_20260828"
                    / "mlb-results.snapshot.html",
                )
                for path in candidates:
                    try:
                        if path.is_file() and path.stat().st_size > 100_000:
                            return path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                return ""

            live_finals = _extract_raw_mlb_finals_from_html(html, limit=800) or []
            snap = _frozen_results_snapshot()
            snap_finals = (
                _extract_raw_mlb_finals_from_html(snap, limit=800) if snap else []
            ) or []
            # Live first as primary; frozen fills older dates — date+matchup dedupe.
            finals = _dedupe_finals_by_game(
                _merge_consensus_finals(live_finals, snap_finals)
            )

            ln_key = None
            m_ln = re.search(
                r"Last Night'?s MLB Results\s*[—\-]\s*(\d{4}-\d{2}-\d{2})",
                html or "",
                flags=re.I,
            )
            if m_ln:
                ln_key = m_ln.group(1)
            today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
            if ln_key:
                ln_n = sum(
                    1
                    for g in finals
                    if str(g.get("game_date") or "")[:10] == ln_key
                )
                if ln_n == 0:
                    past = sorted(
                        {
                            str(g.get("game_date") or "")[:10]
                            for g in finals
                            if str(g.get("game_date") or "")[:10]
                            and str(g.get("game_date") or "")[:10] < today
                        }
                    )
                    if past:
                        ln_key = past[-1]
            elif finals:
                past = sorted(
                    {
                        str(g.get("game_date") or "")[:10]
                        for g in finals
                        if str(g.get("game_date") or "")[:10]
                        and str(g.get("game_date") or "")[:10] < today
                    }
                )
                ln_key = past[-1] if past else None

            html = inject_consensus_records_html(
                html,
                sport="mlb",
                finals=finals,
                last_night_key=ln_key,
                market=market,
                mlb_frozen_only=False,
            )
            if "cons-split-info" in html or "ⓘ" in html:
                html = re.sub(
                    r'<details class="cons-split-info">[\s\S]*?</details>',
                    "",
                    html,
                    flags=re.I,
                )
                html = html.replace("ⓘ", "")
            print(
                f"[mlb_ui_fixup] consensus live+frozen "
                f"market={market} last_night={ln_key} finals={len(finals)} "
                f"snap={len(snap_finals)} live={len(live_finals)}",
                flush=True,
            )
        except Exception as e:
            print(f"[mlb_ui_fixup] consensus inject: {e}", flush=True)
        return html
    except Exception as e:
        print(f"[mlb_ui_fixup] results: {e}", flush=True)
        return html
