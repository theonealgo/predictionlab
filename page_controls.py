"""Repair existing date/detail controls, including cached sport documents."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from html import escape

from html_blocks import blocks, remove_blocks

def picks_date_window(sport: str) -> tuple[int, int]:
    """Owner-approved windows; NFL was expanded before being re-locked."""
    if (sport or "").upper() == "NFL":
        return 14, 14
    if (sport or "").upper() == "NHL":
        return 7, 14
    if (sport or "").upper() == "CFL":
        # Weekly league: the next two weeks of games must show.
        return 1, 14
    if (sport or "").upper() == "NCAAF":
        return 1, 7
    if (sport or "").upper() == "SOCCER":
        return 2, 7
    return 1, 5


def _soccer_week_page() -> bool:
    """A soccer ?week= page is already limited to that week by the route."""
    try:
        from flask import has_request_context, request

        return has_request_context() and bool((request.args.get("week") or "").strip())
    except Exception:
        return False


_CONTROL_SCRIPT = r"""
<script id="pl-page-controls">
(function () {
  const picks = __IS_PICKS__;
  const originalShowDate = window.showDate;
  const today = __TODAY__;
  const days = __DAYS__;
  let selected = days.includes(today) ? today :
    (picks ? days.find(day => day >= today) : [...days].reverse().find(day => day <= today));
  if (!picks) {
    // Results open on the newest night that has finished games, never on an upcoming day.
    const hasFinal = day => {
      const s = document.getElementById('date-' + day);
      return !!s && /\bFINAL\b/.test(s.textContent || '');
    };
    const lastFinal = [...days].reverse().find(day => day <= today && hasFinal(day));
    if (lastFinal) selected = lastFinal;
  }
  selected = selected || days[days.length - 1];
  let start = Math.max(0, days.indexOf(selected) - 3);
  function sections() { return document.querySelectorAll('.date-section[id^="date-"]'); }
  function label(day) {
    if (typeof formatDate === 'function') return formatDate(day);
    if (typeof fmtDate === 'function') return fmtDate(day);
    return day;
  }
  function picker() {
    const sel = document.getElementById('datePicker');
    if (!sel) return;
    sel.replaceChildren();
    days.forEach(day => {
      const opt = document.createElement('option');
      opt.value = day;
      opt.textContent = label(day) + (day === today ? ' (Today)' : '');
      opt.selected = day === selected;
      sel.appendChild(opt);
    });
    sel.style.display = 'inline-block';
    sel.onchange = function () {
      start = Math.max(0, days.indexOf(sel.value) - 3);
      window.showDate(sel.value);
      render();
    };
  }
  function render() {
    const nav = document.getElementById('dateBubbles');
    if (!nav) return;
    nav.replaceChildren();
    days.slice(start, start + 7).forEach(day => {
      const bubble = document.createElement('div');
      bubble.className = 'date-bubble';
      bubble.classList.toggle('today', day === today);
      bubble.classList.toggle('active', day === selected);
      bubble.textContent = day === today ? 'Today' : label(day);
      bubble.title = day;
      bubble.onclick = function () { window.showDate(day); render(); };
      nav.appendChild(bubble);
    });
    picker();
  }
  if (days.length) {
    window.showDate = function (day) {
      if (!days.includes(day)) return;
      selected = day;
      if (picks && typeof originalShowDate === 'function') originalShowDate(day);
      sections().forEach(section => section.classList.toggle('visible', section.id === 'date-' + day));
      if (typeof activeDate !== 'undefined') activeDate = day;
      const sel = document.getElementById('datePicker');
      if (sel) sel.value = day;
    };
    window.renderDateBubbles = render;
    window.renderBubbles = render;
    window.renderDatePicker = picker;
    window.pickDateFromSelect = function (day) {
      start = Math.max(0, days.indexOf(day) - 3);
      window.showDate(day); render();
    };
    window.previousWeek = function () {
      start = Math.max(0, start - 7); render();
      if (!days.slice(start, start + 7).includes(selected)) window.showDate(days[start]);
    };
    window.nextWeek = function () {
      if (start + 7 < days.length) start += 7;
      render();
      if (!days.slice(start, start + 7).includes(selected)) window.showDate(days[start]);
    };
  }
  window.togglePickDetails = function (btn) {
    if (!btn) return;
    const panel = document.getElementById(btn.getAttribute('aria-controls'));
    if (!panel) return;
    const open = panel.hasAttribute('hidden') || getComputedStyle(panel).display === 'none';
    panel.hidden = !open;
    const card = btn.closest('.pick-card,.game-card,.game-card-stack');
    if (card) card.classList.toggle('is-expanded', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    const arrow = document.createElement('span');
    arrow.className = 'chevron'; arrow.textContent = '▾';
    btn.replaceChildren(document.createTextNode(open ? 'Less details ' : 'View Details '), arrow);
  };
  function init() {
    document.querySelectorAll('.view-details-btn[aria-controls]').forEach(btn => {
      const panel = document.getElementById(btn.getAttribute('aria-controls'));
      if (!panel) return;
      const card = btn.closest('.pick-card,.game-card,.game-card-stack');
      const open = !panel.hasAttribute('hidden');
      if (card) card.classList.toggle('is-expanded', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    if (days.length) { window.showDate(selected); render(); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
</script>
"""


def repair_page_controls(html: str, sport: str, path: str, view: str = "") -> str:
    if not html or "<html" not in html.lower() or not path.rstrip("/").endswith(("-picks", "-results")):
        return html
    from scoreboard_dates import _today

    today = _today()
    picks = path.rstrip("/").endswith("-picks")
    selected = blocks(html, lambda tag, attrs: (
        "date-section" in attrs.get("class", "").split() and attrs.get("id", "").startswith("date-")
    ) or attrs.get("id") in {"dateBubbles", "datePicker", "pl-page-controls"}
        or tag == "footer" and "site-directory-footer" in attrs.get("class", "").split())
    sections = [block for block in selected if "date-section" in block.attrs.get("class", "").split()]
    html = remove_blocks(html, [block for block in selected if block.attrs.get("id") == "pl-page-controls"])
    # UFC picks are a fixed fight-card snapshot; its past dates are the slate itself.
    first, last = "0000-00-00", "9999-99-99"
    soccer_week = (sport or "").upper() == "SOCCER" and _soccer_week_page()
    if picks and (sport or "").upper() != "UFC" and not soccer_week:
        past, future = picks_date_window(sport)
        first, last = (today - timedelta(days=past)).isoformat(), (today + timedelta(days=future)).isoformat()
        # Reparse if an earlier control script was removed, so offsets remain exact.
        sections = blocks(html, lambda tag, attrs: "date-section" in attrs.get("class", "").split()
                          and attrs.get("id", "").startswith("date-"))
        html = remove_blocks(html, [block for block in sections if not first <= block.attrs["id"][5:] <= last])
    sections = blocks(html, lambda tag, attrs: "date-section" in attrs.get("class", "").split()
                      and attrs.get("id", "").startswith("date-"))
    days = sorted({block.attrs["id"][5:] for block in sections})
    listed = re.search(r"\bconst allDates\s*=\s*(\[[^\]]*\])", html)
    if listed and sections:
        wanted = re.findall(r"20\d\d-\d\d-\d\d", listed.group(1))
        if picks:
            wanted = [day for day in wanted if first <= day <= last]
        missing = sorted(set(wanted) - set(days))
        if missing:
            kind = "predictions" if picks else "results"
            empty = "".join(
                f'<div class="date-section" id="date-{day}"><div class="date-header">{day}</div>'
                f'<p>N/A — no {kind} are available for this date.</p></div>' for day in missing
            )
            at = sections[-1].end
            html = html[:at] + empty + html[at:]
        days = sorted(set(days) | set(missing))
        html = re.sub(r"(\bconst allDates\s*=\s*)\[[^\]]*\]", lambda m: m.group(1) + json.dumps(days), html)
        default = today.isoformat() if today.isoformat() in days else next(
            (day for day in days if day >= today.isoformat()), days[-1]
        )
        html = re.sub(r"(const defaultPickDate\s*=\s*')[^']*(')", lambda m: m.group(1) + default + m.group(2), html)
    html = re.sub(r"(const today\s*=\s*')[^']*(')", lambda m: m.group(1) + today.isoformat() + m.group(2), html)
    controls = blocks(html, lambda tag, attrs: attrs.get("id") in {"dateBubbles", "datePicker"}
                      or tag == "footer" and "site-directory-footer" in attrs.get("class", "").split())
    footer = next((block for block in controls if block.tag == "footer"), None)
    if not sections:
        # A generated date control cannot select cards on a chart-only document.
        # Leave the chart's native range/market controls alone.
        html = remove_blocks(html, [block for block in controls if footer and block.start > footer.start
                                   and block.attrs.get("id") in {"dateBubbles", "datePicker"}])
        days = []
    else:
        bubbles = next((block for block in controls if block.attrs.get("id") == "dateBubbles"), None)
        picker = next((block for block in controls if block.attrs.get("id") == "datePicker"), None)
        if bubbles and (not picker or footer and picker.start > footer.start):
            if picker:
                html = remove_blocks(html, [picker])
            bubbles = blocks(html, lambda tag, attrs: attrs.get("id") == "dateBubbles")[0]
            options = "".join(f'<option value="{escape(day)}">{escape(day)}</option>' for day in days)
            html = html[:bubbles.end] + f'<select id="datePicker" aria-label="Jump to date">{options}</select>' + html[bubbles.end:]
    script = _CONTROL_SCRIPT.strip().replace("__IS_PICKS__", json.dumps(picks)).replace(
        "__TODAY__", json.dumps(today.isoformat())
    ).replace("__DAYS__", json.dumps(days))
    at = html.lower().rfind("</body>")
    if at >= 0:
        html = html[:at] + script + html[at:]
    return html
