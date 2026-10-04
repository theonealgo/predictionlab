"""Soccer competition catalog: ESPN slugs, numeric IDs, and region groups.

Owner 2026-09-15: active slate is 15 top domestic leagues only (not the full
ESPN dump). Display names for those leagues stay stable for DB/tests.
"""
from __future__ import annotations

import re

# (key, customer-facing label) — ESPN browse buckets, no vendor wording.
SOCCER_REGION_DEFS = (
    ('top', 'Top Competitions'),
    ('europe', 'Europe'),
    ('south-america', 'South America'),
    ('concacaf', 'USA & Canada'),
    ('live', 'Live'),
)

# Owner 2026-09-15: active soccer slate is these 15 domestic leagues only.
# name, espn_slug, numeric_id, regions, extra aliases
_LEAGUES: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ('English Premier League', 'eng.1', '700', ('top', 'europe'),
     ('premier league', 'epl', 'eng.1')),
    ('Italian Serie A', 'ita.1', '730', ('top', 'europe'),
     ('serie a', 'ita.1')),
    ('German Bundesliga', 'ger.1', '720', ('top', 'europe'),
     ('bundesliga', 'ger.1')),
    ('Spanish LaLiga', 'esp.1', '740', ('top', 'europe'),
     ('spanish laliga', 'laliga', 'la liga', 'esp.1')),
    ('French Ligue 1', 'fra.1', '710', ('top', 'europe'),
     ('ligue 1', 'fra.1')),
    ('Brazilian Serie A', 'bra.1', '630', ('top', 'south-america'),
     ('brasileirão', 'brasileirao', 'campeonato brasileiro', 'bra.1')),
    ('Portuguese Primeira Liga', 'por.1', '715', ('top', 'europe'),
     ('primeira liga', 'liga portugal', 'por.1')),
    ('Belgian Pro League', 'bel.1', '3901', ('top', 'europe'),
     ('bel.1',)),
    ('Major League Soccer', 'usa.1', '770', ('top', 'concacaf'),
     ('mls', 'usa.1')),
    ('EFL Championship', 'eng.2', '3914', ('top', 'europe'),
     ('english league championship', 'league championship', 'english championship',
      'eng.2')),
    ('Dutch Eredivisie', 'ned.1', '725', ('top', 'europe'),
     ('eredivisie', 'ned.1')),
    ('Turkish Super Lig', 'tur.1', '3946', ('top', 'europe'),
     ('süper lig', 'super lig', 'turkish süper lig', 'tur.1')),
    ('Austrian Bundesliga', 'aut.1', '3907', ('top', 'europe'),
     ('austrian football bundesliga', 'aut.1')),
    ('Scottish Premiership', 'sco.1', '735', ('top', 'europe'),
     ('sco.1',)),
    ('Swiss Super League', 'sui.1', '3944', ('top', 'europe'),
     ('swiss super league', 'super league', 'sui.1')),
)

# No legacy extra endpoints on the slim 15-league slate.
_EXTRA_ENDPOINTS = {}

# Region headings for the slim picker (no Asia / Africa / Internationals buckets).
ESPN_BROWSE_HEADINGS = (
    'Top Competitions',
    'USA & Canada',
    'Europe',
    'South America',
)

# ESPN browse / checker names — one per active catalog league.
ESPN_BROWSE_LEAGUES = (
    'English Premier League',
    'Italian Serie A',
    'German Bundesliga',
    'Spanish LaLiga',
    'French Ligue 1',
    'Brazilian Serie A',
    'Portuguese Primeira Liga',
    'Belgian Pro League',
    'MLS',
    'English League Championship',
    'Dutch Eredivisie',
    'Turkish Super Lig',
    'Austrian Bundesliga',
    'Scottish Premiership',
    'Swiss Super League',
)

SOCCER_LEAGUE_ORDER = [row[0] for row in _LEAGUES]

SOCCER_LEAGUE_ENDPOINTS = {row[0]: row[1] for row in _LEAGUES}
SOCCER_LEAGUE_ENDPOINTS.update(_EXTRA_ENDPOINTS)

SOCCER_LEAGUE_NUMERIC_IDS = {row[2]: row[0] for row in _LEAGUES if row[2]}

SOCCER_LEAGUE_REGIONS = {row[0]: row[3] for row in _LEAGUES}

SOCCER_REGION_LABELS = {key: label for key, label in SOCCER_REGION_DEFS}
SOCCER_REGION_ORDER = [key for key, _label in SOCCER_REGION_DEFS]

# Score sync walks the same 15 — never the old 100+ ESPN dump.
SOCCER_SCORE_UPDATE_LEAGUES = tuple(SOCCER_LEAGUE_ORDER)



def _build_canonical() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, slug, _lid, _regions, extras in _LEAGUES:
        out[name.strip().lower()] = name
        out[slug.strip().lower()] = name
        for alias in extras:
            if alias:
                out[alias.strip().lower()] = name
    return out


_SOCCER_LEAGUE_CANONICAL = _build_canonical()


def soccer_region_from_slug(slug: str | None):
    if not slug:
        return None
    key = slug.strip().lower()
    if key in SOCCER_REGION_LABELS:
        return key
    return None


def soccer_region_label(slug: str | None) -> str:
    key = soccer_region_from_slug(slug)
    if not key:
        return ''
    return SOCCER_REGION_LABELS[key]


def soccer_leagues_for_region(slug: str | None, live_names: list[str] | None = None) -> list[str]:
    key = soccer_region_from_slug(slug)
    if not key:
        return list(SOCCER_LEAGUE_ORDER)
    if key == 'live':
        allowed = {n for n in (live_names or []) if n}
        return [name for name in SOCCER_LEAGUE_ORDER if name in allowed]
    return [name for name in SOCCER_LEAGUE_ORDER if key in SOCCER_LEAGUE_REGIONS.get(name, ())]


def soccer_primary_region(league_name: str) -> str | None:
    regions = SOCCER_LEAGUE_REGIONS.get(league_name) or ()
    return regions[0] if regions else None


def soccer_league_in_region(
    league_name: str,
    region_slug: str | None,
    live_names: list[str] | None = None,
) -> bool:
    key = soccer_region_from_slug(region_slug)
    if not key:
        return True
    if key == 'live':
        if live_names is None:
            return True
        return league_name in set(live_names)
    return key in (SOCCER_LEAGUE_REGIONS.get(league_name) or ())


def _clean_league_option_label(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = (
        text.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&nbsp;", " ")
    )
    text = re.sub(r"\s*·\s*Live\s*$", "", text, flags=re.I)
    text = re.sub(r"^●\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def soccer_league_option_labels(html: str) -> list[str]:
    """Customer-facing league names from the soccer <select id=league>."""
    sel = re.search(
        r'<select[^>]*\bid=["\']league["\'][^>]*>([\s\S]*?)</select>',
        html or "",
        flags=re.I,
    )
    if not sel:
        return []
    out: list[str] = []
    for m in re.finditer(r"<option\b[^>]*>([\s\S]*?)</option>", sel.group(1), flags=re.I):
        label = _clean_league_option_label(m.group(1))
        if not label or label.lower() in ("all", "all leagues"):
            continue
        out.append(label)
    return out


def missing_espn_browse_leagues(html_or_labels) -> list[str]:
    """ESPN browse names whose catalog league is not in the dropdown."""
    if isinstance(html_or_labels, str):
        labels = soccer_league_option_labels(html_or_labels)
    else:
        labels = [str(x or "") for x in (html_or_labels or [])]
    mapped = set()
    for lab in labels:
        clean = _clean_league_option_label(lab)
        canon = _SOCCER_LEAGUE_CANONICAL.get(clean.lower())
        if canon:
            mapped.add(canon)
    missing: list[str] = []
    for espn_name in ESPN_BROWSE_LEAGUES:
        canon = _SOCCER_LEAGUE_CANONICAL.get(espn_name.lower())
        if not canon or canon not in mapped:
            missing.append(espn_name)
    return missing


def _site_league_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")


def espn_browse_league_pages() -> list[dict[str, str]]:
    """One picks URL + one results URL per ESPN soccer scoreboard league."""
    out: list[dict[str, str]] = []
    for espn_name in ESPN_BROWSE_LEAGUES:
        canon = _SOCCER_LEAGUE_CANONICAL.get(espn_name.lower()) or ""
        slug = _site_league_slug(canon) if canon else ""
        out.append(
            {
                "espn": espn_name,
                "catalog": canon,
                "slug": slug,
                "picks": f"/soccer-picks?league={slug}" if slug else "",
                "results": f"/soccer-results?league={slug}" if slug else "",
            }
        )
    return out


def league_page_selection_issues(
    html: str, *, slug: str, espn_name: str, catalog_name: str = ""
) -> list[str]:
    """FAIL reasons when a league URL is not that league's own page."""
    html = html or ""
    issues: list[str] = []
    if 'id="league-controls"' not in html or 'id="league"' not in html:
        issues.append(f"{espn_name}: page has no league dropdown")
        return issues
    sel = re.search(
        r'<select[^>]*\bid=["\']league["\'][^>]*>([\s\S]*?)</select>',
        html,
        flags=re.I,
    )
    body = sel.group(1) if sel else ""
    picked = re.search(
        rf'<option\b[^>]*\bvalue=["\']{re.escape(slug)}["\'][^>]*\bselected\b',
        body,
        flags=re.I,
    ) or re.search(
        rf'<option\b[^>]*\bselected\b[^>]*\bvalue=["\']{re.escape(slug)}["\']',
        body,
        flags=re.I,
    )
    if not picked:
        issues.append(f"{espn_name}: picks/results URL did not select this league")
    all_picked = re.search(
        r'<option\b[^>]*\bvalue=["\']["\'][^>]*\bselected\b|'
        r'<option\b[^>]*\bselected\b[^>]*\bvalue=["\']["\']',
        body,
        flags=re.I,
    )
    if all_picked and not picked:
        issues.append(f"{espn_name}: page fell back to All leagues")
    scope = ""
    sm = re.search(
        r'id=["\']soccer-showing-scope["\'][^>]*>([\s\S]*?)</p>',
        html,
        flags=re.I,
    )
    if sm:
        scope = _clean_league_option_label(sm.group(1)).lower()
    if scope and "all leagues" in scope and not picked:
        issues.append(f"{espn_name}: showing line is All leagues")
    return issues


def missing_in_season_green_leagues(html: str) -> list[str]:
    """In-season dropdown rows that are not painted green on the custom menu."""
    html = html or ""
    marked = []
    for m in re.finditer(
        r"<option\b([^>]*)>([\s\S]*?)</option>",
        html,
        flags=re.I,
    ):
        attrs, inner = m.group(1) or "", m.group(2) or ""
        if not re.search(r'\bdata-in-season=["\']1["\']', attrs, flags=re.I):
            continue
        label = _clean_league_option_label(inner)
        if not label or label.lower() in ("all", "all leagues"):
            continue
        marked.append(label)
    if not marked:
        return []
    green = set()
    for m in re.finditer(
        r'<button\b[^>]*class="[^"]*\bin-season\b[^"]*"[^>]*>([\s\S]*?)</button>',
        html,
        flags=re.I,
    ):
        lab = _clean_league_option_label(m.group(1))
        if lab:
            green.add(lab.lower())
    return [n for n in marked if n.lower() not in green]


def soccer_heading_labels(html: str) -> list[str]:
    """Continent / ESPN browse headings from the region select + optgroups."""
    html = html or ""
    labels: list[str] = []
    region = re.search(
        r'<select[^>]*\bid=["\']soccer-region["\'][^>]*>([\s\S]*?)</select>',
        html,
        flags=re.I,
    )
    if region:
        for m in re.finditer(
            r"<option\b[^>]*>([\s\S]*?)</option>", region.group(1), flags=re.I
        ):
            lab = _clean_league_option_label(m.group(1))
            if lab and lab.lower() not in ("all", "all continents"):
                labels.append(lab)
    for m in re.finditer(r'<optgroup\b[^>]*label="([^"]+)"', html, flags=re.I):
        lab = _clean_league_option_label(m.group(1).replace("&amp;", "&"))
        if lab:
            labels.append(lab)
    return labels


def missing_espn_browse_headings(html: str) -> list[str]:
    """Continent / heading labels ESPN shows that the picker dropped."""
    have = {lab.lower() for lab in soccer_heading_labels(html)}
    return [h for h in ESPN_BROWSE_HEADINGS if h.lower() not in have]


def soccer_espn_slug(league_name: str | None) -> str | None:
    """ESPN core/scoreboard slug for a catalog league name or alias."""
    if not league_name:
        return None
    key = str(league_name).strip()
    if not key:
        return None
    if key in SOCCER_LEAGUE_ENDPOINTS:
        return SOCCER_LEAGUE_ENDPOINTS[key]
    canon = _SOCCER_LEAGUE_CANONICAL.get(key.lower())
    if canon:
        return SOCCER_LEAGUE_ENDPOINTS.get(canon)
    return None
