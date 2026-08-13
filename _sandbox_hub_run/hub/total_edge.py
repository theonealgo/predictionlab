#!/usr/bin/env python3
"""Total Edge = Predicted Total − Sportsbook Total (sandbox / offline only).

Lean thresholds (match owner examples: +3 Strong Over, +2/+1.5 Over, −3 Strong Under, −2 Under):
  Strong Over:  edge >= +2.5
  Over:         edge >= +1.0
  Strong Under: edge <= -2.5
  Under:        edge <= -1.0
  else:         "—" (neutral / |edge| < 1)

Missing book or predicted total → N/A cells; lean N/A when either side missing.
Never invent book lines.
"""
from __future__ import annotations

import html as html_lib
import re
from typing import Any

# Team sports with game totals on picks cards. Skip UFC / Tennis / Golf.
TOTAL_EDGE_SPORTS: dict[str, dict[str, str]] = {
    "mlb": {"label": "MLB", "picks_path": "/mlb-picks"},
    "soccer": {"label": "Soccer", "picks_path": "/soccer-picks"},
    "wnba": {"label": "WNBA", "picks_path": "/wnba-picks"},
    # CFL: Total Edge removed (no books feed — edge vs book is meaningless).
}

LEAN_DOC = (
    "Strong Over ≥ +2.5 · Over ≥ +1.0 · Under ≤ −1.0 · Strong Under ≤ −2.5 · else —"
)

_STACK_OPEN_RE = re.compile(
    r'<div\s+class="game-card-stack"[^>]*>',
    re.I,
)
_ATTR_RE = re.compile(r'([\w:-]+)="([^"]*)"', re.I)
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_PROJ_SPLIT_RE = re.compile(r"\s*[–—−\-]\s*")
_TOTAL_ROW_RE = re.compile(
    r'<td\s+class="market-k">\s*Total\s*</td>\s*'
    r'<td\s+class="val-books">\s*([^<]*?)\s*</td>\s*'
    r'<td\s+class="val-pl">\s*([^<]*?)\s*</td>',
    re.I,
)
_BOOKS_CHIP_RE = re.compile(
    r'line-chip-label">\s*Books\s+total\s*</div>\s*'
    r'<div\s+class="line-chip-val[^"]*">\s*([^<]*?)\s*</div>',
    re.I,
)


def lean_for_edge(edge: float | None) -> str:
    if edge is None:
        return "N/A"
    if edge >= 2.5:
        return "Strong Over"
    if edge >= 1.0:
        return "Over"
    if edge <= -2.5:
        return "Strong Under"
    if edge <= -1.0:
        return "Under"
    return "—"


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = html_lib.unescape(str(raw)).strip()
    if not text or text in {"—", "–", "-", "N/A", "n/a", "&mdash;"}:
        return None
    # "O/U 8.5" / "8.5"
    m = _NUM_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def predicted_from_pl_proj(raw: str | None) -> float | None:
    """Sum away–home projected scores from data-pl-proj (e.g. '4.5–5.0' → 9.5)."""
    if not raw:
        return None
    text = html_lib.unescape(str(raw)).strip()
    if not text or text in {"—", "–", "-", "N/A"}:
        return None
    parts = [p for p in _PROJ_SPLIT_RE.split(text) if p.strip()]
    if len(parts) >= 2:
        a, b = _parse_float(parts[0]), _parse_float(parts[1])
        if a is not None and b is not None:
            return round(a + b, 2)
    return _parse_float(text)


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): html_lib.unescape(v) for k, v in _ATTR_RE.findall(tag)}


def _fmt_num(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val - round(val)) < 1e-9:
        return str(int(round(val)))
    return f"{val:.1f}".rstrip("0").rstrip(".") if "." in f"{val:.2f}" else f"{val:.1f}"


def _fmt_edge(edge: float | None) -> str:
    if edge is None:
        return "N/A"
    sign = "+" if edge > 0 else ""
    return f"{sign}{_fmt_num(edge)}"


def parse_total_edge_rows(html: str) -> list[dict[str, Any]]:
    """Parse live-parity picks HTML into Total Edge table rows."""
    if not html:
        return []
    rows: list[dict[str, Any]] = []
    opens = list(_STACK_OPEN_RE.finditer(html))
    for i, m in enumerate(opens):
        tag = m.group(0)
        start = m.end()
        end = opens[i + 1].start() if i + 1 < len(opens) else len(html)
        chunk = html[start:end]
        attrs = _attrs(tag)

        away = (attrs.get("data-away") or "").strip()
        home = (attrs.get("data-home") or "").strip()
        if not away and not home:
            continue
        game = f"{away} @ {home}" if away and home else (away or home)

        book = _parse_float(attrs.get("data-books-total"))
        if book is None:
            chip = _BOOKS_CHIP_RE.search(chunk)
            if chip:
                book = _parse_float(chip.group(1))

        # Prefer Odds table "Total" Prediction Lab cell (disp_pl_total) when present;
        # else sum projected team scores from data-pl-proj.
        predicted = None
        tr = _TOTAL_ROW_RE.search(chunk)
        if tr:
            if book is None:
                book = _parse_float(tr.group(1))
            predicted = _parse_float(tr.group(2))
        if predicted is None:
            predicted = predicted_from_pl_proj(attrs.get("data-pl-proj"))

        edge = None
        if predicted is not None and book is not None:
            edge = round(predicted - book, 2)

        lean = lean_for_edge(edge) if edge is not None else "N/A"
        rows.append(
            {
                "game": game,
                "away": away,
                "home": home,
                "predicted": predicted,
                "book": book,
                "edge": edge,
                "predicted_display": _fmt_num(predicted),
                "book_display": _fmt_num(book),
                "edge_display": _fmt_edge(edge),
                "lean": lean,
            }
        )
    return rows


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strongest absolute edge first; N/A edges last."""

    def key(r: dict[str, Any]) -> tuple:
        e = r.get("edge")
        if e is None:
            return (1, 0.0, r.get("game") or "")
        return (0, -abs(float(e)), r.get("game") or "")

    return sorted(rows, key=key)


def build_total_edge_fragment(
    label: str,
    rows: list[dict[str, Any]],
    *,
    empty_message: str | None = None,
    note: str | None = None,
) -> str:
    """HTML fragment for Total Edge table (injected into live chrome)."""
    body_rows = []
    for row in rows:
        edge = row.get("edge")
        lean = row.get("lean") or "—"
        if edge is None:
            edge_cls = "te-edge-na"
        elif edge > 0:
            edge_cls = "te-edge-pos"
        elif edge < 0:
            edge_cls = "te-edge-neg"
        else:
            edge_cls = "te-edge-na"
        if "Over" in str(lean):
            lean_cls = "te-lean-over"
        elif "Under" in str(lean):
            lean_cls = "te-lean-under"
        else:
            lean_cls = "te-lean-neutral"
        body_rows.append(
            "<tr>"
            f"<td>{html_lib.escape(str(row.get('game') or ''))}</td>"
            f"<td>{html_lib.escape(str(row.get('predicted_display') or 'N/A'))}</td>"
            f"<td>{html_lib.escape(str(row.get('book_display') or 'N/A'))}</td>"
            f"<td class='{edge_cls}'>{html_lib.escape(str(row.get('edge_display') or 'N/A'))}</td>"
            f"<td class='{lean_cls}'>{html_lib.escape(str(lean))}</td>"
            "</tr>"
        )
    if not body_rows:
        body_rows.append(
            f"<tr><td colspan='5'>{html_lib.escape(empty_message or 'No games with totals on the current slate.')}</td></tr>"
        )
    note_html = (
        f"<p class='page-sub' style='margin-top:14px;color:#64748b;font-size:.85rem'>{html_lib.escape(note)}</p>"
        if note
        else ""
    )
    return f"""
<style>
.te-edge-pos,.te-lean-over {{ color:#0f8f84; font-weight:700; }}
.te-edge-neg,.te-lean-under {{ color:#c23b22; font-weight:700; }}
.te-edge-na,.te-lean-neutral {{ color:#64748b; }}
</style>
<h1 id="pageHeading">{html_lib.escape(label)} Total Edge</h1>
<p class="page-sub" style="margin:0 0 12px;color:#64748b;max-width:46rem">
  Predicted total minus sportsbook total for today’s slate.
</p>
<p class="page-sub" style="margin:0 0 14px;color:#64748b;font-size:.88rem">
  <strong>Total Edge</strong> = Predicted Total − Book Total · {html_lib.escape(LEAN_DOC)}
</p>
<div style="overflow-x:auto;background:#fff;border:1px solid rgba(15,23,42,.1);border-radius:16px;padding:8px 12px;margin:12px 0">
<table style="width:100%;border-collapse:collapse;font-size:.92rem">
<thead><tr>
<th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0">Game</th>
<th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0">Predicted</th>
<th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0">Book</th>
<th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0">Total Edge</th>
<th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0">Lean</th>
</tr></thead>
<tbody>
{''.join(body_rows)}
</tbody></table></div>
{note_html}
"""


def render_total_edge_into_chrome(chrome_html: str, sport: str, fragment: str) -> str:
    """Replace picks slate container with Total Edge fragment; keep pl2 / research chrome."""
    if not chrome_html:
        return (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            f"<title>{sport.upper()} Total Edge | Prediction Lab</title>"
            '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
            "</head><body>"
            f'<div class="container">{fragment}</div></body></html>'
        )
    html = chrome_html
    title = f"{sport.upper() if sport != 'mlb' else 'MLB'} Total Edge | Prediction Lab"
    if sport == "soccer":
        title = "Soccer Total Edge | Prediction Lab"
    elif sport == "wnba":
        title = "WNBA Total Edge | Prediction Lab"
    html = re.sub(
        r"(<title>)(.*?)(</title>)",
        rf"\1{title}\3",
        html,
        count=1,
        flags=re.I | re.S,
    )
    # Replace first .container inner HTML (same approach as CFL)
    start = re.search(r'<div class="container\b[^"]*"[^>]*>', html, flags=re.I)
    if start:
        i = start.end()
        depth = 1
        j = i
        while j < len(html) and depth > 0:
            next_open = html.find("<div", j)
            next_close = html.find("</div>", j)
            if next_close < 0:
                break
            if next_open >= 0 and next_open < next_close:
                depth += 1
                j = next_open + 4
            else:
                depth -= 1
                if depth == 0:
                    html = html[:i] + "\n" + fragment + "\n" + html[next_close:]
                    break
                j = next_close + 6
    else:
        html = re.sub(
            r"(<main\b[^>]*>)",
            rf"\1<div class='container'>{fragment}</div>",
            html,
            count=1,
            flags=re.I,
        )
    # Purge leftover pick cards / grids outside replaced container
    html = re.sub(r'<div class="date-header\b[^"]*"[^>]*>[\s\S]*?</div>', "", html, flags=re.I)
    html = re.sub(
        r'<div class="games-grid\b[^"]*"[^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    return html
