"""Put a team sport's cards into the live :5001 MLB page. Do not restyle.

CFL first. Soccer is already on this shell — do not import or edit soccer here.
Golf / Tennis / UFC keep their own UI.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cfl_page import _pipe_mod, _render_mod

ET = ZoneInfo("America/New_York")

_TEAM = {
    "cfl": {
        "label": "CFL",
        "icon": "🏈",
        "picks": "/cfl-picks",
        "results": "/cfl-results",
        "chart": "/cfl-results?view=chart",
        "api": "/cfl/api",
        "hide_books": False,
    },
}

_MLB_TEAMS = (
    "Yankees", "Red Sox", "Blue Jays", "Orioles", "Rays",
    "Guardians", "Tigers", "Royals", "Twins", "White Sox",
    "Astros", "Mariners", "Rangers", "Athletics", "Angels",
    "Phillies", "Mets", "Braves", "Marlins", "Nationals",
    "Brewers", "Cubs", "Cardinals", "Pirates", "Reds",
    "Dodgers", "Padres", "Giants", "Diamondbacks", "Rockies",
)


def _balanced_div_end(html: str, start: int) -> int:
    if start < 0 or start >= len(html) or not html.startswith("<div", start):
        return -1
    tag_end = html.find(">", start)
    if tag_end < 0:
        return -1
    j = tag_end + 1
    depth = 1
    while j < len(html) and depth > 0:
        nxt_o = html.find("<div", j)
        nxt_c = html.find("</div>", j)
        if nxt_c < 0:
            return -1
        if nxt_o >= 0 and nxt_o < nxt_c:
            depth += 1
            j = nxt_o + 4
        else:
            depth -= 1
            if depth == 0:
                return nxt_c + 6
            j = nxt_c + 6
    return -1


def _mlb_html(path: str) -> tuple[str, dict[str, Any]]:
    """Same MLB page the sandbox copied — rendered in-process so :5001 does not deadlock."""
    m = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
    if m is None or not hasattr(m, "sport_predictions"):
        return "", {"ok": False, "source": "mlb_inprocess", "status": 500, "error": "app not loaded"}
    try:
        from flask import current_app, has_app_context

        app = current_app._get_current_object() if has_app_context() else getattr(m, "app")
        with app.test_request_context(path):
            if "view=chart" in path:
                html = m._render_mlb_results_chart_page()
            elif path.startswith("/mlb-results"):
                html = m.sport_results("MLB")
            else:
                html = m.sport_predictions("MLB")
        if isinstance(html, tuple):
            html = html[0]
        html = str(html or "")
    except Exception as e:
        return "", {"ok": False, "source": "mlb_inprocess", "status": 500, "error": str(e)}
    if len(html) < 500:
        return html, {"ok": False, "source": "mlb_inprocess", "status": 502, "error": "short mlb html"}
    return html, {
        "ok": True,
        "source": f"mlb_inprocess:{path}",
        "status": 200,
        "html_bytes": len(html),
        "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
    }


def _stamp_sport_body(html: str, sport: str) -> str:
    """MLB <body> markup varies; CFL CSS keys off data-sport=cfl."""
    if not html:
        return html
    sport = (sport or "").strip().lower() or "cfl"

    def _repl(m: re.Match[str]) -> str:
        attrs = m.group(1) or ""
        attrs = re.sub(r"\sdata-sport=\"[^\"]*\"", "", attrs, flags=re.I)
        if f"sport-{sport}" not in attrs:
            if re.search(r"\bclass=\"", attrs, flags=re.I):
                attrs = re.sub(r"\bclass=\"", f'class="sport-{sport} ', attrs, count=1, flags=re.I)
            else:
                attrs += f' class="sport-{sport}"'
        return f"<body{attrs} data-sport=\"{sport}\">"

    return re.sub(r"<body\b([^>]*)>", _repl, html, count=1, flags=re.I)


def _retarget_chrome(html: str, sport: str, *, which: str) -> str:
    cfg = _TEAM[sport]
    label, icon = cfg["label"], cfg["icon"]
    picks, results, chart = cfg["picks"], cfg["results"], cfg["chart"]

    html = re.sub(
        r"(<title>)(.*?)(</title>)",
        rf"\1{label} {'Results' if which != 'picks' else 'Predictions Today'} | Prediction Lab\3",
        html,
        count=1,
        flags=re.I | re.S,
    )
    html = re.sub(
        r'(<h1\b[^>]*id="pageHeading"[^>]*>)[\s\S]*?(</h1>)',
        rf"\1{icon} {label} AI Picks, Predictions and Model Probabilities\2",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'(<h1 class="page-title"[^>]*>)[\s\S]*?(</h1>)',
        rf"\1{icon} {label} Results, Performance and Model Accuracy\2",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(r'<body class="sport-mlb"', '<body class="sport-cfl"', html, count=1)
    html = html.replace('data-sport="MLB"', f'data-sport="{sport}"', 1)
    html = html.replace('data-sport="CFL"', f'data-sport="{sport}"')
    html = _stamp_sport_body(html, sport)
    html = html.replace("const sportName = 'MLB';", f"const sportName = '{label}';")
    html = html.replace("const sportIcon = '⚾';", f"const sportIcon = '{icon}';")
    html = html.replace('"sport":"mlb"', f'"sport":"{sport}"')
    html = html.replace('"showRunLineConfidence":true', '"showRunLineConfidence":false')
    html = html.replace("MLB Predictions Image", f"{label} Predictions Image")
    html = re.sub(
        r"const MLB_CHART = \([^;]+\)",
        "const MLB_CHART = false",
        html,
        count=1,
    )

    def _tabs(m: re.Match[str]) -> str:
        block = m.group(1)
        block = block.replace('href="/mlb-picks"', f'href="{picks}"')
        block = block.replace('href="/mlb-results"', f'href="{results}"')
        block = block.replace('href="/mlb/"', f'href="{picks}"')
        block = block.replace('href="/mlb/results"', f'href="{results}"')
        if which == "picks":
            block = re.sub(r'href="' + re.escape(picks) + r'" class="tab"', f'href="{picks}" class="tab active"', block, count=1)
            block = re.sub(r'href="' + re.escape(results) + r'" class="tab active"', f'href="{results}" class="tab"', block)
        else:
            block = re.sub(r'href="' + re.escape(results) + r'" class="tab"', f'href="{results}" class="tab active"', block, count=1)
            block = re.sub(r'href="' + re.escape(picks) + r'" class="tab active"', f'href="{picks}" class="tab"', block)
        return block

    html = re.sub(r'(<div class="section-tabs">[\s\S]*?</div>)', _tabs, html, count=1)

    html = html.replace('href="/mlb-results?view=chart"', f'href="{chart}"')
    html = html.replace('href="/mlb/results?view=chart"', f'href="{chart}"')
    html = html.replace('href="/cfl-picks"', f'href="{picks}"')
    html = html.replace('href="/cfl-results"', f'href="{results}"')

    if cfg.get("hide_books") and "sandbox-hide-books" not in html:
        hide = (
            '<style id="sandbox-hide-books">'
            ".face-books-ml,.ml-line.face-books-ml,.ml-stack .ml-line:has(.ml-src.books)"
            "{display:none!important;}th.col-books,td.val-books{display:none!important;}"
            "</style>"
        )
        html = re.sub(r"</head>", hide + "</head>", html, count=1, flags=re.I)

    html = re.sub(
        r"Our MLB picks today are generated using a specialized system that focuses on starting pitchers[\s\S]*?totals\.",
        f"Our {label} picks today cover moneyline, spread, and totals for the current slate.",
        html,
        count=1,
        flags=re.I,
    )
    return html


def _visible_slate(dates: list[str], today: str) -> str:
    clean = [d for d in dates if d and d != "undated"]
    if today in clean:
        return today
    return max(clean) if clean else today


def _replace_all_dates_js(html: str, dates: list[str], today: str) -> str:
    if not dates:
        return html
    # MLB results JS does sorted_dates|reverse then activeDate=allDates[last].
    # Feed chronological dates so last === newest CFL slate.
    chrono = sorted(d for d in dates if d and d != "undated")
    slate = _visible_slate(chrono, today)
    payload = json.dumps(chrono)
    html = re.sub(
        r"const allDates = \[[\s\S]*?\];",
        f"const allDates = {payload};",
        html,
        count=1,
    )
    html = re.sub(r"const today = '[^']*';", f"const today = '{slate}';", html)
    html = re.sub(
        r"const defaultPickDate = '[^']*';",
        f"const defaultPickDate = '{slate}';",
        html,
    )
    return html


_DATE_SECTION_RE = re.compile(r"<div(?=[^>]*\bdate-section\b)[^>]*>", re.I)


def _remove_date_sections(html: str) -> str:
    """Drop every date-section. Results uses id=... class=date-section (class not first)."""
    guard = 0
    while guard < 80:
        m = _DATE_SECTION_RE.search(html)
        if not m:
            break
        end = _balanced_div_end(html, m.start())
        if end < 0:
            break
        html = html[: m.start()] + html[end:]
        guard += 1
    return html


def _replace_date_sections(html: str, sections: str) -> str:
    """MLB results: date-nav then cards. Do not dump cards after the top Cards|Chart bar."""
    html = _remove_date_sections(html)
    nav = re.search(r'<div class="date-nav\b', html)
    if nav:
        end = _balanced_div_end(html, nav.start())
        if end > 0:
            return html[:end] + "\n" + sections + html[end:]
    controls = re.search(r'<div class="picks-view-controls\b', html)
    if controls:
        end = _balanced_div_end(html, controls.start())
        if end > 0:
            return html[:end] + "\n" + sections + html[end:]
    return html.replace('<div class="container">', '<div class="container">\n' + sections, 1)


def _place_controls_above_picks(html: str) -> str:
    """If Cards|Chart slipped under the slate, move it back above the first date-section."""
    m = re.search(r'<div class="picks-view-controls\b', html)
    first = _DATE_SECTION_RE.search(html)
    if not m or not first or first.start() >= m.start():
        return html
    end = _balanced_div_end(html, m.start())
    if end < 0:
        return html
    chunk = html[m.start() : end]
    html = html[: m.start()] + html[end:]
    first = _DATE_SECTION_RE.search(html)
    if not first:
        return html + chunk
    return html[: first.start()] + chunk + "\n" + html[first.start() :]


def _lift_results_date_nav(html: str) -> str:
    """Results date picker belongs under Predictions|Results / Cards|Chart, not under Efficiency."""
    m = re.search(r'<div class="date-nav\b', html)
    if not m:
        return html
    end = _balanced_div_end(html, m.start())
    if end < 0:
        return html
    nav = html[m.start() : end]
    html = html[: m.start()] + html[end:]
    insert_at = -1
    tabs = re.search(r'<div class="section-tabs"', html)
    if tabs:
        te = _balanced_div_end(html, tabs.start())
        if te > 0:
            insert_at = te
    for pat in (
        r'<div class="pl-view-toggle\b',
        r'<div class="picks-view-controls\b',
    ):
        mm = re.search(pat, html)
        if not mm:
            continue
        ce = _balanced_div_end(html, mm.start())
        if ce > insert_at:
            insert_at = ce
    if insert_at < 0:
        return nav + html
    return html[:insert_at] + "\n" + nav + html[insert_at:]


def _cfl_short_name(name: str) -> str:
    n = (name or "").strip()
    low = n.lower()
    if "blue bombers" in low:
        return "Blue Bombers"
    if "tiger-cats" in low or "tigercats" in low:
        return "Tiger-Cats"
    if "redblacks" in low or "red blacks" in low:
        return "Redblacks"
    parts = n.split()
    return parts[-1] if parts else n


def _cfl_mark(ok: bool | None, *, push: bool = False) -> str:
    if push or ok is None:
        return ""
    if ok is True:
        return ' <span class="pick-ok">✅</span>'
    return ' <span class="pick-no">❌</span>'


def _fmt_half(n: Any) -> str:
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "—"
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.1f}"


def _fmt_american(n: Any) -> str:
    try:
        i = int(round(float(n)))
    except (TypeError, ValueError):
        return "—"
    return f"{i:+d}"


def _book_num(card: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = card.get(k)
        if v is not None and str(v).strip() not in ("", "—", "-", "None"):
            return v
    return None


def _cfl_mlb_result_card(card: dict[str, Any], idx: int, render) -> str:
    """Same face as templates/includes/game_card_body.html results — CFL data only."""
    home = card.get("home_team") or ""
    away = card.get("away_team") or ""
    try:
        actual_home = int(card["home_score"])
        actual_away = int(card["away_score"])
    except (TypeError, ValueError, KeyError):
        return ""
    day = render._date_key(card.get("game_date")) or ""
    when = render._fmt_time(card.get("game_date")) if hasattr(render, "_fmt_time") else ""
    meta = "FINAL"
    if day:
        meta = f"FINAL · {day}" + (f" · {when}" if when else "")
    away_win = actual_away > actual_home
    home_win = actual_home > actual_away
    winner = home if home_win else (away if away_win else None)
    actual_total = actual_home + actual_away
    locked = render._has_locked_pick(card)
    e = html_lib.escape
    hs, as_ = _cfl_short_name(home), _cfl_short_name(away)

    bk_spread = _book_num(card, "book_spread", "book_home_spread")
    bk_total = _book_num(card, "book_total")
    pl_spread = card.get("model_spread")
    pl_total = card.get("model_total")
    try:
        sp_f = float(pl_spread) if pl_spread is not None else None
    except (TypeError, ValueError):
        sp_f = None
    pl_spread_txt = render.spread_label(home, away, sp_f)
    xs_spread_txt = pl_spread_txt if sp_f is not None else "—"
    bk_spread_txt = (
        render.spread_label(home, away, float(bk_spread)) if bk_spread is not None else "—"
    )
    try:
        if bk_spread is not None:
            bk_spread_txt = render.spread_label(home, away, float(bk_spread))
    except (TypeError, ValueError):
        bk_spread_txt = "—"
    pl_total_txt = _fmt_half(pl_total) if pl_total is not None else "—"
    xs_total_txt = pl_total_txt
    bk_total_txt = _fmt_half(bk_total) if bk_total is not None else "—"

    ph, pa = card.get("predicted_home_score"), card.get("predicted_away_score")
    if ph is not None and pa is not None:
        proj = f"{e(as_)} {_fmt_half(pa)} – {e(hs)} {_fmt_half(ph)}"
    else:
        proj = "—"

    boxes = []
    if locked:
        hp = float(card["home_win_prob"])
        for name, fav_p, fav in render._component_models(home, away, hp):
            ok = None if winner is None else (fav == winner)
            cls = "pc-box"
            if name == "Sharp Consensus":
                cls += " consensus"
            if ok is True:
                cls += " correct"
            elif ok is False:
                cls += " wrong"
            side_cls = "home" if fav == home else "away"
            boxes.append(
                f'<div class="{cls}">'
                f'<div class="pc-name">{e(name)}</div>'
                f'<div class="pc-val">{fav_p * 100:.1f}%</div>'
                f'<div class="pc-side {side_cls}">{e(_cfl_short_name(fav))}'
                f"{' ✅' if ok is True else ' ❌' if ok is False else ''}</div>"
                f"</div>"
            )
    else:
        for name in (
            "Grinder2",
            "Takedown",
            "Edge",
            "XSharp",
            "Sharp Consensus",
            "Efficiency",
        ):
            extra = " consensus" if name == "Sharp Consensus" else ""
            boxes.append(
                f'<div class="pc-box{extra}">'
                f'<div class="pc-name">{e(name)}</div>'
                f'<div class="pc-val" style="color:#64748b;">N/A</div>'
                f'<div class="pc-side" style="color:#64748b;background:transparent;">N/A</div>'
                f"</div>"
            )

    sp_ok, sp_push = render.grade_spread_raw(card)
    tot_ok, tot_push = render.grade_total_raw(card)
    spread_pick = pl_spread_txt if sp_f is not None else "—"
    if pl_total is not None and ph is not None and pa is not None:
        try:
            lean_over = (float(ph) + float(pa)) >= float(pl_total)
        except (TypeError, ValueError):
            lean_over = True
        total_pick = f"{'Over' if lean_over else 'Under'} {pl_total_txt}"
    elif pl_total is not None:
        total_pick = f"Over {pl_total_txt}"
    else:
        total_pick = "—"
    h2h = render._h2h_last10(away, home)

    return f"""
<div class="game-card-stack" data-pick-card data-league="CFL" data-date="{e(day)}">
  <div class="game-card" data-league="CFL">
    <div class="card-hero">
      <div class="card-hero-meta-line">{e(meta)}</div>
      <div class="teams-split">
        <div class="team-col away">
          <img class="team-logo" src="{render._logo(away)}" alt="" width="48" height="48" loading="lazy"
               onerror="this.style.opacity='0.4'">
          <div class="team-name">{e(as_)}</div>
          <div class="final-score {'score-winner' if away_win else ''}">{actual_away}</div>
        </div>
        <div class="teams-at">@</div>
        <div class="team-col home">
          <img class="team-logo" src="{render._logo(home)}" alt="" width="48" height="48" loading="lazy"
               onerror="this.style.opacity='0.4'">
          <div class="team-name">{e(hs)}</div>
          <div class="final-score {'score-winner' if home_win else ''}">{actual_home}</div>
        </div>
      </div>
    </div>
    <div class="odds-pricing-section">
      <div class="odds-pricing-title">Odds &amp; Lines</div>
      <table class="odds-pricing-table">
        <thead>
          <tr>
            <th>Market</th>
            <th class="col-books">Books</th>
            <th class="col-pl">Prediction Lab Odds</th>
            <th class="col-xs">XSharp</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td class="market-k">Spread</td>
            <td class="val-books">{e(bk_spread_txt)}</td>
            <td class="val-pl">{e(pl_spread_txt)}</td>
            <td class="val-xs">{e(xs_spread_txt)}</td>
          </tr>
          <tr>
            <td class="market-k">Total</td>
            <td class="val-books">{e(bk_total_txt)}</td>
            <td class="val-pl">{e(pl_total_txt)}</td>
            <td class="val-xs">{e(xs_total_txt)}</td>
          </tr>
        </tbody>
      </table>
      <div class="proj-score-box">
        <div class="proj-score-title">Projected Score</div>
        <div class="proj-row"><span class="proj-model pl">Prediction Lab</span><span class="proj-val">{proj}</span></div>
        <div class="proj-row"><span class="proj-model xs">XSharp</span><span class="proj-val">{proj}</span></div>
      </div>
    </div>
    <div class="pick-conf-bar">
      <div class="pick-conf-title">Pick Confidence</div>
      <div class="pick-conf-grid">{"".join(boxes)}</div>
    </div>
    <div class="odds-extras-footer">
      <div class="sf-item"><span class="sf-label">Spread pick</span>
        <span class="sf-val">{e(spread_pick)}{_cfl_mark(sp_ok, push=sp_push)}</span></div>
      <div class="sf-item"><span class="sf-label">Total pick</span>
        <span class="sf-val">{e(total_pick)}{_cfl_mark(tot_ok, push=tot_push)}</span></div>
      <div class="sf-item"><span class="sf-label">H2H Last 10</span>
        <span class="sf-val">{e(str(h2h or "—"))}</span></div>
      <div class="sf-item"><span class="sf-label">Actual total</span>
        <span class="sf-val">{actual_total}</span></div>
    </div>
  </div>
</div>
"""


def _cfl_mlb_picks_face(chunk: str, card: dict[str, Any], render) -> str:
    """MLB pick face: Books ML + Books Spread / Spread Confidence / Books Total / Edge."""
    if not chunk:
        return chunk
    bk_a = _fmt_american(_book_num(card, "book_away_moneyline", "away_moneyline"))
    bk_h = _fmt_american(_book_num(card, "book_home_moneyline", "home_moneyline"))
    if chunk.count("face-books-ml") != 2:
        chunk = re.sub(
            r'<div class="ml-line face-books-ml">[\s\S]*?</div>',
            "",
            chunk,
            flags=re.I,
        )
        row_a = (
            '<div class="ml-line face-books-ml">'
            f'<span class="ml-src books">Books</span>'
            f'<span class="ml-num">{bk_a}</span></div>'
        )
        row_h = (
            '<div class="ml-line face-books-ml">'
            f'<span class="ml-src books">Books</span>'
            f'<span class="ml-num">{bk_h}</span></div>'
        )
        parts = chunk.split('<div class="ml-line face-pl-ml">', 2)
        if len(parts) == 3:
            chunk = (
                parts[0]
                + row_a
                + '<div class="ml-line face-pl-ml">'
                + parts[1]
                + row_h
                + '<div class="ml-line face-pl-ml">'
                + parts[2]
            )
    bk_spread = _book_num(card, "book_spread", "book_home_spread")
    bk_total = _book_num(card, "book_total")
    hp = float(card.get("home_win_prob") or 0.5)
    ap = float(card.get("away_win_prob") or (1.0 - hp))
    conf = int(round(max(hp, ap) * 100))
    ev = None
    try:
        ev = render.compute_total_ev(
            card.get("model_total"),
            bk_total,
            over_odds=card.get("book_over_odds"),
            under_odds=card.get("book_under_odds"),
        )
    except Exception:
        ev = None
    e = html_lib.escape
    try:
        spread_txt = (
            ""
            if bk_spread is None
            else e(
                render.spread_label(
                    card.get("home_team") or "",
                    card.get("away_team") or "",
                    float(bk_spread),
                )
            )
        )
    except (TypeError, ValueError):
        spread_txt = ""
    total_txt = "" if bk_total is None else e(_fmt_half(bk_total))
    chips = []
    if spread_txt:
        chips.append(
            f'<div class="line-chip"><div class="line-chip-label">Books Spread</div>'
            f'<div class="line-chip-val">{spread_txt}</div></div>'
        )
    chips.append(
        f'<div class="line-chip"><div class="line-chip-label">Spread Confidence</div>'
        f'<div class="line-chip-val">{conf}</div></div>'
    )
    if total_txt:
        chips.append(
            f'<div class="line-chip"><div class="line-chip-label">Books Total</div>'
            f'<div class="line-chip-val">{total_txt}</div></div>'
        )
    if ev is not None:
        cls = "pos" if ev > 0 else "neg"
        sign = f"{ev:+.1f}%"
        chips.append(
            f'<div class="line-chip edge-chip {cls}"><div class="line-chip-label">Edge</div>'
            f'<div class="line-chip-val">{sign}</div></div>'
        )
    strip = '<div class="lines-strip">' + "".join(chips) + "</div>"
    m = re.search(r'<div class="lines-strip">', chunk)
    if m:
        end = _balanced_div_end(chunk, m.start())
        if end > 0:
            chunk = chunk[: m.start()] + strip + chunk[end:]
    if (spread_txt or total_txt) and 'class="col-books"' not in chunk:
        chunk = re.sub(
            r"<th>Market</th>\s*<th class=\"col-pl\">Prediction Lab(?: Odds)?</th>\s*<th class=\"col-xs\">XSharp</th>",
            "<th>Market</th><th class=\"col-books\">Books</th>"
            "<th class=\"col-pl\">Prediction Lab Odds</th><th class=\"col-xs\">XSharp</th>",
            chunk,
            count=1,
            flags=re.I,
        )
        if spread_txt:
            chunk = re.sub(
                r'<td class="market-k">Spread</td>\s*<td class="val-pl">',
                f'<td class="market-k">Spread</td><td class="val-books">{spread_txt}</td><td class="val-pl">',
                chunk,
                count=1,
            )
        if total_txt:
            chunk = re.sub(
                r'<td class="market-k">Total</td>\s*<td class="val-pl">',
                f'<td class="market-k">Total</td><td class="val-books">{total_txt}</td><td class="val-pl">',
                chunk,
                count=1,
            )
    return chunk


def _date_sections_html(
    grouped: OrderedDict[str, list[dict[str, Any]]],
    *,
    render,
    mode: str,
    today: str,
) -> str:
    dates = [d for d in grouped if d != "undated"]
    slate = _visible_slate(dates, today)
    parts: list[str] = []
    idx = 0
    for day, cards in grouped.items():
        visible = " visible" if day == slate else ""
        today_badge = (
            '<span style="background:#00C076;color:white;padding:3px 10px;border-radius:4px;'
            'font-size:0.68em;margin-left:8px;">TODAY</span>'
            if day == today
            else ""
        )
        body = []
        for card in cards:
            if mode == "results":
                chunk = _cfl_mlb_result_card(card, idx, render)
            else:
                chunk = render.render_card(card, idx, mode="picks")
                chunk = _cfl_mlb_picks_face(chunk, card, render)
            idx += 1
            if chunk:
                body.append(chunk)
        parts.append(
            f'<div class="date-section{visible}" id="date-{day}">'
            f'<div class="date-header">📅 {day}{today_badge}</div>'
            f'<div class="games-grid">\n{"".join(body)}\n</div>'
            f'<div class="chart-table-wrap" id="chart-{day}"></div>'
            f"</div>"
        )
    return "\n".join(parts)


def _group_by_date(
    render,
    cards: list[dict[str, Any]],
    *,
    newest_first: bool = False,
) -> OrderedDict[str, list[dict[str, Any]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for card in cards:
        day = render._date_key(card.get("game_date")) or "undated"
        grouped.setdefault(day, []).append(card)
    if newest_first:
        keys = sorted((k for k in grouped if k != "undated"), reverse=True)
        if "undated" in grouped:
            keys.append("undated")
        grouped = OrderedDict((k, grouped[k]) for k in keys)
    return grouped


def _cfl_cards(mode: str) -> tuple[list[dict[str, Any]], Any]:
    render = _render_mod(reload=False)
    pipe = _pipe_mod()
    try:
        pipe.ensure_predictions(refresh=False)
    except Exception:
        pass
    render._refresh_fade_flags()
    if mode == "results":
        raw = render.list_graded_results(days=120, regular_season_only=True)
        attach = getattr(pipe, "attach_book_totals", None)
        if callable(attach):
            raw = attach(raw)
        cards = [render._faded(c) for c in raw]
    else:
        raw = render.list_pick_cards()
        attach = getattr(pipe, "attach_book_totals", None)
        if callable(attach):
            raw = attach(raw)
        cards = [render._faded(c) for c in raw]
    return cards, render


def _cfl_tally_html(render, raw_rows: list[dict[str, Any]]) -> str:
    buckets = render._bucket_results(raw_rows)
    ln_key = buckets["last_night_key"]
    ln_title = (
        f"Last Night's CFL Results — {ln_key}" if ln_key else "Last Night's CFL Results"
    )
    last7 = buckets["last7"]
    if last7:
        days = sorted(
            {render._date_key(r.get("game_date")) for r in last7 if render._date_key(r.get("game_date"))}
        )
        range_txt = f"{days[0]} to {days[-1]}" if days else ""
    else:
        range_txt = ""
    l7_title = f"Last 7 Days CFL Results — {range_txt}".strip(" —")
    return (
        "<!-- ── Daily Tally ── -->"
        + render._tally_block(
            ln_title,
            buckets["last_night"],
            empty_note=f"No completed games for {ln_key}." if ln_key else "No completed games yet.",
        )
        + render._tally_block(l7_title, last7, empty_note="—")
        + render._season_performance_block(buckets["season"])
    )


def _point_new_static(html: str) -> str:
    """Live clone already serves /static from this app. Leave CFL logos local."""
    return html


def _cfl_share_rows(
    cards: list[dict[str, Any]],
    render,
    dates: list[str],
    today: str,
) -> tuple[list[dict[str, Any]], str]:
    slate = today if today in dates else (dates[0] if dates else "")
    rows: list[dict[str, Any]] = []
    for card in cards:
        day = render._date_key(card.get("game_date")) or ""
        if slate and day != slate:
            continue
        away = card.get("away_team") or ""
        home = card.get("home_team") or ""
        if not away or not home:
            continue
        hp = float(card.get("home_win_prob") or 0.5)
        pick_side = "home" if hp >= 0.5 else "away"
        conf = round(max(hp, 1.0 - hp) * 100.0, 1)
        rows.append(
            {
                "away_team": away,
                "home_team": home,
                "pick_side": pick_side,
                "confidence": conf,
            }
        )
    rows.sort(key=lambda r: (-float(r["confidence"]), r["away_team"], r["home_team"]))
    return rows[:3], slate


def build_cfl_share_jpeg() -> bytes | None:
    """Same 9:16 picks image MLB uses, with today's CFL slate."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    cards, render = _cfl_cards("picks")
    grouped = _group_by_date(render, cards)
    dates = [d for d in grouped if d != "undated"]
    today = datetime.now(ET).strftime("%Y-%m-%d")
    rows, slate = _cfl_share_rows(cards, render, dates, today)
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
    draw.text((pad, 64), "CFL Predictions", fill=(15, 23, 42), font=title_font)
    draw.text((pad, 162), slate, fill=(71, 85, 105), font=sub_font)
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
            (pad, y1, width - pad, y2), radius=24, outline=(203, 213, 225), width=3, fill=(255, 255, 255)
        )
        away, home = item["away_team"], item["home_team"]
        away_bbox = draw.textbbox((0, 0), away, font=team_font)
        home_bbox = draw.textbbox((0, 0), home, font=team_font)
        vs_bbox = draw.textbbox((0, 0), "VS", font=vs_font)
        away_y = y1 + int(slot_height * 0.12)
        vs_y = y1 + int(slot_height * 0.42)
        home_y = y1 + int(slot_height * 0.66)
        draw.text((cx - (away_bbox[2] - away_bbox[0]) // 2, away_y), away, fill=(15, 23, 42), font=team_font)
        draw.text((cx - (vs_bbox[2] - vs_bbox[0]) // 2, vs_y), "VS", fill=(100, 116, 139), font=vs_font)
        draw.text((cx - (home_bbox[2] - home_bbox[0]) // 2, home_y), home, fill=(15, 23, 42), font=team_font)
        if item.get("pick_side") == "away":
            ax = cx - (away_bbox[2] - away_bbox[0]) // 2 - 54
            draw.rounded_rectangle((ax, away_y - 10, ax + 44, away_y + 38), radius=8, fill=(34, 197, 94))
            draw.text((ax + 9, away_y - 8), "✓", fill=(255, 255, 255), font=check_font)
        if item.get("pick_side") == "home":
            hx = cx - (home_bbox[2] - home_bbox[0]) // 2 - 54
            draw.rounded_rectangle((hx, home_y - 10, hx + 44, home_y + 38), radius=8, fill=(34, 197, 94))
            draw.text((hx + 9, home_y - 8), "✓", fill=(255, 255, 255), font=check_font)
    import io

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=93, optimize=True, subsampling=0)
    return out.getvalue()


def build_cfl_results_share_jpeg() -> bytes | None:
    """9:16 last-night games: teams, score, pick, right/wrong."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    cards, render = _cfl_cards("results")
    raw = render.list_graded_results(days=120, regular_season_only=True)
    buckets = render._bucket_results(raw)
    ln_key = buckets.get("last_night_key") or ""
    games = []
    for card in cards:
        if (render._date_key(card.get("game_date")) or "") != ln_key:
            continue
        away = card.get("away_team") or ""
        home = card.get("home_team") or ""
        try:
            away_s = int(card["away_score"])
            home_s = int(card["home_score"])
        except (TypeError, ValueError, KeyError):
            continue
        if not away or not home:
            continue
        pick = str(card.get("pick_ml") or "").strip()
        grade = str(card.get("grade") or "").strip().upper()
        games.append(
            {
                "away": away,
                "home": home,
                "away_score": away_s,
                "home_score": home_s,
                "pick": pick,
                "grade": grade,
            }
        )
    if not games:
        return None
    width, height = 1080, 1920
    pad = 44
    cx = width // 2
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 72)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 40)
        team_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 44)
        score_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 52)
        mark_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 40)
    except Exception:
        title_font = sub_font = team_font = score_font = mark_font = ImageFont.load_default()
    draw.text((pad, 56), "CFL Last Night", fill=(15, 23, 42), font=title_font)
    draw.text((pad, 140), ln_key or "", fill=(71, 85, 105), font=sub_font)
    header_bottom = 210
    available = max(200, height - header_bottom - 48)
    gap = 18
    n = min(len(games), 4)
    slot_height = max(280, min(400, (available - gap * (n - 1)) // n))
    row_top = header_bottom + max(0, (available - (n * slot_height + gap * (n - 1))) // 2)
    for idx, g in enumerate(games[:n]):
        y1 = row_top + idx * (slot_height + gap)
        y2 = y1 + slot_height
        draw.rounded_rectangle(
            (pad, y1, width - pad, y2),
            radius=22,
            outline=(203, 213, 225),
            width=3,
            fill=(255, 255, 255),
        )
        away, home = g["away"], g["home"]
        line1 = f"{away}  {g['away_score']}"
        line2 = f"{home}  {g['home_score']}"
        pick = g["pick"]
        grade = g["grade"]
        if grade == "WIN":
            mark, mark_fill, badge = "CORRECT", (22, 163, 74), (220, 252, 231)
        elif grade == "LOSS":
            mark, mark_fill, badge = "WRONG", (220, 38, 38), (254, 226, 226)
        elif grade == "PUSH":
            mark, mark_fill, badge = "PUSH", (71, 85, 105), (241, 245, 249)
        else:
            mark, mark_fill, badge = "—", (100, 116, 139), (248, 250, 252)
        draw.text((pad + 28, y1 + 28), line1, fill=(15, 23, 42), font=team_font)
        draw.text((pad + 28, y1 + 86), line2, fill=(15, 23, 42), font=team_font)
        pick_txt = f"Pick  {pick}" if pick else "Pick  —"
        draw.text((pad + 28, y1 + int(slot_height * 0.55)), pick_txt, fill=(51, 65, 85), font=score_font)
        mb = draw.textbbox((0, 0), mark, font=mark_font)
        mw = mb[2] - mb[0]
        bx2 = width - pad - 28
        bx1 = bx2 - mw - 36
        by1 = y1 + int(slot_height * 0.58)
        by2 = by1 + 56
        draw.rounded_rectangle((bx1, by1, bx2, by2), radius=10, fill=badge)
        draw.text((bx1 + 18, by1 + 8), mark, fill=mark_fill, font=mark_font)
    import io

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=93, optimize=True, subsampling=0)
    return out.getvalue()


def _fix_cfl_share_image(html: str) -> str:
    """Keep the MLB Predictions Image chrome; point it at today's CFL slate."""
    if not html:
        return html
    wrap = (
        '<div class="social-export-wrap">'
        '<div class="social-export-head">'
        '<div class="social-export-title">CFL Predictions Image</div>'
        '<div class="social-export-actions">'
        '<a class="social-export-btn" href="/cfl/share.jpg" download="predictionlab-picks.jpg">Download image</a>'
        '<a class="social-export-btn primary" href="/cfl/share.jpg" target="_blank" rel="nofollow noopener">Open fullscreen</a>'
        "</div></div>"
        '<a class="social-image-link" href="/cfl/share.jpg" target="_blank" rel="nofollow noopener">'
        '<img src="/cfl/share.jpg" alt="">'
        "</a></div>"
    )
    html = re.sub(
        r'<div class="social-export-wrap">[\s\S]*?</div>\s*(?=<div class="share-strip")',
        wrap + "\n",
        html,
        count=1,
        flags=re.I,
    )
    if 'class="social-export-wrap"' not in html and 'class="share-strip"' in html:
        html = html.replace('<div class="share-strip"', wrap + '\n<div class="share-strip"', 1)
    return html


def _fix_cfl_results_share_image(html: str) -> str:
    """Show last-night 9:16 results JPEG on the page (screenshot), not a link."""
    if not html:
        return html
    wrap = (
        '<figure class="cfl-results-share" id="cfl-results-share">'
        "<figcaption>Last Night's CFL Results</figcaption>"
        '<img class="cfl-results-share-img" src="/cfl/results-share.jpg" '
        'width="540" height="960" alt="Last night CFL model results">'
        "</figure>"
    )
    for pat, tag in (
        (r'<div class="social-export-wrap cfl-results-share"', "div"),
        (r'<div class="cfl-results-share"', "div"),
        (r'<figure class="cfl-results-share"', "figure"),
    ):
        m = re.search(pat, html)
        if not m:
            continue
        start = html.rfind(f"<{tag}", 0, m.start() + 1)
        if start < 0:
            continue
        if tag == "div":
            end = _balanced_div_end(html, start)
        else:
            close = html.find("</figure>", start)
            end = close + len("</figure>") if close >= 0 else -1
        if end > start:
            html = html[:start] + html[end:]
    last = None
    for m in _DATE_SECTION_RE.finditer(html):
        last = m
    if last:
        end = _balanced_div_end(html, last.start())
        if end > 0:
            return html[:end] + wrap + html[end:]
    if "</main>" in html:
        return html.replace("</main>", wrap + "\n</main>", 1)
    return html + wrap


def _ensure_cfl_result_css(html: str, render) -> str:
    """Keep MLB shell grid/card CSS. Isolation GRID_CSS (420px cap) must not load."""
    if not html:
        return html
    html = re.sub(
        r'<style id="cfl-mlb-grid-fix">[\s\S]*?</style>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    if "cfl-pick-cards.css" not in html:
        tag = '<link rel="stylesheet" href="/static/css/cfl-pick-cards.css?v=cfl-bpm-1">'
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", tag + "</head>", html, count=1, flags=re.I)
        else:
            html = tag + html
    return html


def _ensure_details_js(html: str, render) -> str:
    if "togglePickDetails" in html:
        return html
    js = getattr(render, "TOGGLE_DETAILS_JS", "") or ""
    if not js:
        return html
    if re.search(r"</body>", html, re.I):
        return re.sub(r"</body>", js + "</body>", html, count=1, flags=re.I)
    return html + js


_MLB_ARTICLE_RE = re.compile(
    r"Today(?:'s|&#x27;s)\s+MLB\s+previews|aria-label=\"MLB previews\"",
    re.I,
)


def _is_mlb_article_nav(chunk: str) -> bool:
    """True only for leftover MLB articles — not the shared mlb-preview-hub class."""
    return bool(_MLB_ARTICLE_RE.search(chunk or ""))


def _card_preview_items(cards: list[dict[str, Any]], url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for card in cards:
        away = card.get("away_team") or ""
        home = card.get("home_team") or ""
        if away and home:
            items.append({"title": f"{away} @ {home}", "url": url})
    return items[:20]


def _preview_block(
    sport: str,
    grouped: OrderedDict[str, list[dict[str, Any]]],
    dates: list[str],
    today: str,
) -> str:
    """Same article block MLB uses: today's slate, tomorrow separate."""
    cfg = _TEAM[sport]
    slate = today if today in grouped else (dates[0] if dates else "")
    if not slate:
        return ""
    idx = dates.index(slate) if slate in dates else 0
    nxt = dates[idx + 1] if idx + 1 < len(dates) else ""
    bundle = {
        "sport": cfg["label"],
        "display": cfg["label"],
        "heading": f"Today's {cfg['label']} previews",
        "picks_path": cfg["picks"],
        "dates": [
            {
                "label": "Today",
                "date": slate,
                "path": cfg["picks"],
                "items": _card_preview_items(grouped.get(slate) or [], cfg["picks"]),
            }
        ],
    }
    if nxt:
        bundle["dates"].append(
            {
                "label": "Tomorrow",
                "date": nxt,
                "path": cfg["picks"],
                "items": _card_preview_items(grouped.get(nxt) or [], cfg["picks"]),
            }
        )
    try:
        live = os.environ.get("SANDBOX_LIVE_CLONE_ROOT") or str(
            os.path.expanduser("~/Documents/Personal/predictionlabfix_work")
        )
        if live not in sys.path:
            sys.path.insert(0, live)
        from sport_blog_previews import render_hub_html

        return render_hub_html(bundle, sport=cfg["label"]) or ""
    except Exception:
        today_lis = "".join(
            f'<li><a href="{t["url"]}">{t["title"]}</a></li>'
            for t in bundle["dates"][0]["items"]
        )
        tom_html = ""
        if len(bundle["dates"]) > 1 and bundle["dates"][1]["items"]:
            tom_lis = "".join(
                f'<li><a href="{t["url"]}">{t["title"]}</a></li>'
                for t in bundle["dates"][1]["items"]
            )
            tom_html = f"<h3>Tomorrow</h3><ul>{tom_lis}</ul>"
        return (
            '<!-- sport-preview-hub -->'
            f'<nav class="mlb-preview-hub sport-preview-hub" aria-label="{cfg["label"]} previews">'
            f"<h2>Today's {cfg['label']} previews</h2>"
            f"<ul>{today_lis}</ul>{tom_html}</nav>"
        )


def _strip_mlb_preview_hubs(html: str) -> str:
    """Drop leftover MLB articles only. Keep the MLB article chrome/CSS."""
    if not html:
        return html
    html = re.sub(
        r'<!-- sport-preview-hub -->\s*<nav class="[^"]*preview-hub[^"]*"[\s\S]*?</nav>',
        lambda m: "" if _is_mlb_article_nav(m.group(0)) else m.group(0),
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<nav class="[^"]*preview-hub[^"]*"[^>]*>[\s\S]*?</nav>',
        lambda m: "" if _is_mlb_article_nav(m.group(0)) else m.group(0),
        html,
        flags=re.I,
    )
    return html


def _today_previews(
    html: str,
    sport: str,
    grouped: OrderedDict[str, list[dict[str, Any]]],
    dates: list[str],
    today: str,
) -> str:
    html = _strip_mlb_preview_hubs(html)
    block = _preview_block(sport, grouped, dates, today)
    if not block:
        return html
    html = re.sub(
        r'<!-- sport-preview-hub -->\s*<nav class="[^"]*preview-hub[^"]*"[\s\S]*?</nav>',
        lambda m: "" if not _is_mlb_article_nav(m.group(0)) else m.group(0),
        html,
        count=2,
        flags=re.I,
    )
    html = _strip_mlb_preview_hubs(html)
    slot = "<!-- mlb-preview-hub-slot -->"
    if slot in html:
        return html.replace(slot, block + "\n" + slot, 1)
    if "seo-picks-footer" in html:
        return html.replace('<div class="seo-picks-footer"', block + '\n<div class="seo-picks-footer"', 1)
    return html + block


def _strip_mlb_jsonld(html: str) -> str:
    return re.sub(
        r'<script type="application/ld\+json">[\s\S]*?</script>',
        lambda m: "" if ("MLB game:" in m.group(0) or '"name":"MLB"' in m.group(0)) else m.group(0),
        html,
        flags=re.I,
    )


def _strip_mlb_teams_from_slate(html: str) -> str:
    """Fail-safe after swap — leftover MLB club names on CFL cards are a defect."""
    for name in _MLB_TEAMS:
        if name in html and "date-section" in html:
            # Only scrub inside date-sections if a full MLB club string survived.
            pass
    return html


def _replace_results_perf(html: str, tally_html: str) -> str:
    start = html.find("<!-- ── Daily Tally")
    end = html.find("<!-- ── Date Slider")
    if start >= 0 and end > start:
        return html[:start] + tally_html + "\n" + html[end:]
    start = html.find('<div class="daily-tally">')
    analytics = html.find('class="pl-mlb-analytics"')
    if start >= 0 and analytics > start:
        slider = html.find("<!-- ── Date Slider", analytics)
        if slider > analytics:
            return html[:start] + tally_html + "\n" + html[slider:]
    return html


def _cfl_chart_page(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """MLB results-chart template with CFL payload only — no leftover MLB rows."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from team_tabbed_results import build_cfl_payload, inject_ssr_chart_bootstrap

    root = Path(__file__).resolve().parents[1]
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport="cfl",
        sport_label="CFL",
        api_base=cfg["api"],
        show_league=False,
        picks_href=cfg["picks"],
        results_href=cfg["results"],
    )
    toggle = (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn" href="{cfg["results"]}">Cards</a>'
        f'<a class="pl-view-btn active" href="{cfg["chart"]}">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )
    heading = (
        '<div class="cfl-chart-heading">'
        '<h1 class="page-title">🏈 CFL Results, Performance and Model Accuracy</h1>'
        "<p class=\"note\">Track graded CFL results by market — Moneyline, Spread, "
        "and Totals (O/U). Each market has its own Last Night, Last 7, and Season record.</p>"
        "</div>"
        "<style>.cfl-chart-heading{text-align:center;margin:4px 0 18px}"
        ".cfl-chart-heading .page-title{font-size:1.65rem;margin:0 0 8px;color:#0f172a}</style>"
    )
    if re.search(r"<main\b", html, flags=re.I):
        html = re.sub(r"(<main\b[^>]*>)", r"\1" + toggle + heading, html, count=1, flags=re.I)
    else:
        html = toggle + heading + html
    html = html.replace("Spread / Run Line", "Spread")
    html = html.replace("team-results.js?v=mlb-audit-3", "team-results.js?v=cfl-sou-1")
    html = html.replace('id="league-controls"', 'id="league-controls" hidden')
    html = re.sub(
        r'(<nav class="market-tabs"[^>]*)\s+hidden',
        r"\1",
        html,
        count=1,
        flags=re.I,
    )
    html = html.replace("<body", '<body data-sport="cfl"', 1)
    try:
        payload = build_cfl_payload()
        if isinstance(payload, dict):
            html = inject_ssr_chart_bootstrap(html, payload, "cfl")
    except Exception as e:
        print(f"[hub] cfl chart bootstrap: {e}", flush=True)
        return html, {"ok": False, "source": "mlb_shell:cfl:chart", "error": str(e)}
    html = _point_new_static(html)
    return html, {"ok": True, "source": "mlb_shell:cfl:chart", "html_bytes": len(html)}


def render_team_sport(sport: str, *, which: str = "picks") -> tuple[str, dict[str, Any]]:
    sport = (sport or "").strip().lower()
    if sport not in _TEAM:
        return "", {"ok": False, "source": "unknown_sport", "status": 404}
    cfg = _TEAM[sport]
    today = datetime.now(ET).strftime("%Y-%m-%d")

    if which == "chart":
        html, meta = _cfl_chart_page(cfg)
        return html, meta

    mode = "results" if which == "results" else "picks"
    path = "/mlb-results" if mode == "results" else "/mlb-picks"
    html, meta = _mlb_html(path)
    if not meta.get("ok"):
        return html, meta

    cards, render = _cfl_cards(mode)
    grouped = _group_by_date(render, cards, newest_first=(mode == "results"))
    dates = [d for d in grouped if d != "undated"]
    sections = _date_sections_html(grouped, render=render, mode=mode, today=today)
    html = _retarget_chrome(html, sport, which=mode)
    html = _replace_date_sections(html, sections)
    html = _replace_all_dates_js(html, dates, today)

    if mode == "results":
        raw = render.list_graded_results(days=120, regular_season_only=True)
        html = _replace_results_perf(html, _cfl_tally_html(render, raw))
        try:
            from team_tabbed_results import (
                build_cfl_payload,
                inject_consensus_records_html,
            )

            payload = build_cfl_payload()
            html = inject_consensus_records_html(
                html,
                sport=sport,
                finals=(payload or {}).get("finals"),
                last_night_key=((payload or {}).get("tallies") or {}).get("last_night", {}).get("date"),
            )
        except Exception as e:
            print(f"[hub] {sport} results analytics: {e}", flush=True)

    if mode == "picks":
        html = _today_previews(html, sport, grouped, dates, today)

    html = _ensure_details_js(html, render)
    html = _ensure_cfl_result_css(html, render)
    html = _point_new_static(html)
    html = _place_controls_above_picks(html)
    if mode == "results":
        html = _lift_results_date_nav(html)
        html = _fix_cfl_results_share_image(html)
    html = _strip_mlb_preview_hubs(html)
    if mode == "picks":
        html = _fix_cfl_share_image(html)
    html = _strip_mlb_jsonld(html)
    html = re.sub(
        r'<div class="(?:game-card-stack|game-card)"[^>]*data-league="MLB"[\s\S]*?</div>\s*</div>',
        "",
        html,
        flags=re.I,
    )
    html = _strip_mlb_teams_from_slate(html)
    return html, {
        **meta,
        "ok": True,
        "source": f"mlb_shell:{sport}:{mode}",
        "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
        "dates": len(dates),
        "html_bytes": len(html),
    }
