"""Signed-off MLB chart shape + soccer card/books checks.

Consensus Based Betting Records must be dissent combinations
(N/N unanimous, all but …) with color bars — not per-model 0-0 stubs.

PL vs Sportsbook must be the Books favorite / PL favorite table with bars —
not the stub title Books · Prediction Lab · XSharp.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime

ML_CHART = "Consensus Based Betting Records"
PL_VS_BOOKS = "PL vs Sportsbook"
OU_CHART_DOT = "Prediction Lab · XSharp — Totals"
OU_CHART_AMP = "Prediction Lab & XSharp — Totals"
PL_VS_ROWS = (
    "Books favorite",
    "PL favorite",
    "PL vs Books disagree",
    "PL and Books agree",
)


def has_dissent_consensus(html: str) -> bool:
    html = html or ""
    if ML_CHART not in html:
        return False
    if not re.search(r"unanimous|all but", html, flags=re.I):
        return False
    return "cons-bar" in html


def has_pl_vs_sportsbook(html: str) -> bool:
    html = html or ""
    if PL_VS_BOOKS not in html:
        return False
    if any(row not in html for row in PL_VS_ROWS):
        return False
    return "cons-bar" in html


def has_market_tabs(html: str) -> bool:
    html = html or ""
    if not re.search(r">\s*Moneyline\s*<", html, flags=re.I):
        return False
    if not re.search(r">\s*(?:Spread|Run Line|Puck Line)\s*<", html, flags=re.I):
        return False
    return bool(re.search(r">\s*Totals\s*<", html, flags=re.I))


def has_totals_chart(html: str) -> bool:
    html = html or ""
    return OU_CHART_DOT in html or OU_CHART_AMP in html


SPREAD_CHART_DOT = "Prediction Lab · XSharp — Spread"
SPREAD_CHART_AMP = "Prediction Lab & XSharp — Spread"
EMPTY_MARKET_STUB = "No graded games for this market"


def has_spread_chart(html: str) -> bool:
    html = html or ""
    return SPREAD_CHART_DOT in html or SPREAD_CHART_AMP in html


def empty_market_chart_issues(html: str) -> list[str]:
    """FAIL when Moneyline|Spread|Totals tabs are present but a market is a stub."""
    html = html or ""
    if EMPTY_MARKET_STUB not in html:
        return []
    if not has_market_tabs(html):
        return []
    issues: list[str] = []
    if not has_spread_chart(html):
        issues.append("Spread tab has no Prediction Lab / XSharp chart")
    if not has_totals_chart(html):
        issues.append("Totals tab has no Prediction Lab / XSharp chart")
    return issues


def missing_signed_off_charts(
    html: str,
    *,
    require_pl_vs_books: bool = True,
    require_market_tabs: bool = True,
    require_totals: bool = False,
) -> list[str]:
    html = html or ""
    missing: list[str] = []
    if ML_CHART not in html:
        missing.append(ML_CHART)
    elif not re.search(r"unanimous|all but|Strong consensus", html, flags=re.I):
        missing.append("Consensus dissent rows (N/N unanimous / all but)")
    if "among the 4 live models" in html:
        for label in (
            "Unanimous",
            "Strong consensus",
            "Split / no consensus",
            "Strong disagreement",
        ):
            if label not in html:
                missing.append(f"4-model consensus meaning: {label}")
    elif "cons-bar" not in html:
        missing.append("Consensus color bars")
    if require_pl_vs_books:
        if PL_VS_BOOKS not in html:
            missing.append("PL vs Sportsbook")
        else:
            for row in PL_VS_ROWS:
                if row not in html:
                    missing.append(f"PL vs Sportsbook row: {row}")
            if "cons-bar" not in html:
                missing.append("PL vs Sportsbook color bars")
    if require_market_tabs and not has_market_tabs(html):
        missing.append("Moneyline | Spread | Totals market tabs")
    if require_totals and not has_totals_chart(html):
        missing.append(OU_CHART_DOT)
    return missing


def has_three_cards_per_row(html: str) -> bool:
    """Desktop pick/results cards sit 3 across (games-grid / results-grid)."""
    html = html or ""
    compact = re.sub(r"\s+", "", html)
    if re.search(
        r"(?:games-grid|results-grid)[^{]{0,180}\{[^}]{0,400}grid-template-columns:repeat\(3",
        compact,
        flags=re.I,
    ):
        return True
    return bool(
        re.search(
            r"(?:games-grid|results-grid)[^{;]{0,80}repeat\(\s*3",
            html,
            flags=re.I,
        )
    )


def _plain_cell(raw: str) -> str:
    plain = re.sub(r"<[^>]+>", "", raw or "")
    plain = re.sub(r"&mdash;|&ndash;|&nbsp;", " ", plain, flags=re.I)
    plain = re.sub(r"[—–]", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def _books_cell_present(raw: str) -> bool:
    cell = _plain_cell(raw)
    if cell.upper() == "PK":
        return True
    return bool(re.search(r"\d", cell))


def soccer_spread_books_missing(html: str) -> int:
    """Spread Books empty while the same card has other book numbers.

    PK (pick'em) counts as present. A dash is only a gap when Moneyline or
    Total on that card already has a book line.
    """
    html = html or ""
    tables = re.findall(
        r'(?is)<table[^>]*class="[^"]*odds-pricing-table[^"]*"[\s\S]*?</table>',
        html,
    )
    missing = 0
    for table in tables:
        spread = re.search(
            r'(?is)<td[^>]*class="[^"]*market-k[^"]*"[^>]*>\s*Spread\s*</td>'
            r'\s*<td[^>]*class="[^"]*val-books[^"]*"[^>]*>([\s\S]*?)</td>',
            table,
        )
        if not spread:
            continue
        if _books_cell_present(spread.group(1)):
            continue
        ml = re.search(
            r'(?is)<td[^>]*class="[^"]*market-k[^"]*"[^>]*>\s*Moneyline\s*</td>'
            r'\s*<td[^>]*class="[^"]*val-books[^"]*"[^>]*>([\s\S]*?)</td>',
            table,
        )
        # Totals-only books (OU posted, no AH) are not a missing-spread bug.
        if ml and _books_cell_present(ml.group(1)):
            missing += 1
    return missing


_COINFLIP = frozenset({"50", "50.0", "50.00"})
_DASH = frozenset({"", "—", "–", "-", "&mdash;", "&ndash;", "n/a", "na"})


def _is_fifty(raw: str) -> bool:
    text = (raw or "").strip().replace("%", "")
    try:
        return abs(float(text) - 50.0) < 0.05
    except ValueError:
        return text in _COINFLIP


def _is_dash(raw: str) -> bool:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = re.sub(r"&(?:mdash|ndash|nbsp);", "", text, flags=re.I)
    return re.sub(r"\s+", "", text).strip().lower() in _DASH


def picks_placeholder_issues(html: str) -> list[str]:
    """Coin-flip face %, Pick Confidence stuck at 50%, blank Books ML."""
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
    issues: list[str] = []
    face = [
        m.strip()
        for m in re.findall(r'class="win-pct[^"]*">\s*([^<]+)', html)
        if m.strip()
    ]
    if len(face) >= 6:
        n50 = sum(1 for v in face if _is_fifty(v))
        if n50 / len(face) >= 0.9:
            issues.append(
                f"coin-flip slate: {n50}/{len(face)} face win% are 50.0 "
                "(placeholder / no model)"
            )
    pairs = re.findall(
        r'class="pc-name">([^<]+)</div>\s*<div class="pc-val">([^<]+)',
        html,
    )
    by_model: dict[str, list[str]] = {}
    for name, val in pairs:
        name = re.sub(r"[^A-Za-z0-9 ]+", "", name).strip()
        if name:
            by_model.setdefault(name, []).append(val.strip())
    for model in ("Edge", "XSharp", "Sharp Consensus"):
        vals = by_model.get(model) or []
        if len(vals) < 8:
            continue
        n50 = sum(1 for v in vals if _is_fifty(v))
        if n50 / len(vals) >= 0.8:
            issues.append(f"{model} Pick Confidence stuck at 50% ({n50}/{len(vals)})")
    books = re.findall(
        r'class="ml-src books">[\s\S]{0,160}?class="ml-num[^"]*">\s*([^<]+)',
        html,
        flags=re.I,
    )
    if len(books) >= 6:
        blank = sum(1 for v in books if _is_dash(v) or "—" in v)
        if blank / len(books) >= 0.9:
            issues.append(f"Books ML blank on {blank}/{len(books)} face lines (—)")
    return issues


_CLOCK_STUBS = frozenset(
    {"upcoming", "tbd", "tba", "time tba", "time tbd", "—", "–", "-", ""}
)


def picks_clock_issues(html: str) -> list[str]:
    """FAIL when card kickoff is Upcoming / TBD instead of a clock."""
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
    times = [t.strip() for t in re.findall(r'class="game-time">([^<]+)', html)]
    if len(times) < 2:
        times = [t.strip() for t in re.findall(r'data-time="([^"]*)"', html)]
    if len(times) < 2:
        return []

    def _stub(t: str) -> bool:
        low = re.sub(r"\s+", " ", t).strip().lower()
        if low in _CLOCK_STUBS:
            return True
        if low == "upcoming":
            return True
        return not bool(re.search(r"\d", t))

    stub = sum(1 for t in times if _stub(t))
    if stub == 0:
        return []
    if stub == len(times):
        return [f"Kickoff time stuck on Upcoming / no clock ({stub}/{len(times)})"]
    return [f"Kickoff time missing on {stub}/{len(times)} cards"]


def _logo_src_missing(src: str) -> bool:
    s = (src or "").strip()
    if not s or s in {"#", "/", "about:blank"}:
        return True
    low = s.lower()
    if "pl-logo" in low or "placeholder" in low or "missing" in low:
        return True
    if re.search(r"/500/?(?:\.png)?$", low):
        return True
    return False


def picks_logo_issues(html: str) -> list[str]:
    """FAIL when a face is missing a team logo (empty src or site fallback)."""
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html or "", flags=re.I)
    slots = re.findall(
        r'<div class="team-slot[^"]*">([\s\S]*?)<div class="(?:model-tag|win-pct)',
        html,
        flags=re.I,
    )
    if len(slots) < 4:
        logos = re.findall(r'<img class="team-logo"[^>]*>', html, flags=re.I)
        if len(logos) < 4:
            return []
        missing = 0
        for tag in logos:
            src_m = re.search(r'src="([^"]*)"', tag)
            if _logo_src_missing(src_m.group(1) if src_m else ""):
                missing += 1
        if missing:
            return [f"Team logo missing on {missing}/{len(logos)} faces"]
        return []
    missing_names: list[str] = []
    for sl in slots:
        name_m = re.search(r'class="team-name">([^<]+)', sl)
        src_m = re.search(r'<img class="team-logo"[^>]*src="([^"]*)"', sl, flags=re.I)
        name = (name_m.group(1) if name_m else "").strip()
        src = src_m.group(1) if src_m else ""
        if _logo_src_missing(src):
            missing_names.append(name or "?")
    if not missing_names:
        return []
    uniq = list(dict.fromkeys(missing_names))
    who = ", ".join(uniq[:8])
    return [
        f"Team logo missing on {len(missing_names)}/{len(slots)} faces ({who})"
    ]


def h2h_gap_issues(html: str) -> list[str]:
    """H2H Last 10 present on some cards but — on others (or all —).

    Face dashes count even when ``data-h2h-reason`` is set. ``First meeting``
    is a real label, not a missing value.
    """
    html = html or ""
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html or "", flags=re.I)
    vals: list[str] = []
    for part in parts[1:] if len(parts) > 1 else [html]:
        found = re.findall(
            r"H2H Last 10</span>\s*<span class=\"sf-val\">([\s\S]*?)</span>",
            part,
            flags=re.I,
        )
        if not found:
            found = re.findall(
                r'class="line-chip-label">\s*H2H Last 10\s*</div>\s*'
                r'<div class="line-chip-val">([\s\S]*?)</div>',
                part,
                flags=re.I,
            )
        vals.extend(found)
        if not found:
            data = re.search(r'data-h2h="([^"]*)"', part, flags=re.I)
            if data:
                vals.append(data.group(1))
    if len(vals) < 3:
        return []

    def _blank(v: str) -> bool:
        if re.search(r"first meeting", v or "", flags=re.I):
            return False
        return _is_dash(v)

    blank = sum(1 for v in vals if _blank(v))
    if blank == 0:
        return []
    if blank == len(vals):
        return [f"H2H Last 10 missing on {blank}/{len(vals)} cards (all —)"]
    return [f"H2H Last 10 missing on {blank}/{len(vals)} cards"]


_SIX_MODEL_SPORTS = frozenset({"NFL", "NHL", "NCAAF", "CFL"})
_TEAM_SPORTS_SHARED_UI = frozenset({
    "MLB", "NHL", "NBA", "NCAAB", "NCAAW", "NFL", "NCAAF", "WNBA", "CFL",
})
_IN_SEASON_FOOTBALL = frozenset({"NFL", "NCAAF", "CFL"})
_OFFSEASON_TEAM = frozenset({"NBA", "NCAAB", "NCAAW"})
_WEEKLY_FRANKENSTEIN = (
    "NFL - Week by Week",
    "Week by Week Results",
    'class="week-section"',
    "class='week-section'",
)
_CHART_CARD_BOARD = (
    "Last Night's NFL Results",
    "Last Night's NCAA Football Results",
    "Last Night's CFL Results",
    "Last 7 Days NFL Results",
    "Season Performance",
    "Moneyline Accuracy by Model",
    "Overall Model Performance",
)


def six_model_chart_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when a 6-model sport's consensus is not the MLB 6/6 dissent table.

    Required shape (MLB Consensus Based Betting Records):
    6 live models, 6/6 unanimous + all-but rows, Agreement / Last night /
    Past 7 / Past 30, color bars, Moneyline | Spread | Totals tabs.
    """
    sport_u = (sport or "").strip().upper()
    if sport_u not in _SIX_MODEL_SPORTS:
        return []
    html = html or ""
    if "Consensus Based Betting Records" not in html:
        return []
    issues: list[str] = []
    if re.search(
        r"among the [45] live models|[45] live models",
        html,
        flags=re.I,
    ):
        issues.append(f"{sport_u} consensus is not 6-model (must be 6/6 like MLB)")
    if "4 live models" in html or "among the 4 live" in html:
        issues.append(f"{sport_u} consensus is 4-model (must be 6/6 like MLB)")
    if re.search(r"\b4/4\b", html):
        issues.append(f"{sport_u} consensus rows are 4/4 (must be 6/6 like MLB)")
    if re.search(r"\b5/5\b", html) and not re.search(r"\b6/6\b", html):
        issues.append(f"{sport_u} consensus rows are 5/5 (must be 6/6 like MLB)")
    if "2/4 Split / no consensus" in html or "1/4 Strong disagreement" in html:
        issues.append(f"{sport_u} chart has WNBA 2/4 / 1/4 rows")
    if not re.search(r"6/6\s+unanimous", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing 6/6 unanimous row")
    if not re.search(r"all but", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing all-but dissent rows")
    buckets = re.findall(r'class="bucket">([^<]+)', html)
    cons_rows = [
        b
        for b in buckets
        if re.search(r"\d+\s*/\s*\d+|unanimous|all but", b, flags=re.I)
    ]
    if len(cons_rows) < 2:
        issues.append(
            f"{sport_u} consensus has {len(cons_rows)} agreement row(s) "
            "(MLB lists 6/6 + all-but rows)"
        )
    if "cons-bar" not in html:
        issues.append(f"{sport_u} consensus missing color bars")
    if not re.search(r"Agreement", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing Agreement column")
    if not re.search(r"Last night", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing Last night column")
    if not re.search(r"Past 7 days", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing Past 7 days column")
    if not re.search(r"Past 30 days", html, flags=re.I):
        issues.append(f"{sport_u} consensus missing Past 30 days column")
    if not has_market_tabs(html):
        issues.append(f"{sport_u} consensus missing Moneyline | Spread | Totals tabs")
    return issues


def team_picks_template_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when a team-sport picks page leaves the shared pick-card template."""
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI:
        return []
    html = html or ""
    if sport_u in _OFFSEASON_TEAM and "data-pick-card" not in html:
        return []
    issues: list[str] = []
    if "data-pick-card" not in html and "pick-conf-grid" not in html:
        issues.append(
            f"{sport_u} picks is missing the shared pick-conf-grid cards"
        )
        return issues
    if "pick-conf-grid" not in html and "pc-name" not in html:
        issues.append(f"{sport_u} picks cards are not the shared model grid")
    return issues


def team_results_template_issues(
    html: str, sport: str = "", today: date | None = None
) -> list[str]:
    """FAIL when a team-sport results page is not the shared daily template.

    Catches the empty NFL page (Last Night + one Thursday card) that the
    weekly pipeline produced while August/Week-1 games sat in the DB.
    """
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI:
        return []
    html = html or ""
    if len(html) < 400 and "daily-tally" not in html and "Last Night" not in html:
        return [f"{sport_u} results did not load the shared daily template"]
    if sport_u in _OFFSEASON_TEAM and "game-card" not in html and "data-pick-card" not in html:
        return []
    issues: list[str] = []
    for marker in _WEEKLY_FRANKENSTEIN:
        if marker in html:
            issues.append(
                f"{sport_u} results uses a sport-specific weekly board "
                "(must use the shared daily results template)"
            )
            break
    if "daily-tally" not in html and "Last Night" not in html:
        issues.append(f"{sport_u} results missing the shared Last Night tally")
    if "Season Performance" not in html:
        issues.append(f"{sport_u} results missing shared Season Performance")
    n_cards = html.count("game-card") + html.count('data-pick-card')
    n_dates = len(set(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html)))
    if n_cards < 1:
        issues.append(f"{sport_u} results has no shared game cards")
    if "date-nav" not in html and n_dates < 1:
        issues.append(f"{sport_u} results missing the shared date nav")
    if "Consensus Based Betting Records" not in html:
        issues.append(
            f"{sport_u} results missing shared Consensus Based Betting Records"
        )
    today = today or date.today()
    if sport_u in _IN_SEASON_FOOTBALL and today.month in (8, 9, 10, 11, 12):
        if n_dates <= 1 or n_cards < 3:
            issues.append(
                f"{sport_u} results only has {n_dates} date(s) and {n_cards} "
                "game card(s) — team-sports results must show the shared "
                "multi-day slate, not last night only"
            )
    return issues


def team_chart_template_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when a team-sport chart view is still the card board."""
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI or sport_u == "MLB":
        return []
    html = html or ""
    if not html:
        return [f"{sport_u} chart view did not load"]
    issues: list[str] = []
    if "Consensus Based Betting Records" not in html:
        issues.append(f"{sport_u} chart view missing Consensus Based Betting Records")
    board = (
        "Season Performance",
        "Moneyline Accuracy by Model",
        "Overall Model Performance",
        f"Last Night's {sport_u} Results",
        "Last Night's NCAA Football Results" if sport_u == "NCAAF" else "",
        f"Last 7 Days {sport_u} Results",
    )
    for marker in board:
        if marker and marker in html:
            issues.append(
                f"{sport_u} chart view still shows {marker} "
                "(must be the shared consensus table)"
            )
    if sport_u in _SIX_MODEL_SPORTS:
        issues.extend(six_model_chart_issues(html, sport_u))
    issues.extend(team_chart_leftover_board_issues(html, sport_u))
    issues.extend(team_chart_window_tally_issues(html, sport_u))
    return issues


_SIX_MODEL_TALLY = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)
_FOUR_MODEL_TALLY = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")
_CHART_LEFTOVER = (
    "ncaafTopDates",
    "ncaaf-top-dates",
    "Model Performance (Flat Unit Tracking)",
    "Moneyline Accuracy by Model",
)


def _tally_models_for_sport(sport: str) -> tuple[str, ...]:
    sport_u = (sport or "").strip().upper()
    if sport_u in ("WNBA", "NBA"):
        return _FOUR_MODEL_TALLY
    return _SIX_MODEL_TALLY


def _section_between(html: str, start: str, end: str) -> str:
    i = html.find(start)
    if i < 0:
        return ""
    j = html.find(end, i + len(start)) if end else -1
    return html[i:j] if j > i else html[i : i + 4000]


def team_results_tally_model_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when Last Night / Last 7 / Season Accuracy hide a required model.

    NFL-only nfl_missing_efficiency_issues missed NCAAF because Efficiency
    was deleted from the board instead of shown as a dash.
    """
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI:
        return []
    html = html or ""
    if "Last Night" not in html and "Moneyline Accuracy by Model" not in html:
        return []
    if re.search(r"data-ssr-chart\s*=\s*[\"']1[\"']", html) or (
        f"{sport_u.lower()}-results-chart" in html
        and "Last Night's" not in html
        and "Moneyline Accuracy by Model" not in html
    ):
        return []
    required = _tally_models_for_sport(sport_u)
    issues: list[str] = []
    sections = (
        ("Last Night", "Last Night", "Last 7 Days"),
        ("Last 7 Days", "Last 7 Days", "Season Performance"),
        (
            "Moneyline Accuracy by Model",
            "Moneyline Accuracy by Model",
            'class="date-nav"',
        ),
    )
    for label, start, end in sections:
        chunk = _section_between(html, start, end)
        if not chunk:
            if label == "Last Night" and "Last Night" not in html:
                issues.append(f"{sport_u} results missing Last Night tally")
            elif label == "Last 7 Days" and "Last 7 Days" not in html:
                issues.append(f"{sport_u} results missing Last 7 Days tally")
            elif (
                label == "Moneyline Accuracy by Model"
                and "Moneyline Accuracy by Model" not in html
            ):
                issues.append(
                    f"{sport_u} results missing Moneyline Accuracy by Model"
                )
            continue
        missing = [name for name in required if name not in chunk]
        if missing:
            issues.append(
                f"{sport_u} {label} is missing {', '.join(missing)} "
                "(team-sports results must show every live model)"
            )
    return issues


def results_card_parameter_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when results game-cards drop Odds & Lines, models, or Efficiency."""
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI:
        return []
    html = html or ""
    parts = re.split(
        r'(?=<div\b[^>]*class="[^"]*\bgame-card\b)',
        html,
        flags=re.I,
    )
    cards = [p for p in parts[1:] if "pick-conf-grid" in p or "pc-name" in p]
    if len(cards) < 2:
        return []
    required = _tally_models_for_sport(sport_u)
    missing_model = 0
    blank_eff = 0
    missing_odds = 0
    missing_h2h = 0
    for card in cards:
        names = [
            re.sub(r"<[^>]+>", "", n).strip()
            for n in re.findall(
                r'class="pc-name">\s*([\s\S]*?)</div>', card, flags=re.I
            )
        ]
        vals = [
            re.sub(r"<[^>]+>", "", v).strip()
            for v in re.findall(
                r'class="pc-val"[^>]*>\s*([\s\S]*?)</div>', card, flags=re.I
            )
        ]
        have = {n.lower() for n in names}
        if any(model.lower() not in have for model in required):
            missing_model += 1
        for name, val_html in zip(names, vals):
            if name.lower() != "efficiency":
                continue
            val = val_html.lower().replace("&mdash;", "—").replace("&ndash;", "–")
            if val in {"", "n/a", "na", "—", "–", "-"}:
                blank_eff += 1
        if "Odds" not in card and "odds-pricing" not in card:
            missing_odds += 1
        if "H2H Last 10" not in card:
            missing_h2h += 1
    issues: list[str] = []
    label = sport_u or "results"
    if missing_model:
        issues.append(
            f"{label}: {missing_model}/{len(cards)} results cards are missing a "
            "model box (Grinder2 / Takedown / Edge / XSharp / Sharp Consensus / "
            "Efficiency)"
        )
    if blank_eff:
        issues.append(
            f"{label}: {blank_eff}/{len(cards)} results cards show Efficiency "
            "as N/A or blank"
        )
    if missing_odds:
        issues.append(
            f"{label}: {missing_odds}/{len(cards)} results cards are missing "
            "Odds & Lines"
        )
    if missing_h2h:
        issues.append(
            f"{label}: {missing_h2h}/{len(cards)} results cards are missing "
            "H2H Last 10"
        )
    return issues


def team_chart_leftover_board_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when ?view=chart still has cards-page leftovers (date strip, ROI)."""
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI or sport_u == "MLB":
        return []
    html = html or ""
    if not html:
        return [f"{sport_u} chart view did not load"]
    issues: list[str] = []
    for marker in _CHART_LEFTOVER:
        if marker in html:
            issues.append(
                f"{sport_u} chart view still shows leftover cards chrome "
                f"({marker}) — Chart must be the shared consensus table"
            )
    n_cards = html.count("game-card") + html.count("data-pick-card")
    if n_cards >= 3:
        issues.append(
            f"{sport_u} chart view still has {n_cards} game cards "
            "(Chart must not be a stripped cards page)"
        )
    return issues


def team_chart_window_tally_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when team-sport chart lacks Last Night / Last 7 / Season model cards."""
    sport_u = (sport or "").strip().upper()
    if sport_u not in _TEAM_SPORTS_SHARED_UI or sport_u == "MLB":
        return []
    html = html or ""
    if not html:
        return [f"{sport_u} chart view did not load"]
    issues: list[str] = []
    if not re.search(r"Last Night", html, flags=re.I):
        issues.append(f"{sport_u} chart missing Last Night tally")
    if not re.search(r"Last 7|Past 7", html, flags=re.I):
        issues.append(f"{sport_u} chart missing Last 7 tally")
    if not re.search(r">\s*Season\s*<", html, flags=re.I) and "Season " not in html:
        issues.append(f"{sport_u} chart missing Season tally")
    n_cards = html.count("tally-card") + html.count("daily-tally-card")
    need = 4 if sport_u in ("WNBA", "NBA") else 6
    if n_cards < need:
        issues.append(
            f"{sport_u} chart Last Night / Last 7 / Season have {n_cards} "
            f"model card(s) — shared chart shows the {need}-model rows"
        )
    return issues


def team_chart_same_as_cards_issues(
    cards_html: str, chart_html: str, sport: str = ""
) -> list[str]:
    """FAIL when Cards and Chart are the same board."""
    sport_u = (sport or "").strip().upper()
    cards_html = cards_html or ""
    chart_html = chart_html or ""
    if not cards_html or not chart_html:
        return [f"{sport_u} cards or chart view did not load"]
    if cards_html == chart_html:
        return [f"{sport_u} results chart view looks the same as cards"]
    leftovers = team_chart_leftover_board_issues(chart_html, sport_u)
    if leftovers:
        return leftovers
    return []


_ML_TALLY_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)
_ML_TALLY_REQUIRED = ("Grinder2", "Takedown", "Efficiency")
_ML_TALLY_SIBLINGS = ("Edge", "XSharp", "Sharp Consensus")
_DASH_REC = frozenset({"", "—", "–", "-", "n/a", "na", "&mdash;", "&ndash;"})


def _plain_results_text(html: str) -> str:
    """HTML or copied results text → line-oriented plain text."""
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "\n", html or "", flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:div|tr|p|h[1-6]|li|section)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&mdash;", "—")
        .replace("&ndash;", "–")
        .replace("&nbsp;", " ")
        .replace("⭐", " ")
        .replace("🎯", " ")
        .replace("📊", " ")
        .replace("🤖", " ")
        .replace("🏆", " ")
        .replace("⚡", " ")
        .replace("📈", " ")
        .replace("🎲", " ")
        .replace("💰", " ")
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text


def _is_blank_tally_val(raw: str) -> bool:
    text = re.sub(r"\s+", "", raw or "").strip().lower()
    return text in _DASH_REC


def _is_zero_rec(raw: str) -> bool:
    return bool(re.fullmatch(r"0-0(?:-0)?", (raw or "").strip()))


def _has_wl_rec(raw: str) -> bool:
    return bool(re.search(r"\b\d+-\d+(?:-\d+)?\b", raw or "")) and not _is_zero_rec(raw)


def _tally_sections(text: str) -> list[tuple[str, str]]:
    """Last Night / Last 7 / season moneyline-accuracy blocks."""
    starts = [
        m
        for m in re.finditer(
            r"Last Night's \w[\w /]* Results|Last 7 Days \w[\w /]* Results|"
            r"Moneyline Accuracy by Model|Season Performance",
            text,
            flags=re.I,
        )
    ]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        label = re.sub(r"\s+", " ", m.group(0)).strip()
        # Season Performance also contains Edge/Spread/O/U hero rows — only the
        # per-model moneyline list is a blank-G2/TD/Eff fail.
        chunk = text[m.start() : end]
        if re.search(r"Season Performance", label, flags=re.I):
            acc = re.search(r"Moneyline Accuracy by Model", chunk, flags=re.I)
            if acc:
                chunk = chunk[acc.start() :]
                label = "Moneyline Accuracy by Model"
            else:
                continue
        if re.search(r"Moneyline Accuracy by Model", label, flags=re.I):
            stop = re.search(
                r"Consensus Based Betting Records|PL vs Sportsbook|"
                r"Moneyline Spread Totals|📅|date-nav",
                chunk[40:],
                flags=re.I,
            )
            if stop:
                chunk = chunk[: stop.start() + 40]
        out.append((label, chunk))
    return out


def _models_in_tally_chunk(chunk: str) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    names = "|".join(re.escape(n) for n in _ML_TALLY_MODELS)
    for m in re.finditer(
        rf"\b({names})\b\s+([^\n]+)\s+([^\n]+)",
        chunk,
    ):
        name, a, b = m.group(1), m.group(2).strip(), m.group(3).strip()
        if name not in found:
            found[name] = (a, b)
    return found


def blank_moneyline_model_issues(html: str) -> list[str]:
    """FAIL when G2 / Takedown / Efficiency are — on a slate that graded other ML models.

    Catches the Aug 29 NFL last-night / last-7 / season accuracy board where
    Edge / XSharp / Sharp Consensus have W-L but Grinder2, Takedown, and
    Efficiency are dashes.
    """
    text = _plain_results_text(html)
    # NBA / WNBA / hoops list leftover G2/TD rows — only 6-model football/hockey
    # slates must have those moneyline models filled.
    if not re.search(
        r"(?:Last Night's|Last 7 Days) (?:NFL|NHL|CFL|NCAA Football) Results",
        text,
        flags=re.I,
    ):
        return []
    issues: list[str] = []
    for label, chunk in _tally_sections(text):
        if not re.search(r"Last Night|Last 7 Days", label, flags=re.I):
            continue
        models = _models_in_tally_chunk(chunk)
        # Footer "Premium Edge — $4.99/wk" is not a moneyline board.
        sibling_hit = any(
            _has_wl_rec(models[n][0]) or _has_wl_rec(models[n][1])
            for n in _ML_TALLY_SIBLINGS
            if n in models
        )
        if not sibling_hit:
            continue
        required = _ML_TALLY_REQUIRED
        if re.search(r"NCAA Football", label, flags=re.I):
            # NCAAF still hides a blank Efficiency tile when no PL spread
            # is stored. Missing G2/TD is the miss; do not fail Efficiency.
            required = ("Grinder2", "Takedown")
        missing = [n for n in required if n not in models]
        blank = [
            n
            for n in required
            if n in models
            and _is_blank_tally_val(models[n][0])
            and _is_blank_tally_val(models[n][1])
        ]
        bad = missing + blank
        if bad:
            issues.append(
                f"{label}: {', '.join(bad)} are missing or — while other "
                "moneyline models have a record"
            )
    return issues


def _last_night_dates(text: str) -> list[str]:
    dates = re.findall(
        r"Last Night's [^—\n]{0,40}—\s*(\d{4}-\d{2}-\d{2})",
        text,
        flags=re.I,
    )
    dates += re.findall(r"Last night \((\d{4}-\d{2}-\d{2})\)", text, flags=re.I)
    return dates


def last_night_window_mismatch_issues(html: str) -> list[str]:
    """FAIL when Last Night is Super Bowl on one chart and Aug 29 on another."""
    dates = list(dict.fromkeys(_last_night_dates(_plain_results_text(html))))
    if len(dates) < 2:
        return []
    return [f"Last night windows disagree ({', '.join(dates)})"]


def _last_night_game_count(text: str) -> int:
    nums = [
        int(n)
        for n in re.findall(
            r"Last Night's [^—\n]{0,40}—\s*\d{4}-\d{2}-\d{2}\s*\((\d+)\s*games?\)",
            text,
            flags=re.I,
        )
    ]
    return max(nums) if nums else 0


def _first_col_records(block: str, row_pat: str) -> list[str]:
    recs: list[str] = []
    wl = re.compile(r"\b(\d{1,3}-\d{1,3}(?:-\d{1,3})?)\b")
    for m in re.finditer(row_pat, block, flags=re.I | re.M):
        tail = block[m.end() : m.end() + 180]
        found = wl.search(tail)
        if found:
            recs.append(found.group(1))
    return recs


def consensus_empty_vs_tally_issues(html: str) -> list[str]:
    """FAIL when last-night consensus is all 0-0 but the daily tally graded games."""
    text = _plain_results_text(html)
    if ML_CHART not in text:
        return []
    games = _last_night_game_count(text)
    if games < 1:
        return []
    start = text.find(ML_CHART)
    end = text.find(PL_VS_BOOKS, start + 1)
    block = text[start : end if end > start else start + 4000]
    recs = _first_col_records(
        block, r"(?m)^(?:\d+/\d+\s+unanimous|\d+/\d+\s+[—-]\s+all but)"
    )
    if len(recs) < 2:
        return []
    if all(_is_zero_rec(r) for r in recs):
        return [
            f"Consensus last night is all 0-0 while Last Night tally has {games} games"
        ]
    return []


def pl_vs_books_empty_vs_tally_issues(html: str) -> list[str]:
    """FAIL when PL vs Sportsbook last night is 0-0 but the daily tally graded games."""
    text = _plain_results_text(html)
    if PL_VS_BOOKS not in text:
        return []
    games = _last_night_game_count(text)
    if games < 1:
        return []
    start = text.find(PL_VS_BOOKS)
    block = text[start : start + 2500]
    recs = _first_col_records(
        block,
        r"(?m)^(Books favorite|PL favorite|PL vs Books disagree|PL and Books agree)",
    )
    if len(recs) < 2:
        return []
    if all(_is_zero_rec(r) for r in recs):
        return [
            f"PL vs Sportsbook last night is all 0-0 while Last Night tally has {games} games"
        ]
    # row order: Books favorite, PL favorite, disagree, agree
    if len(recs) >= 2 and _is_zero_rec(recs[0]) and not _is_zero_rec(recs[1]):
        return [
            "Books favorite last night is 0-0 while PL favorite has a graded record"
        ]
    return []


def ncaaf_results_date_nav_issues(html: str) -> list[str]:
    """FAIL when NCAAF results hide dates below Last Night or leave the strip empty."""
    html = html or ""
    if "ncaaf-results" not in html and "NCAA Football Results" not in html:
        return []
    dates = list(dict.fromkeys(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html)))
    if len(dates) < 3:
        return []
    issues: list[str] = []
    top = re.search(
        r'<nav[^>]*(?:id="ncaafTopDates"|class="[^"]*ncaaf-top-dates)[^>]*>([\s\S]*?)</nav>',
        html,
        flags=re.I,
    )
    ln = html.find("Last Night")
    if not top:
        issues.append("NCAAF results has no date strip at the top")
        return issues
    if ln >= 0 and top.start() > ln:
        issues.append("NCAAF date strip is below Last Night (must be at the top)")
    bubbles = len(re.findall(r'data-date="\d{4}-\d{2}-\d{2}"', top.group(1)))
    if bubbles < 3:
        issues.append(f"NCAAF top date strip has {bubbles} day(s) (need at least 3)")
    return issues


def _parse_ymd(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def nfl_stale_season_week_issues(html: str, today: date | None = None) -> list[str]:
    """FAIL when NFL results still open on last season's Super Bowl / playoffs.

    Owner 2026-09-10: page led with Week 22, Seattle @ New England on
    2026-02-08, during Week 1 of the new regular season.
    """
    today = today or date.today()
    if today.month not in (8, 9, 10, 11, 12):
        return []
    blob = html or ""
    text = _plain_results_text(blob)
    if not re.search(r"Week\s+\d+", text):
        return []
    nflish = bool(
        re.search(
            r"\bNFL\b|nfl-results|week-title|Seahawks|Patriots|Grinder2",
            blob,
            flags=re.I,
        )
    )
    if not nflish:
        return []
    weeks = list(re.finditer(r"(?:🏈\s*)?Week\s+(\d+)\b", text, flags=re.I))
    if not weeks:
        return []
    lead = int(weeks[0].group(1))
    start = weeks[0].start()
    end = weeks[1].start() if len(weeks) > 1 else min(len(text), start + 2500)
    chunk = text[start:end]
    dates = [d for d in (_parse_ymd(x) for x in re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", chunk)) if d]
    newest = max(dates) if dates else None
    playoff_week = lead >= 19
    last_season_date = newest is not None and newest.month in (1, 2)
    if not playoff_week and not last_season_date:
        return []
    extra = f", newest game {newest.isoformat()}" if newest else ""
    return [
        f"NFL results lead with Week {lead}{extra} — last season's playoffs "
        f"during the {today.year} regular season"
    ]


def nfl_stale_season_perf_issues(html: str, today: date | None = None) -> list[str]:
    """FAIL when Season Performance still shows last season's 200+ game sample.

    Owner 2026-09-10: banner was Moneyline (XSharp) 72.3% 206-79 from the
    frozen 2025-26 snapshot while Week 1 of 2026 was on the cards.
    """
    today = today or date.today()
    blob = html or ""
    text = _plain_results_text(blob)
    if "Season Performance" not in text:
        return []
    nflish = bool(
        re.search(
            r'rel=["\']canonical["\'][^>]+/nfl-results|Last Night\'s NFL Results',
            blob,
            flags=re.I,
        )
    )
    if not nflish:
        return []
    idx = text.find("Season Performance")
    chunk = text[idx : idx + 1200]
    rec = re.search(
        r"Moneyline[^\n]*\n\s*[\d.]+%\s*\n\s*(\d+)-(\d+)",
        chunk,
        flags=re.I,
    )
    if not rec:
        rec = re.search(r"Moneyline[^\n]*?(\d+)-(\d+)", chunk, flags=re.I)
    if not rec:
        return []
    wins = int(rec.group(1))
    losses = int(rec.group(2))
    total = wins + losses
    n_cards = len(re.findall(r"class=[\"'][^\"']*game-card", blob, flags=re.I))
    early = today.month in (8, 9, 10)
    if early and total >= 180:
        return [
            f"NFL Season Performance moneyline is {wins}-{losses} ({total} games) "
            f"— last season's snapshot during {today.strftime('%B')} {today.year}"
        ]
    has_preseason = bool(re.search(r"\b20\d{2}-08-\d{2}\b", blob))
    week1 = today.month == 9 and today.day <= 14
    if week1 and has_preseason and total >= 8:
        return [
            f"NFL Season Performance moneyline is {wins}-{losses} ({total} games) "
            f"— preseason is still in the Season board; regular season only"
        ]
    if n_cards >= 3 and total > n_cards + 40:
        return [
            f"NFL Season Performance moneyline is {wins}-{losses} ({total} games) "
            f"but the page lists {n_cards} cards — last season leak"
        ]
    return []


def nfl_missing_efficiency_issues(html: str) -> list[str]:
    """FAIL when NFL results omit Efficiency while the other ML models are shown.

    blank_moneyline_model_issues used to require Efficiency to already be in
    the Last Night / Last 7 board (present as a dash). The NFL weekly template
    left Efficiency out entirely, so that check passed on a 5-model page.
    """
    blob = html or ""
    text = _plain_results_text(blob)
    nflish = bool(
        re.search(
            r"Last Night's NFL Results|NFL - Week by Week|nfl-results|\bNFL\b",
            blob + "\n" + text,
            flags=re.I,
        )
    )
    if not nflish:
        return []
    # Chart view is the MLB dissent table — Last Night tiles are stripped.
    if "Last Night's NFL Results" not in blob and "NFL - Week by Week" not in blob:
        return []
    if not re.search(r"(?:Last Night|Last 7 Days|Week by Week|Overall Model)", text, flags=re.I):
        return []
    issues: list[str] = []
    if not re.search(r"\bEfficiency\b", text):
        issues.append(
            "NFL results omit Efficiency while Grinder2 / Takedown / Edge / "
            "XSharp / Sharp Consensus are shown"
        )
        return issues
    if re.search(r"<th>\s*Grinder2\s*</th>", blob, flags=re.I) and not re.search(
        r"<th>\s*Efficiency\s*</th>", blob, flags=re.I
    ):
        issues.append("NFL week game table has no Efficiency column")
    return issues


_CARD_MODELS = (
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
    "Grinder2",
    "Takedown",
)


def _norm_team(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _name_is_side(name: str, home: str, away: str) -> bool:
    n = _norm_team(name)
    if not n or n in {"na", "n/a"}:
        return True
    sides = [_norm_team(home), _norm_team(away)]
    return any(s and (n == s or n in s or s in n) for s in sides)


def card_matchup_mismatch_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when a card's face / projection / models are a different game."""
    html = html or ""
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    bad = 0
    examples: list[str] = []
    for part in parts[1:]:
        tag = re.match(r"<div\b[^>]*>", part, flags=re.I)
        if not tag:
            continue
        home_m = re.search(r'\bdata-home="([^"]*)"', tag.group(0), flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', tag.group(0), flags=re.I)
        home = html_lib.unescape((home_m.group(1) if home_m else "").strip())
        away = html_lib.unescape((away_m.group(1) if away_m else "").strip())
        if not home or not away:
            continue
        faces = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", n).strip())
            for n in re.findall(
                r'class="team-name[^"]*"[^>]*>\s*([\s\S]*?)</div>', part, flags=re.I
            )
        ]
        faces = [n for n in faces if n][:2]
        projs = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", v).strip())
            for v in re.findall(
                r'class="proj-val"[^>]*>\s*([\s\S]*?)</span>', part, flags=re.I
            )
        ]
        sides = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", s).strip())
            for s in re.findall(
                r'class="pc-side[^"]*"[^>]*>\s*([\s\S]*?)</div>', part, flags=re.I
            )
        ]
        mismatch = False
        if len(faces) >= 2 and not (
            _name_is_side(faces[0], home, away) and _name_is_side(faces[1], home, away)
        ):
            mismatch = True
        for proj in projs:
            names = re.findall(r"[A-Za-z][A-Za-z\- ]{2,}", proj)
            names = [n.strip(" –-") for n in names if not re.fullmatch(r"\d+", n)]
            if any(not _name_is_side(n, home, away) for n in names if len(n) > 3):
                mismatch = True
                break
        if any(s and not _name_is_side(s, home, away) for s in sides):
            mismatch = True
        if mismatch:
            bad += 1
            if len(examples) < 2:
                examples.append(f"{away} @ {home}")
    if not bad:
        return []
    label = (sport or "cards").strip() or "cards"
    extra = f" (e.g. {'; '.join(examples)})" if examples else ""
    return [
        f"{label}: {bad} card(s) show a different matchup on the face / "
        f"projected score / models than the card header{extra}"
    ]


def wrong_sport_heading_issues(html: str, sport: str) -> list[str]:
    """FAIL when the picks H1 / sportName JS is a different sport."""
    sport_u = (sport or "").strip().upper()
    if not sport_u or not html:
        return []
    issues = []
    heading = re.search(
        r'<h1\b[^>]*id="pageHeading"[^>]*>([\s\S]*?)</h1>', html, flags=re.I
    )
    if heading:
        text = re.sub(r"<[^>]+>", " ", heading.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        others = ("MLB", "NBA", "NFL", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW", "CFL", "SOCCER")
        for other in others:
            if other == sport_u:
                continue
            if re.search(rf"\b{re.escape(other)}\b", text):
                issues.append(f"Heading is {other} on a {sport_u} page: {text[:80]}")
                break
    names = re.findall(r"const sportName = ([\"'])([^\"']+)\1", html)
    for _q, name in names:
        if name.strip().upper() not in {sport_u, sport_u.replace("SOCCER", "SOCCER")} and name.strip().upper() != sport_u:
            if name.strip().upper() in {
                "MLB", "NBA", "NFL", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW", "CFL",
            } and name.strip().upper() != sport_u:
                issues.append(f"sportName JS is {name!r} on a {sport_u} page")
                break
    return issues


def card_missing_model_value_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when pick cards drop model boxes or leave them blank/N/A."""
    html = html or ""
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    cards = [p for p in parts[1:] if "pick-conf-grid" in p or "pc-name" in p]
    if len(cards) < 2:
        return []
    missing_box = 0
    blank_val = 0
    required = _CARD_MODELS
    if (sport or "").strip().lower() == "soccer":
        required = ("Edge", "XSharp", "Sharp Consensus")
    for card in cards:
        names = [
            re.sub(r"<[^>]+>", "", n).strip()
            for n in re.findall(
                r'class="pc-name">\s*([\s\S]*?)</div>', card, flags=re.I
            )
        ]
        vals = [
            re.sub(r"<[^>]+>", "", v).strip()
            for v in re.findall(
                r'class="pc-val"[^>]*>\s*([\s\S]*?)</div>', card, flags=re.I
            )
        ]
        have = {n.lower() for n in names}
        for model in required:
            if model.lower() not in have:
                missing_box += 1
                break
        need = {n.lower() for n in required}
        for name, val_html in zip(names, vals):
            if name.lower() not in need:
                continue
            val = val_html.lower().replace("&mdash;", "—").replace("&ndash;", "–")
            if val in {"", "n/a", "na", "—", "–", "-"}:
                blank_val += 1
                break
            if name.lower() == "edge" and _is_fifty(val_html):
                blank_val += 1
                break
    issues = []
    label = (sport or "cards").strip() or "cards"
    if missing_box:
        issues.append(
            f"{label}: {missing_box}/{len(cards)} cards are missing a model box "
            "(Edge / XSharp / Sharp Consensus / Efficiency / Grinder2 / Takedown)"
        )
    if blank_val:
        issues.append(
            f"{label}: {blank_val}/{len(cards)} cards have a blank, N/A, or 50% Edge model value"
        )
    return issues


def _odds_cells(card: str, market: str) -> tuple[str, str, str] | None:
    m = re.search(
        rf'<td class="market-k">\s*{re.escape(market)}\s*</td>\s*'
        r'(?:<td class="val-books">([\s\S]*?)</td>\s*)?'
        r'<td class="val-pl">([\s\S]*?)</td>\s*'
        r'<td class="val-xs">([\s\S]*?)</td>',
        card,
        flags=re.I,
    )
    if not m:
        return None
    books = re.sub(r"<[^>]+>", "", m.group(1) or "").strip()
    pl = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
    xs = re.sub(r"<[^>]+>", "", m.group(3) or "").strip()
    return books, pl, xs


def card_blank_market_line_issues(html: str, sport: str = "") -> list[str]:
    """FAIL when Odds & Lines leaves PL / XSharp spread or total as —."""
    html = html or ""
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    cards = [p for p in parts[1:] if "market-k" in p]
    if len(cards) < 2:
        return []
    xs_sp = pl_tot = xs_tot = xs_proj = 0
    for card in cards:
        spread = _odds_cells(card, "Spread") or _odds_cells(card, "Run Line")
        total = _odds_cells(card, "Total")
        if spread:
            books, pl, xs = spread
            if not _is_dash(books) and _is_dash(xs):
                xs_sp += 1
        if total:
            books, pl, xs = total
            if not _is_dash(books) and _is_dash(pl):
                pl_tot += 1
            if not _is_dash(books) and _is_dash(xs):
                xs_tot += 1
        proj = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bproj-model\b[^"]*"[^>]*>\s*XSharp\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bproj-val\b[^"]*"[^>]*>([\s\S]*?)</span>',
            card,
            flags=re.I,
        )
        if proj and _is_dash(re.sub(r"<[^>]+>", "", proj.group(1) or "").strip()):
            xs_proj += 1
    issues = []
    label = (sport or "cards").strip() or "cards"
    n = len(cards)
    if xs_sp:
        issues.append(f"{label}: {xs_sp}/{n} cards have a blank XSharp spread")
    if pl_tot:
        issues.append(f"{label}: {pl_tot}/{n} cards have a blank Prediction Lab total")
    if xs_tot:
        issues.append(f"{label}: {xs_tot}/{n} cards have a blank XSharp total")
    if xs_proj:
        issues.append(f"{label}: {xs_proj}/{n} cards have a blank XSharp projected score")
    return issues


def nba_last_season_gap_issues(html: str) -> list[str]:
    """FAIL when NBA results last-season boards are empty stubs."""
    html = html or ""
    if "NBA" not in html and "nba-results" not in html:
        return []
    text = _plain_results_text(html)
    issues = []
    if "Season Performance" not in text and "Moneyline Accuracy" not in text:
        issues.append("NBA last-season results have no Season Performance / moneyline board")
    if ML_CHART not in html:
        issues.append("NBA last-season results missing Consensus Based Betting Records")
    if PL_VS_BOOKS not in html:
        issues.append("NBA last-season results missing PL vs Sportsbook")
    zeros = len(re.findall(r"\b0-0(?:-0)?\b", text))
    real = len(re.findall(r"\b[1-9]\d*-\d+\b", text))
    if zeros >= 8 and real < 4:
        issues.append(
            f"NBA last-season results look empty ({zeros} 0-0 rows, {real} real records)"
        )
    return issues


def mlb_xsharp_totals_issues(cards_html: str = "", chart_html: str = "") -> list[str]:
    """FAIL when MLB results drop Prediction Lab · XSharp — Totals."""
    issues: list[str] = []
    for label, html in (("cards", cards_html or ""), ("chart", chart_html or "")):
        if not html:
            if label == "chart" and cards_html:
                issues.append("MLB /mlb-results?view=chart did not load")
            continue
        if not has_totals_chart(html):
            issues.append(
                f"MLB results {label} missing Prediction Lab · XSharp — Totals"
            )
        if "XSharp" not in html:
            issues.append(f"MLB results {label} has no XSharp face at all")
    return issues


def nhl_chart_view_missing_issues(chart_html: str, cards_html: str = "") -> list[str]:
    """FAIL when NHL has no real chart view (offseason skip used to hide this)."""
    chart_html = chart_html or ""
    if not chart_html:
        return ["NHL /nhl-results?view=chart did not load"]
    if ML_CHART not in chart_html and "cons-bar" not in chart_html:
        return ["NHL chart view does not exist (no Consensus Based Betting Records)"]
    cards = cards_html or ""
    if cards and chart_html == cards:
        return ["NHL chart view is the same page as cards"]
    return []


_NFL_CHART_CARD_BOARD = (
    "Last Night's NFL Results",
    "Last 7 Days NFL Results",
    "Season Performance",
    "Moneyline Accuracy by Model",
    "Overall Model Performance",
)


def nfl_chart_not_mlb_issues(chart_html: str) -> list[str]:
    """FAIL when NFL ?view=chart is not the MLB consensus table.

    The signed-off MLB chart is Moneyline | Spread | Totals plus the
    6/6 + all-but dissent table. Cards-board titles belong on Cards.
    Chart Last Night / Last 7 / Season must still show model tally cards.
    """
    html = chart_html or ""
    if not html:
        return ["NFL chart view did not load"]
    issues = six_model_chart_issues(html, "NFL")
    if "Consensus Based Betting Records" not in html:
        issues.append("NFL chart view missing Consensus Based Betting Records")
    for marker in _NFL_CHART_CARD_BOARD:
        if marker in html:
            issues.append(
                f"NFL chart view still shows {marker} (must be the MLB consensus table)"
            )
    return issues


def nfl_chart_window_tally_issues(chart_html: str) -> list[str]:
    """FAIL when NFL chart Last Night / Last 7 / Season have no model cards."""
    html = chart_html or ""
    if not html:
        return ["NFL chart view did not load"]
    issues: list[str] = []
    if not re.search(r"Last Night", html, flags=re.I):
        issues.append("NFL chart missing Last Night tally")
    if not re.search(r"Last 7|Past 7", html, flags=re.I):
        issues.append("NFL chart missing Last 7 tally")
    if not re.search(r">\s*Season\s*<", html, flags=re.I) and "Season " not in html:
        issues.append("NFL chart missing Season tally")
    n_cards = html.count("tally-card") + html.count("daily-tally-card")
    if n_cards < 6:
        issues.append(
            f"NFL chart Last Night / Last 7 / Season have {n_cards} model "
            "card(s) — MLB chart shows the 6-model rows"
        )
    return issues


def nfl_chart_api_issues(payload: dict | None) -> list[str]:
    """FAIL when /nfl/api/picks cannot hydrate Moneyline | Spread | Totals."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return ["NFL chart API /nfl/api/picks did not return ok"]
    issues: list[str] = []
    markets = payload.get("markets") or {}
    for key in ("moneyline", "spread", "totals"):
        market = markets.get(key) or {}
        if not market:
            issues.append(f"NFL chart API missing {key} market")
            continue
        tallies = market.get("tallies") or {}
        ln = (tallies.get("last_night") or {}).get("models") or {}
        l7 = (tallies.get("last_7") or {}).get("models") or {}
        if key == "moneyline" and len(ln) < 3:
            issues.append(
                f"NFL chart API moneyline Last Night has {len(ln)} model(s)"
            )
        if key == "moneyline" and len(l7) < 3:
            issues.append(
                f"NFL chart API moneyline Last 7 has {len(l7)} model(s)"
            )
    return issues


def nfl_chart_same_as_cards_issues(cards_html: str, chart_html: str) -> list[str]:
    """FAIL when NFL Cards and Chart are the same board."""
    cards_html = cards_html or ""
    chart_html = chart_html or ""
    if not cards_html or not chart_html:
        return ["NFL cards or chart view did not load"]
    if cards_html == chart_html:
        return ["NFL results chart view looks the same as cards"]
    issues = nfl_chart_not_mlb_issues(chart_html)
    if issues:
        return issues
    if "nfl-results-chart" not in chart_html and re.search(
        r'class="[^"]*(?:week-section|date-section|game-card|daily-tally)',
        chart_html,
    ):
        return ["NFL chart view still shows the card board"]
    return []


def empty_last_night_spread_tally_issues(html: str) -> list[str]:
    """FAIL when Last Night moneyline graded but Spread/O/U say no data."""
    html = html or ""
    if "Last Night's NFL Results" not in html:
        return []
    issues: list[str] = []
    if "no spread data" in html:
        issues.append("Last Night Spread tally says no spread data")
    if "no O/U data" in html:
        issues.append("Last Night Over/Under tally says no O/U data")
    if "No graded picks in range" in html and "Model Performance" in html:
        issues.append("NFL Model Performance has no graded spread/total picks")
    return issues


def nfl_duplicate_h2h_issues(html: str) -> list[str]:
    """FAIL when H2H Last 10 is on the card face and again in details."""
    html = html or ""
    if "h2h-face-chip" not in html:
        return []
    cards = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(cards) < 2:
        cards = re.split(r'(?=<div\b[^>]*\bgame-card-stack\b)', html, flags=re.I)
    duped = 0
    for card in cards[1:]:
        if "h2h-face-chip" not in card:
            continue
        if re.search(r'class="sf-label">\s*H2H Last 10', card, flags=re.I):
            duped += 1
    if duped:
        return [
            f"{duped} NFL pick card(s) show H2H Last 10 on the face and again "
            "under details"
        ]
    return []


def nfl_preseason_results_issues(html: str) -> list[str]:
    """FAIL when NFL results still list August / preseason games."""
    html = html or ""
    if "nfl-results" not in html.lower() and "NFL Results" not in html:
        return []
    dates = set(re.findall(r'id="date-(20\d{2}-08-\d{2})"', html))
    dates.update(re.findall(r'data-date="(20\d{2}-08-\d{2})"', html))
    dates.update(re.findall(r'[?&]date=(20\d{2}-08-\d{2})', html))
    if dates:
        shown = ", ".join(sorted(dates)[:6])
        return [
            f"NFL results still include preseason date(s) {shown} "
            "— regular season only"
        ]
    return []


def nfl_spread_result_card_issues(html: str) -> list[str]:
    """FAIL when NFL results have a Spread tab but no ATS result cards."""
    html = html or ""
    if not re.search(r">\s*Spread\s*<", html):
        return []
    if "game-card" in html or "data-pick-card" in html or "nfl-ats-card" in html:
        return []
    if "Last Night's NFL Results" not in html and "Week by Week Results" not in html:
        return []
    return ["NFL Spread tab has no result cards showing whether the spread hit"]


def tennis_chart_same_as_cards_issues(cards_html: str, chart_html: str) -> list[str]:
    """FAIL when tennis Cards and Chart are the same page."""
    cards_html = cards_html or ""
    chart_html = chart_html or ""
    if not cards_html or not chart_html:
        return ["Tennis cards or chart view did not load"]
    if cards_html == chart_html:
        return ["Tennis results chart view looks the same as cards"]
    if ML_CHART not in chart_html and "cons-bar" not in chart_html:
        return ["Tennis chart view has no results chart"]
    return []


def results_math_issues(html: str, cards_html: str | None = None) -> list[str]:
    """2/4 chart vs cards, Past 7 dropping last night, empty spread/totals,
    blank G2/TD/Efficiency tallies, 0-0 last-night charts on a graded slate,
    and Last Night date mismatches across moneyline / spread / totals."""
    html = html or ""
    is_nfl = bool(
        re.search(
            r'rel=["\']canonical["\'][^>]+/nfl-results|Last Night\'s NFL Results|class="sport-nfl"',
            html,
            flags=re.I,
        )
    )
    issues = empty_market_chart_issues(html)
    issues.extend(blank_moneyline_model_issues(html))
    issues.extend(last_night_window_mismatch_issues(html))
    issues.extend(consensus_empty_vs_tally_issues(html))
    issues.extend(pl_vs_books_empty_vs_tally_issues(html))
    issues.extend(empty_last_night_spread_tally_issues(html))
    if is_nfl:
        issues.extend(nfl_missing_efficiency_issues(html))
        issues.extend(nfl_stale_season_perf_issues(html))
    if "Consensus Based Betting Records" not in html and "PL vs Sportsbook" not in html:
        return issues
    try:
        from team_results_charts import four_model_chart_mismatches
    except Exception:
        return issues
    issues.extend(four_model_chart_mismatches(html, cards_html))
    return issues


def efficiency_copied_na_issues(html: str) -> list[str]:
    """FAIL when Efficiency shows another model's % but the side is still N/A."""
    html = html or ""
    n = 0
    for box in re.findall(
        r'<div class="pc-box[^"]*">[\s\S]*?<div class="pc-name">\s*Efficiency\s*</div>'
        r'[\s\S]*?</div>\s*</div>',
        html,
        flags=re.I,
    ):
        val_m = re.search(r'class="pc-val"[^>]*>\s*([^<]+)', box, flags=re.I)
        side_m = re.search(r'class="pc-side"[^>]*>\s*([^<]+)', box, flags=re.I)
        val = (val_m.group(1) if val_m else "").strip()
        side = (side_m.group(1) if side_m else "").strip()
        if re.fullmatch(r"\d+(?:\.\d+)?%", val) and side.upper() == "N/A":
            n += 1
    if n:
        return [f"Efficiency shows a copied % with N/A side on {n} card(s)"]
    return []


def share_ad_card_issues(html: str, min_picks: int = 2) -> list[str]:
    """FAIL when the bottom advertising / share card is missing or has <2 picks."""
    html = html or ""
    if "social-export-wrap" not in html:
        return ["Picks page missing bottom share / advertising card"]
    m = re.search(r'data-share-picks="(\d+)"', html)
    if not m:
        return ["Share card does not report how many picks it drew"]
    n = int(m.group(1))
    if n < min_picks:
        return [f"Share card has {n} pick(s); need at least {min_picks}"]
    return []


def team_chart_sou_table_issues(html: str, market: str) -> list[str]:
    """FAIL when Spread/Totals still show the moneyline table or omit line compare."""
    html = html or ""
    market = (market or "").lower()
    if market not in ("spread", "totals"):
        return []
    issues: list[str] = []
    ssr = re.search(
        r'id="ssr-finals"[^>]*data-ssr-market="([^"]+)"', html, flags=re.I
    )
    ssr_m = (ssr.group(1) if ssr else "").lower()
    if ssr_m == "moneyline" or (
        "Moneyline games" in html and "Edge pick" in html and ssr_m != market
    ):
        issues.append(f"{market} chart still showing the moneyline Edge pick table")
    if not re.search(r">\s*Books?\s*<", html, flags=re.I):
        issues.append(f"{market} chart missing Books line column")
    if not re.search(r"Actual vs lines|Act \d", html, flags=re.I):
        issues.append(
            f"{market} chart does not compare actual score to books / PL lines"
        )
    return issues


def best_performing_width_issues(html: str) -> list[str]:
    """FAIL when Best Performing Model is not stretched to the tally row width."""
    html = html or ""
    if "Best Performing Model" not in html:
        return []
    if "ncaaf-chart-best-width" in html:
        return []
    if re.search(
        r"section\.pl-analytics\s+\.tally-grid\s*\{[^}]*width\s*:\s*100%",
        html,
        flags=re.I,
    ):
        return []
    return ["Best Performing Model box is not as wide as the other tally boxes"]


def ncaaf_chart_api_issues(payload: dict | None) -> list[str]:
    """FAIL when /ncaaf/api/picks cannot hydrate Moneyline | Spread | Totals."""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return ["NCAAF chart API /ncaaf/api/picks did not return ok"]
    issues: list[str] = []
    markets = payload.get("markets") or {}
    for key in ("moneyline", "spread", "totals"):
        if not (markets.get(key) or {}):
            issues.append(f"NCAAF chart API missing {key} market")
    return issues
