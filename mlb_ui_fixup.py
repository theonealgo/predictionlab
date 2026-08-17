#!/usr/bin/env python3
"""MLB UI SIGNED OFF — do not change without owner request.

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

# MLB spread display invert (publish layer only). Default ON.
# Set MLB_FLIP_SPREAD=0 to disable. Does not touch ML/totals or graded DB.
MLB_FLIP_SPREAD = os.environ.get("MLB_FLIP_SPREAD", "1").strip().lower() not in (
    "0",
    "false",
    "off",
    "no",
)


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


def _inject_projected_score_rows(rest: str, pl_proj: str, xs_proj: str) -> str:
    """Fill missing View Details PL/XSharp projected-score rows.

    Ported from iso_hub/sandbox_fixup.py — live mlb_ui_fixup previously only
    patched chart data-* attrs, so cards with a suppressed model stayed blank.
    """
    if not rest or (not pl_proj and not xs_proj):
        return rest
    rest2 = rest
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

        # Last resort: scale PL split to XSharp (or books) total when XS line missing
        if not xs_proj:
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
    Controlled by MLB_FLIP_SPREAD (default ON).
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
    if "odds-pricing-locked" in html and 'data-m-consensus="' not in html:
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
        # Fallback: end of lines-strip
        ls = re.search(r'(<div class="lines-strip">[\s\S]*?)(</div>\s*(?:<footer|<!--|</div>\s*</div>))', stack, re.I)
        if ls:
            return stack.replace(ls.group(1), ls.group(1) + "\n    " + chip + "\n", 1)
        return stack

    parts = re.split(r'(?=<div class="game-card-stack\b)', html)
    if len(parts) <= 1:
        parts = re.split(r'(?=<div class="game-card\b)', html)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])



def apply_mlb_picks_fixups(html: str) -> str:
    """Signed-off MLB picks publish layer (no chrome swap).

    Order matches sandbox: flip model run-line display, enrich chart attrs,
    inject Run Line Confidence chips. Chart market tabs live in the template
    (espn_predictions_template.html) — do not inject sandbox picks-chart.js
    when setPicksChartMarket is already present.
    """
    if not html or "data-pick-card" not in html:
        return html
    try:
        html = dedupe_game_card_stacks(html)
    except Exception as e:
        print(f"[mlb_ui_fixup] dedupe: {e}", flush=True)
    html = flip_mlb_model_spread_display(html)
    html = enrich_mlb_chart_data_attrs(html)
    html = inject_mlb_run_line_confidence(html)
    return html


def apply_mlb_results_fixups(html: str) -> str:
    """MLB results publish layer: season efficiency fill + analytics cards + Cards|Chart."""
    if not html:
        return html
    try:
        from mlb_results_ui import (
            fix_mlb_results_display,
            inject_mlb_results_view_toggle,
            strip_inert_results_market_toggle,
        )
        html = fix_mlb_results_display(html)
        html = strip_inert_results_market_toggle(html)
        html = inject_mlb_results_view_toggle(html, active="normal")
        return html
    except Exception as e:
        print(f"[mlb_ui_fixup] results: {e}", flush=True)
        return html
