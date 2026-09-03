"""Tennis dissent-combination consensus table (signed-off :5081 look).

Used by iso_hub tennis pages on :5052. Do not change without UNLOCK TENNIS.
"""
from __future__ import annotations

import html as html_lib
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MODEL_ORDER = [
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
]


def _fold_agree_n(agree_n: int, *, panel: int = 6) -> int:
    n = int(agree_n or 0)
    panel_n = int(panel or 6)
    if panel_n < 2:
        panel_n = 6
    if n >= panel_n:
        return panel_n
    if n <= 0:
        return 0
    if n * 2 == panel_n:
        return n
    return max(n, panel_n - n)


def _format_dissent_names(dissent: tuple[str, ...] | list[str]) -> str:
    names = [str(m) for m in dissent if m]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _combo_label_html(
    *,
    agree_n: int,
    dissent: tuple[str, ...] | list[str] | None = None,
    panel: int = 6,
) -> str:
    panel_n = int(panel or 6)
    n = int(agree_n or 0)
    diss = tuple(dissent or ())
    if n >= panel_n or not diss:
        return html_lib.escape(f"{panel_n}/{panel_n} unanimous")
    return html_lib.escape(f"{n}/{panel_n} — all but " + _format_dissent_names(diss))


def _record_cell(items: list[dict[str, Any]], *, empty: str = "0-0") -> str:
    w = sum(1 for i in items if i.get("grade") == "WIN")
    l = sum(1 for i in items if i.get("grade") == "LOSS")
    p = sum(1 for i in items if i.get("grade") == "PUSH")
    if w + l == 0 and p == 0:
        return empty
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    decided = w + l
    if decided == 0:
        return rec
    pct = 100.0 * w / decided
    color = "#00C076" if pct >= 55 else ("#ca8a04" if pct >= 50 else "#D93025")
    width = max(4, min(100, int(round(pct))))
    return (
        f"{rec} <span style='color:{color};font-weight:700'>({pct:.0f}%)</span>"
        f"<div class='cons-bar' aria-hidden='true'><i style='width:{width}%;background:{color}'></i></div>"
    )


def _agreements_from_finals(finals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One graded row per match with majority/dissent model lists (even splits omitted)."""
    out: list[dict[str, Any]] = []
    for g in finals or []:
        models = g.get("models") or {}
        home = str(g.get("home_team_id") or g.get("home") or g.get("player_a") or "")
        away = str(g.get("away_team_id") or g.get("away") or g.get("player_b") or "")
        winner = str(g.get("winner") or "").strip()
        sides: dict[str, str] = {}
        for name in MODEL_ORDER:
            m = models.get(name) or {}
            pick = str(m.get("pick") or "").strip()
            if not pick or pick.lower() in ("n/a", "na", "—", "-", "–"):
                continue
            pl, hl, al = pick.lower(), home.lower(), away.lower()
            if hl and (pl == hl or pl in hl or hl in pl):
                sides[name] = "HOME"
            elif al and (pl == al or pl in al or al in pl):
                sides[name] = "AWAY"
            else:
                # already a side token?
                side = str(m.get("side") or "").strip().upper()
                if side in ("HOME", "AWAY"):
                    sides[name] = side
        if len(sides) < 6:
            continue
        counts = Counter(sides.values())
        if len(counts) >= 2 and counts.most_common(2)[0][1] == counts.most_common(2)[1][1]:
            continue  # even split omitted
        majority_side = counts.most_common(1)[0][0]
        agree_n = counts[majority_side]
        majority_models = [n for n in MODEL_ORDER if sides.get(n) == majority_side]
        dissent_models = [n for n in MODEL_ORDER if sides.get(n) and sides.get(n) != majority_side]
        pick_team = home if majority_side == "HOME" else away
        hs, aa = g.get("home_score"), g.get("away_score")
        try:
            hs_i = int(hs) if hs is not None else None
            aa_i = int(aa) if aa is not None else None
        except (TypeError, ValueError):
            hs_i = aa_i = None
        if hs_i is not None and aa_i is not None and hs_i != aa_i:
            win_team = home if hs_i > aa_i else away
            grade = "WIN" if pick_team.lower() == win_team.lower() else "LOSS"
        elif winner:
            grade = "WIN" if pick_team.lower() == winner.lower() else "LOSS"
        else:
            continue
        out.append(
            {
                "agree_n": agree_n,
                "is_unanimous": agree_n >= 6,
                "grade": grade,
                "game_date": str(g.get("game_date") or "")[:10],
                "majority_models": majority_models,
                "dissent_models": dissent_models,
            }
        )
    return out


def build_tennis_consensus_html(
    finals: list[dict[str, Any]],
    *,
    last_night_key: str | None = None,
) -> str:
    """Signed-off Consensus Based Betting Records (dissent combinations + bars)."""
    panel_n = 6
    min_majority = panel_n // 2 + 1
    folded_levels = tuple(range(panel_n, min_majority - 1, -1))
    agreements = _agreements_from_finals(finals)
    if not agreements:
        return ""
    now = datetime.now(ZoneInfo("America/New_York"))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    past_dates = sorted(
        {
            str(g.get("game_date") or "")[:10]
            for g in finals or []
            if str(g.get("game_date") or "")[:10] < today
        }
    )
    ln_key = last_night_key or (past_dates[-1] if past_dates else yesterday)
    cut7 = (now.date() - timedelta(days=7)).strftime("%Y-%m-%d")
    cut30 = (now.date() - timedelta(days=30)).strftime("%Y-%m-%d")

    def period(pred) -> list[dict[str, Any]]:
        return [a for a in agreements if pred(str(a.get("game_date") or "")[:10])]

    ln = period(lambda d: d == ln_key)
    d7 = period(lambda d: cut7 <= d < today)
    d30 = period(lambda d: cut30 <= d < today)

    def dissent_key(a: dict[str, Any]) -> tuple[str, ...]:
        present = {str(m) for m in (a.get("dissent_models") or []) if m}
        return tuple(name for name in MODEL_ORDER if name in present)

    def agree_bucket(a: dict[str, Any]) -> int | None:
        n = int(a.get("agree_n") or 0)
        folded = _fold_agree_n(n, panel=panel_n)
        if folded not in folded_levels:
            return None
        if folded == panel_n and not a.get("is_unanimous"):
            return None
        return folded

    combo_keys: dict[int, set[tuple[str, ...]]] = {lvl: set() for lvl in folded_levels}
    counts_30: dict[tuple[int, tuple[str, ...]], int] = {}
    for a in agreements:
        folded = agree_bucket(a)
        if folded is None:
            continue
        key = dissent_key(a)
        combo_keys[folded].add(key)
        dk = str(a.get("game_date") or "")[:10]
        if cut30 <= dk < today:
            counts_30[(folded, key)] = counts_30.get((folded, key), 0) + 1

    def filter_combo(
        items: list[dict[str, Any]], *, folded: int, key: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return [a for a in items if agree_bucket(a) == folded and dissent_key(a) == key]

    empty = "0-0"
    rows_html: list[str] = []
    for folded in folded_levels:
        keys = sorted(
            combo_keys.get(folded) or [],
            key=lambda k: (-counts_30.get((folded, k), 0), k),
        )
        if not keys:
            if folded == panel_n:
                rows_html.append(
                    "<tr>"
                    f'<td class="bucket">{_combo_label_html(agree_n=panel_n, panel=panel_n)}</td>'
                    f"<td>{empty}</td><td>{empty}</td><td>{empty}</td>"
                    "</tr>"
                )
            continue
        for key in keys:
            rows_html.append(
                "<tr>"
                f'<td class="bucket">{_combo_label_html(agree_n=folded, dissent=key, panel=panel_n)}</td>'
                f"<td>{_record_cell(filter_combo(ln, folded=folded, key=key), empty=empty)}</td>"
                f"<td>{_record_cell(filter_combo(d7, folded=folded, key=key), empty=empty)}</td>"
                f"<td>{_record_cell(filter_combo(d30, folded=folded, key=key), empty=empty)}</td>"
                "</tr>"
            )
    if not rows_html:
        return ""
    ln_hdr = f"Last night ({ln_key})" if ln_key else "Last night"
    sub = (
        f"Moneyline on the pregame majority among the {panel_n} live models. "
        "Each row is one dissent combination (model(s) that broke from the majority). "
        "0-0 means that combination had no graded games in the window. "
        "Even splits are omitted."
    )
    return f"""
    <div class="pl-consensus-records" id="pl-consensus-records">
      <h2>Consensus Based Betting Records</h2>
      <p class="sub">{html_lib.escape(sub)}</p>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Agreement</th>
              <th>{html_lib.escape(ln_hdr)}</th>
              <th>Past 7 days</th>
              <th>Past 30 days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
      <style>
        .pl-consensus-records{{background:#fff;border:1px solid rgba(15,23,42,.12);border-radius:14px;padding:18px;margin:16px 0 20px;max-width:1100px;margin-left:auto;margin-right:auto}}
        .pl-consensus-records h2{{margin:0 0 6px;font-size:1.15rem;color:#0f172a;text-align:center}}
        .pl-consensus-records .sub{{margin:0 0 14px;color:#64748b;font-size:.88rem;text-align:center;max-width:46rem;margin-left:auto;margin-right:auto}}
        .pl-consensus-records table{{width:100%;border-collapse:collapse;font-size:.9rem}}
        .pl-consensus-records th,.pl-consensus-records td{{padding:10px 8px;border-bottom:1px solid #e2e8f0;text-align:center}}
        .pl-consensus-records th{{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:#64748b}}
        .pl-consensus-records td.bucket{{text-align:left;font-weight:700;color:#0f172a}}
        .pl-consensus-records .cons-bar{{height:4px;background:#e2e8f0;border-radius:99px;margin:6px auto 0;max-width:7.5rem;overflow:hidden}}
        .pl-consensus-records .cons-bar i{{display:block;height:100%;border-radius:99px}}
      </style>
    </div>
    """
