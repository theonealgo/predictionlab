"""Apply CFL-only NHL77FINAL / team_results_charts hunks onto origin/main files."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CFL_LOGO_HELPER = '''
_CFL_TEAM_LOGOS = {
    'calgary stampeders': '/static/img/cfl/calgary.svg',
    'edmonton elks': '/static/img/cfl/edmonton.svg',
    'saskatchewan roughriders': '/static/img/cfl/saskatchewan.svg',
    'winnipeg blue bombers': '/static/img/cfl/winnipeg.svg',
    'bc lions': '/static/img/cfl/bc.svg',
    'hamilton tiger-cats': '/static/img/cfl/hamilton.svg',
    'toronto argonauts': '/static/img/cfl/toronto.svg',
    'montreal alouettes': '/static/img/cfl/montreal.svg',
    'ottawa redblacks': '/static/img/cfl/ottawa.svg',
    'ottawa red blacks': '/static/img/cfl/ottawa.svg',
}


def _cfl_team_logo_url(team_name: str) -> str:
    """Local CFL marks only — do not send CFL through ESPN team-logo slugs."""
    key = (team_name or '').strip().lower()
    if key in _CFL_TEAM_LOGOS:
        return _CFL_TEAM_LOGOS[key]
    for name, url in _CFL_TEAM_LOGOS.items():
        if key and (key in name or name.split()[-1] in key):
            return url
    return '/static/pl-logo.svg'


'''

CFL_LOGO_BRANCH = """    if sport == 'NCAAF':
        return _ncaaf_espn_logo_url(team_name)
    if sport == 'CFL':
        return _cfl_team_logo_url(team_name)
    slug = _TEAM_LOGO_SLUG.get(sport)
"""

CFL_PICK_CARD = (
    "{% endif %}{% endif %}{% if sport == 'CFL' %} data-pick-card "
    'data-home="{{ game.home }}" data-away="{{ game.away }}" data-date="{{ date }}"'
    "{% endif %} data-league="
)

CFL_INJECT = '''        elif re.search(r'<div class="(?:odds-pricing-section|card-details)\\b', stack):
            # Results cards (no face lines-strip) — insert before odds / details.
            strip = f'<div class="lines-strip">{chip}</div>\\n'
            stack = re.sub(
                r'(<div class="(?:odds-pricing-section|card-details)\\b)',
                strip + r"\\1",
                stack,
                count=1,
                flags=re.I,
            )
        elif sport_u == "CFL" and re.search(r'<div class="pick-conf-bar\\b', stack):
            strip = f'<div class="lines-strip">{chip}</div>\\n'
            stack = re.sub(
                r'(<div class="pick-conf-bar\\b)',
                strip + r"\\1",
                stack,
                count=1,
                flags=re.I,
            )
'''


def patch_nhl() -> None:
    path = ROOT / "NHL77FINAL.py"
    text = path.read_text(encoding="utf-8")
    if "_cfl_team_logo_url" not in text:
        needle = "def team_logo_url(sport: str, team_name: str) -> str:"
        if needle not in text:
            raise SystemExit("NHL77FINAL: team_logo_url missing")
        text = text.replace(needle, CFL_LOGO_HELPER + needle, 1)
    old = """    if sport == 'NCAAF':
        return _ncaaf_espn_logo_url(team_name)
    slug = _TEAM_LOGO_SLUG.get(sport)
"""
    if "return _cfl_team_logo_url(team_name)" not in text:
        if old not in text:
            raise SystemExit("NHL77FINAL: NCAAF logo branch missing")
        text = text.replace(old, CFL_LOGO_BRANCH, 1)
    if "{% if sport == 'CFL' %} data-pick-card" not in text:
        old_card = "{% endif %}{% endif %} data-league="
        if old_card not in text:
            raise SystemExit("NHL77FINAL: game-card endif missing")
        text = text.replace(old_card, CFL_PICK_CARD, 1)
    path.write_text(text, encoding="utf-8")
    print("patched NHL77FINAL.py")


def patch_trc() -> None:
    path = ROOT / "team_results_charts.py"
    text = path.read_text(encoding="utf-8")
    if 'sport_u == "CFL" and re.search(r\'<div class="pick-conf-bar' in text:
        print("team_results_charts.py already has CFL fallback")
        return
    if CFL_INJECT.strip().split("elif sport_u")[0] not in text:
        raise SystemExit("team_results_charts: odds-pricing insert missing")
    if '        out.append(stack)\n    return "".join(out)' not in text:
        raise SystemExit("team_results_charts: inject tail missing")
    # Insert CFL elif before out.append in _inject_consensus_hist_chips only
    old = '''                flags=re.I,
            )
        out.append(stack)
    return "".join(out)


def _inject_ncaaf_consensus_hist_chips'''
    new = '''                flags=re.I,
            )
        elif sport_u == "CFL" and re.search(r'<div class="pick-conf-bar\\b', stack):
            strip = f'<div class="lines-strip">{chip}</div>\\n'
            stack = re.sub(
                r'(<div class="pick-conf-bar\\b)',
                strip + r"\\1",
                stack,
                count=1,
                flags=re.I,
            )
        out.append(stack)
    return "".join(out)


def _inject_ncaaf_consensus_hist_chips'''
    if old not in text:
        raise SystemExit("team_results_charts: unique inject tail missing")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched team_results_charts.py")


if __name__ == "__main__":
    patch_nhl()
    patch_trc()
