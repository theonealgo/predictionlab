"""Team-sport card face: PL vs Books and Consensus Historical Record, nothing else."""
from __future__ import annotations

import importlib
import re

FACE_SPORTS = frozenset({"MLB", "NHL", "NBA", "WNBA", "NCAAB", "NCAAW", "NFL", "NCAAF", "CFL", "SOCCER"})
FACE_LABELS = ("Consensus Historical Record", "PL vs Books")

# (module, function, keyword args); a str value starting with "_" names a constant in that module.
_RESULTS_CHIPS = {
    "MLB": [("mlb_ui_fixup", "inject_mlb_consensus_and_pl_vs_books_chips", {})],
    "NHL": [("team_results_charts", "_inject_nhl_consensus_hist_chips", {})],
    "WNBA": [
        ("team_results_charts", "_inject_wnba_consensus_hist_chips", {}),
        ("team_results_charts", "_inject_wnba_pl_vs_books_chips", {}),
    ],
    "NFL": [("team_results_charts", "_inject_nfl_consensus_hist_chips", {})],
    "NCAAF": [("team_results_charts", "_inject_ncaaf_consensus_hist_chips", {})],
    "CFL": [
        ("team_results_charts", "_inject_cfl_consensus_hist_chips", {}),
        ("team_results_charts", "_inject_nhl_pl_vs_books_chips", {"sport": "CFL"}),
    ],
    "SOCCER": [
        ("team_results_charts", "_inject_soccer_consensus_hist_chips", {}),
        ("team_results_charts", "_inject_soccer_pl_vs_books_chips", {}),
    ],
}
for _s, _models in (("NBA", "_WNBA_CONSENSUS_MODELS"), ("NCAAB", "_NCAAF_CONSENSUS_MODELS"),
                    ("NCAAW", "_NCAAF_CONSENSUS_MODELS")):
    _RESULTS_CHIPS[_s] = [
        ("team_results_charts", "_inject_consensus_hist_chips", {"sport": _s, "models": _models}),
        ("team_results_charts", "_inject_nhl_pl_vs_books_chips", {"sport": _s}),
    ]

_STRIP_RE = re.compile(r'<div class="lines-strip"[^>]*>')
_CARD_RE = re.compile(r'<div class="game-card pick-card\b(?![^>]*data-pick-card)[^>]*>')


def _balanced_div(html: str, start: int) -> int:
    depth, j = 0, start
    for tag in re.finditer(r"<div\b|</div>", html[start:]):
        depth += 1 if tag.group(0) == "<div" else -1
        if depth == 0:
            return start + tag.end()
    return -1


def _label(chip: str) -> str:
    m = re.search(r'class="line-chip-label"[^>]*>([\s\S]*?)</div>', chip)
    text = re.sub(r"<button\b[\s\S]*?</button>", " ", m.group(1) if m else "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _strip_extra_chips(html: str) -> str:
    out, pos = [], 0
    for strip in _STRIP_RE.finditer(html):
        if strip.start() < pos:
            continue
        end = _balanced_div(html, strip.start())
        if end < 0:
            break
        inner_start = strip.end()
        inner_end = html.rfind("</div>", inner_start, end)
        inner = html[inner_start:inner_end]
        kept, i = [], 0
        for chip in re.finditer(r'<div class="line-chip\b', inner):
            if chip.start() < i:
                continue
            chip_end = _balanced_div(inner, chip.start())
            if chip_end < 0:
                break
            kept.append(inner[i:chip.start()])
            block = inner[chip.start():chip_end]
            if _label(block) in FACE_LABELS:
                kept.append(block)
            i = chip_end
        kept.append(inner[i:])
        out.append(html[pos:inner_start] + "".join(kept))
        pos = inner_end
    out.append(html[pos:])
    return "".join(out)


def _missing_face(html: str) -> bool:
    for strip in _STRIP_RE.finditer(html):
        end = _balanced_div(html, strip.start())
        labels = {_label(c) for c in re.findall(r'<div class="line-chip\b[\s\S]*?</div>\s*</div>', html[strip.start():end])}
        if not set(FACE_LABELS) <= labels:
            return True
    return False


def _add_results_chips(html: str, sport: str) -> str:
    html = _CARD_RE.sub(lambda m: m.group(0)[:-1] + " data-pick-card>", html)
    for mod_name, fn_name, kwargs in _RESULTS_CHIPS.get(sport, []):
        try:
            mod = importlib.import_module(mod_name)
            args = {k: getattr(mod, v) if isinstance(v, str) and v.startswith("_") else v for k, v in kwargs.items()}
            html = getattr(mod, fn_name)(html, **args) or html
        except Exception:
            continue
    return html


def _pattern_key(label: str) -> str:
    text = (label or "").lower()
    text = re.sub(r"consensus record:\s*", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.replace("—", " ").replace("–", " ").replace("-", " ")
    text = text.replace("/ no consensus", "")
    text = re.sub(r"[^a-z0-9/ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _past7_by_pattern(html: str) -> dict[str, str]:
    """Pattern -> Past 7 days W-L from the page's Consensus Based Betting Records table."""
    i = html.find("Consensus Based Betting Records")
    if i < 0:
        return {}
    j = html.find("</table>", i)
    out: dict[str, str] = {}
    for row in re.findall(r"<tr>([\s\S]*?)</tr>", html[i:j if j > i else i + 20000]):
        m = re.search(r'<td class="bucket">([^<]+)</td>', row)
        if not m:
            continue
        cells = re.findall(r"<td>([\s\S]*?)</td>", row[m.end():])
        if len(cells) < 2:
            continue
        rec = re.search(r"(\d+-\d+(?:-\d+)?)", re.sub(r"<[^>]+>", " ", cells[1]))
        if rec:
            out[_pattern_key(m.group(1))] = rec.group(1)
    return out


def _match_table_records(html: str) -> str:
    """A results card's Consensus Historical Record is the Past 7 days record of its pattern."""
    past7 = _past7_by_pattern(html)
    if not past7:
        return html

    def _fix(m: re.Match) -> str:
        val = m.group(2)
        rec = past7.get(_pattern_key(val))
        if not rec:
            return m.group(0)
        return m.group(1) + re.sub(r"\(\d+-\d+(?:-\d+)?\)\s*$", f"({rec})", val.rstrip()) + m.group(3)

    return re.sub(
        r'(Consensus Historical Record</div>\s*<div class="line-chip-val">)([^<]*\(\d+-\d+(?:-\d+)?\)\s*)(</div>)',
        _fix,
        html,
    )


def normalize_card_faces(html: str, sport: str, path: str) -> str:
    sport_u = (sport or "").upper()
    low = (path or "").rstrip("/").lower()
    if sport_u not in FACE_SPORTS or not html or "lines-strip" not in html:
        return html
    if low.endswith("-results"):
        if _missing_face(html):
            html = _add_results_chips(html, sport_u)
        html = _match_table_records(html)
    return _strip_extra_chips(html)
