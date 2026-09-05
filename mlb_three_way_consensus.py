"""MLB run-line / totals agreement: Books · Prediction Lab · XSharp.

Staging-only experiment. Do not merge into folder 1 until signed off on :5081 → :5052 → :5001.
Does not touch moneyline 6-model consensus.

Render is gated by audit_three_way_tables() — if invariants fail, HTML is withheld.
"""
from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Inclusive pair rows + partition rows (exactly 2 / exactly 1 / all 3 / all disagree).
_SPREAD_BUCKET_ORDER = (
    "PL = Books",
    "PL = XSharp",
    "Books = XSharp",
    "All 3 agree",
    "All 3 disagree",
    "2/3 — PL + Books",
    "2/3 — PL + XSharp",
    "2/3 — Books + XSharp",
    "1/3 — PL only",
    "1/3 — Books only",
    "1/3 — XSharp only",
)

_TOTALS_BUCKET_ORDER = (
    "3/3 — PL = XSharp",
    "2/3 — vs book line",
    "1/3 — PL only",
    "1/3 — XSharp only",
)


def _norm_token(raw: str) -> str:
    s = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    s = s.replace("−", "-").replace("–", "-")
    return s


def _spread_side_token(raw: str, home: str, away: str) -> str:
    """Map a run-line string to home|away (who is laying -1.5), else empty."""
    s = _norm_token(raw)
    if not s or s in {"—", "-", "n/a", "na", "pk", "pick'em", "pickem"}:
        return ""
    home_l = _norm_token(home)
    away_l = _norm_token(away)
    if home_l and home_l in s:
        return "home"
    if away_l and away_l in s:
        return "away"
    return ""


def _totals_side_token(raw: str, book_line: float | None) -> str:
    """Map a totals pick/projection to over|under vs the book line when possible.

    Bare numbers (projections) compare to book_line. Exact equality → no side.
    Explicit Over/Under strings win. A bare book line number alone is not a pick.
    """
    s = _norm_token(raw)
    if not s or s in {"—", "-", "n/a", "na"}:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        if book_line is None:
            return ""
        try:
            proj = float(s)
        except ValueError:
            return ""
        if proj > book_line:
            return "over"
        if proj < book_line:
            return "under"
        return ""
    if s.startswith("over") or re.match(r"^o\s*\d", s):
        return "over"
    if s.startswith("under") or re.match(r"^u\s*\d", s):
        return "under"
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m and book_line is not None:
        try:
            proj = float(m.group(1))
        except ValueError:
            return ""
        if proj > book_line:
            return "over"
        if proj < book_line:
            return "under"
    return ""


def _grade_spread(side: str, home: str, away: str, hs: int, aa: int) -> str:
    if side not in ("home", "away"):
        return ""
    if side == "home":
        margin = hs - aa
        return "WIN" if margin >= 2 else ("PUSH" if margin == 0 else "LOSS")
    margin = aa - hs
    return "WIN" if margin >= 2 else ("PUSH" if margin == 0 else "LOSS")


def _grade_totals(side: str, book_line: float | None, hs: int, aa: int) -> str:
    if side not in ("over", "under") or book_line is None:
        return ""
    total = hs + aa
    if total == book_line:
        return "PUSH"
    if side == "over":
        return "WIN" if total > book_line else "LOSS"
    return "WIN" if total < book_line else "LOSS"


def _extract_three(g: dict[str, Any], market: str) -> dict[str, Any] | None:
    home = str(g.get("home_team_id") or g.get("home") or "")
    away = str(g.get("away_team_id") or g.get("away") or "")
    try:
        hs = int(g.get("home_score"))
        aa = int(g.get("away_score"))
    except (TypeError, ValueError):
        return None
    dk = str(g.get("game_date") or "")[:10]
    if market == "spread":
        blk = g.get("spread") if isinstance(g.get("spread"), dict) else {}
        book_t = _spread_side_token(str(blk.get("book") or ""), home, away)
        pl_t = _spread_side_token(str(blk.get("pl_pick") or ""), home, away)
        xs_t = _spread_side_token(str(blk.get("xs_pick") or ""), home, away)
        if not book_t and not pl_t and not xs_t:
            return None
        return {
            "game_date": dk,
            "book": book_t,
            "pl": pl_t,
            "xs": xs_t,
            "grade_fn": lambda side: _grade_spread(side, home, away, hs, aa),
        }
    # totals — Books is the line number only (not a third Over/Under vote)
    blk = g.get("totals") if isinstance(g.get("totals"), dict) else {}
    book_line = blk.get("book_line")
    try:
        book_line_f = float(book_line) if book_line is not None else None
    except (TypeError, ValueError):
        book_line_f = None
    if book_line_f is None:
        m = re.search(r"(\d+(?:\.\d+)?)", str(blk.get("book") or ""))
        if m:
            try:
                book_line_f = float(m.group(1))
            except ValueError:
                book_line_f = None
    book_raw = str(blk.get("book") or "").strip()
    book_t = ""
    if book_raw and not re.fullmatch(r"\d+(?:\.\d+)?", book_raw.replace("−", "-")):
        book_t = _totals_side_token(book_raw, book_line_f)
    pl_t = _totals_side_token(str(blk.get("pick") or blk.get("line") or ""), book_line_f)
    if not pl_t:
        pl_t = _totals_side_token(str(blk.get("pl_pick") or ""), book_line_f)
    xs_t = _totals_side_token(str(blk.get("xs_pick") or ""), book_line_f)
    if not book_t and not pl_t and not xs_t:
        return None
    return {
        "game_date": dk,
        "book": book_t,
        "pl": pl_t,
        "xs": xs_t,
        "grade_fn": lambda side: _grade_totals(side, book_line_f, hs, aa),
    }


def _totals_bucket_keys(pl: str, xs: str) -> list[tuple[str, str]]:
    """Return (label, side_to_grade) for totals partitions.

    3/3 — both lean the same Over/Under side
    2/3 — exactly one leans; the other sits on the book line (no lean)
    1/3 — both lean opposite sides (each graded on its own side)
    """
    if pl and xs and pl == xs:
        return [("3/3 — PL = XSharp", pl)]
    if pl and xs and pl != xs:
        return [("1/3 — PL only", pl), ("1/3 — XSharp only", xs)]
    if pl and not xs:
        return [("2/3 — vs book line", pl)]
    if xs and not pl:
        return [("2/3 — vs book line", xs)]
    return []


def _spread_bucket_keys(book: str, pl: str, xs: str) -> list[tuple[str, str]]:
    """Return (label, side_to_grade) for run-line agreement rows."""
    out: list[tuple[str, str]] = []
    if pl and book and pl == book:
        out.append(("PL = Books", pl))
    if pl and xs and pl == xs:
        out.append(("PL = XSharp", pl))
    if book and xs and book == xs:
        out.append(("Books = XSharp", book))

    present = [(n, t) for n, t in (("pl", pl), ("book", book), ("xs", xs)) if t]
    if not present:
        return out
    sides = {t for _, t in present}
    lone = {
        "pl": ("1/3 — PL only", pl),
        "book": ("1/3 — Books only", book),
        "xs": ("1/3 — XSharp only", xs),
    }

    if len(present) == 3 and len(sides) == 1:
        out.append(("All 3 agree", pl or book or xs))
        return out

    # All three present and not unanimous → "All 3 disagree"
    # (on a two-sided market this is the split / non-unanimous case).
    if len(present) == 3 and len(sides) > 1:
        # Grade the Books side as the market reference for the disagree bucket.
        out.append(("All 3 disagree", book or pl or xs))

    if len(present) >= 2:
        if pl and book and pl == book and (not xs or xs != pl):
            out.append(("2/3 — PL + Books", pl))
        if pl and xs and pl == xs and (not book or book != pl):
            out.append(("2/3 — PL + XSharp", pl))
        if book and xs and book == xs and (not pl or pl != book):
            out.append(("2/3 — Books + XSharp", book))

    if len(present) == 1:
        out.append(lone[present[0][0]])
    elif len(present) == 3 and len(sides) == 2:
        maj = Counter(t for _, t in present).most_common(1)[0][0]
        for name, t in present:
            if t != maj:
                out.append(lone[name])
    elif len(present) == 2 and len(sides) == 2:
        for name, _t in present:
            out.append(lone[name])
    elif len(sides) == 3:
        for name, _t in present:
            out.append(lone[name])
    return out


def _agreements(finals: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in finals or []:
        triple = _extract_three(g, market)
        if not triple:
            continue
        book, pl, xs = triple["book"], triple["pl"], triple["xs"]
        grade_fn = triple["grade_fn"]
        labeled = (
            _totals_bucket_keys(pl, xs)
            if market == "totals"
            else _spread_bucket_keys(book, pl, xs)
        )
        for label, side in labeled:
            grade = grade_fn(side) if side else ""
            if grade not in ("WIN", "LOSS", "PUSH"):
                continue
            out.append(
                {
                    "label": label,
                    "grade": grade,
                    "game_date": triple["game_date"],
                    "book": book,
                    "pl": pl,
                    "xs": xs,
                }
            )
    return out


def _wl(items: list[dict[str, Any]]) -> tuple[int, int, int, float | None]:
    w = sum(1 for i in items if i.get("grade") == "WIN")
    l = sum(1 for i in items if i.get("grade") == "LOSS")
    p = sum(1 for i in items if i.get("grade") == "PUSH")
    decided = w + l
    pct = (100.0 * w / decided) if decided else None
    return w, l, p, pct


def _cell(items: list[dict[str, Any]], *, show_zero: bool = False) -> str:
    w, l, p, pct = _wl(items)
    if w + l == 0 and p == 0:
        return "0-0" if show_zero else "—"
    if w + l == 0 and p:
        return f"0-0-{p} <span style='color:#64748b'>(push)</span>"
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    if pct is None:
        return rec
    color = "#00C076" if pct >= 55 else ("#ca8a04" if pct >= 50 else "#D93025")
    width = max(4, min(100, int(round(pct))))
    return (
        f"{rec} <span style='color:{color};font-weight:700'>({pct:.0f}%)</span>"
        f"<div class='cons-bar' aria-hidden='true'><i style='width:{width}%;background:{color}'></i></div>"
    )


def _window_maps(
    rows: list[dict[str, Any]],
    *,
    ln_key: str,
    cut7: str,
    cut30: str,
    today: str,
    labels: tuple[str, ...],
) -> tuple[dict[str, list], dict[str, list], dict[str, list]]:
    def empty() -> dict[str, list]:
        return {lab: [] for lab in labels}

    ln_b, d7_b, d30_b = empty(), empty(), empty()
    for a in rows:
        lab = a.get("label")
        if lab not in ln_b:
            continue
        d = str(a.get("game_date") or "")[:10]
        if d == ln_key:
            ln_b[lab].append(a)
        if cut7 <= d < today:
            d7_b[lab].append(a)
        if cut30 <= d < today:
            d30_b[lab].append(a)
    return ln_b, d7_b, d30_b


def audit_three_way_tables(
    finals: list[dict[str, Any]],
    *,
    last_night_key: str | None = None,
) -> dict[str, Any]:
    """Pre-render accuracy checker. Returns {ok, errors, warnings, stats}.

    Invariants (must hold or HTML is withheld):
    - Every extracted game lands in exactly one totals partition (3/3 | 2/3 | 1/3*).
    - Totals 3/3 + 2/3 + opposite-pair games == graded totals games with a lean.
    - Spread All 3 agree + All 3 disagree == games with all three sides present.
    - Spread All 3 agree ⊆ PL=Books ∩ PL=XSharp ∩ Books=XSharp (same W-L).
    - No negative W/L; every graded row has WIN|LOSS|PUSH.
    """
    errors: list[str] = []
    warnings: list[str] = []
    now = datetime.now(ZoneInfo("America/New_York"))
    today = now.strftime("%Y-%m-%d")

    # --- totals partition coverage ---
    tot_part = Counter()
    tot_games = 0
    for g in finals or []:
        t = _extract_three(g, "totals")
        if not t:
            continue
        pl, xs = t["pl"], t["xs"]
        if not pl and not xs:
            continue
        tot_games += 1
        keys = [lab for lab, _side in _totals_bucket_keys(pl, xs)]
        if pl and xs and pl == xs:
            tot_part["3/3"] += 1
            if keys != ["3/3 — PL = XSharp"]:
                errors.append(f"totals agree mis-bucketed: {keys}")
        elif pl and xs and pl != xs:
            tot_part["1/3"] += 1
            if set(keys) != {"1/3 — PL only", "1/3 — XSharp only"}:
                errors.append(f"totals opposite mis-bucketed: {keys}")
        elif pl or xs:
            tot_part["2/3"] += 1
            if keys != ["2/3 — vs book line"]:
                errors.append(f"totals one-lean mis-bucketed: {keys}")
        # Grade sanity
        for lab, side in _totals_bucket_keys(pl, xs):
            gr = t["grade_fn"](side)
            if gr not in ("WIN", "LOSS", "PUSH"):
                errors.append(f"totals ungradable {lab} side={side!r} date={t['game_date']}")

    if tot_part["3/3"] + tot_part["2/3"] + tot_part["1/3"] != tot_games:
        errors.append(
            f"totals partition sum {tot_part['3/3']+tot_part['2/3']+tot_part['1/3']} "
            f"!= games {tot_games}"
        )

    # --- spread unanimous vs disagree ---
    sp_all3 = sp_disagree = sp_triple = 0
    for g in finals or []:
        t = _extract_three(g, "spread")
        if not t:
            continue
        b, p, x = t["book"], t["pl"], t["xs"]
        if not (b and p and x):
            continue
        sp_triple += 1
        if b == p == x:
            sp_all3 += 1
        else:
            sp_disagree += 1
        labels = [lab for lab, _ in _spread_bucket_keys(b, p, x)]
        if b == p == x:
            if "All 3 agree" not in labels:
                errors.append(f"spread unanimous missing All 3 agree {t['game_date']}")
            if "All 3 disagree" in labels:
                errors.append(f"spread unanimous also tagged disagree {t['game_date']}")
        else:
            if "All 3 disagree" not in labels:
                errors.append(f"spread split missing All 3 disagree {t['game_date']}")
            if "All 3 agree" in labels:
                errors.append(f"spread split also tagged agree {t['game_date']}")

    if sp_all3 + sp_disagree != sp_triple:
        errors.append(f"spread agree+disagree {sp_all3}+{sp_disagree} != triple {sp_triple}")

    # Inclusive pairs must match All 3 agree W-L on last-night window when unanimous
    rows_sp = _agreements(finals, "spread")
    dates = sorted(
        {
            str(a.get("game_date") or "")[:10]
            for a in rows_sp
            if str(a.get("game_date") or "")[:10] < today
        }
    )
    ln_key = last_night_key or (dates[-1] if dates else (now - timedelta(days=1)).strftime("%Y-%m-%d"))

    def wl_for(label: str, date_pred) -> tuple[int, int, int]:
        items = [
            a
            for a in rows_sp
            if a.get("label") == label and date_pred(str(a.get("game_date") or "")[:10])
        ]
        w, l, p, _ = _wl(items)
        return w, l, p

    for win_name, pred in (
        ("last_night", lambda d: d == ln_key),
        ("past7", lambda d: (now.date() - timedelta(days=7)).strftime("%Y-%m-%d") <= d < today),
    ):
        a = wl_for("All 3 agree", pred)
        pb = wl_for("PL = Books", pred)
        px = wl_for("PL = XSharp", pred)
        bx = wl_for("Books = XSharp", pred)
        dis = wl_for("All 3 disagree", pred)
        # Inclusive pairs cover All-3 plus exact-2/3, so they must be ≥ All 3 agree.
        a_n = a[0] + a[1] + a[2]
        if a_n > pb[0] + pb[1] + pb[2]:
            errors.append(
                f"spread {win_name}: All 3 agree n={a_n} > PL=Books n={pb[0]+pb[1]+pb[2]}"
            )
        if a_n > bx[0] + bx[1] + bx[2]:
            errors.append(
                f"spread {win_name}: All 3 agree n={a_n} > Books=XSharp n={bx[0]+bx[1]+bx[2]}"
            )
        if a_n > px[0] + px[1] + px[2]:
            errors.append(
                f"spread {win_name}: All 3 agree n={a_n} > PL=XSharp n={px[0]+px[1]+px[2]}"
            )
        # Agree + disagree partition of triple-present games (by decided+push count)
        # Only enforce on last_night where we can also recount from finals.
        if win_name == "last_night":
            agree_n = a_n
            dis_n = dis[0] + dis[1] + dis[2]
            ln_triple = sum(
                1
                for g in finals or []
                if str(g.get("game_date") or "")[:10] == ln_key
                and (_extract_three(g, "spread") or {}).get("book")
                and (_extract_three(g, "spread") or {}).get("pl")
                and (_extract_three(g, "spread") or {}).get("xs")
            )
            # Re-extract once
            ln_triple = 0
            for g in finals or []:
                if str(g.get("game_date") or "")[:10] != ln_key:
                    continue
                t = _extract_three(g, "spread")
                if t and t["book"] and t["pl"] and t["xs"]:
                    ln_triple += 1
            if agree_n + dis_n != ln_triple and ln_triple:
                errors.append(
                    f"spread last_night: agree({agree_n})+disagree({dis_n}) != triple({ln_triple})"
                )

    rows_tot = _agreements(finals, "totals")
    if tot_games and not rows_tot:
        errors.append("totals produced zero graded agreement rows")
    if sp_triple and not rows_sp:
        errors.append("spread produced zero graded agreement rows")

    # Required labels must be representable in the HTML order lists
    for row in rows_tot:
        lab = row.get("label")
        if lab not in _TOTALS_BUCKET_ORDER:
            errors.append(f"totals unknown label {lab!r}")
    for row in rows_sp:
        lab = row.get("label")
        if lab not in _SPREAD_BUCKET_ORDER:
            errors.append(f"spread unknown label {lab!r}")

    stats = {
        "totals_games": tot_games,
        "totals_3_3": tot_part["3/3"],
        "totals_2_3": tot_part["2/3"],
        "totals_1_3": tot_part["1/3"],
        "spread_triple": sp_triple,
        "spread_all3_agree": sp_all3,
        "spread_all3_disagree": sp_disagree,
        "last_night_key": ln_key,
    }
    if tot_part["3/3"] == 0 and tot_games > 10:
        warnings.append("totals 3/3 is empty despite many games — check extraction")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def build_three_way_records_html(
    finals: list[dict[str, Any]],
    market: str,
    *,
    last_night_key: str | None = None,
    skip_audit: bool = False,
) -> str:
    """HTML table for Books/PL/XSharp agreement on spread or totals.

    Runs audit_three_way_tables first (unless skip_audit). On failure returns ""
    so a bad table is never injected into the page.
    """
    market = "spread" if market == "spread" else "totals"
    if not skip_audit:
        audit = audit_three_way_tables(finals, last_night_key=last_night_key)
        if not audit["ok"]:
            print(
                f"[mlb_three_way] AUDIT FAIL ({market}) — withholding table: "
                + "; ".join(audit["errors"][:8]),
                flush=True,
            )
            return ""
        for w in audit.get("warnings") or []:
            print(f"[mlb_three_way] AUDIT WARN ({market}): {w}", flush=True)

    rows = _agreements(finals, market)
    if not rows and market == "spread":
        return ""
    # Totals still render the scaffold (0-0 cells) so the page never looks "missing"
    now = datetime.now(ZoneInfo("America/New_York"))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    past_dates = sorted(
        {
            str(a.get("game_date") or "")[:10]
            for a in rows
            if str(a.get("game_date") or "")[:10] < today
        }
    )
    ln_key = last_night_key or (past_dates[-1] if past_dates else yesterday)
    cut7 = (now.date() - timedelta(days=7)).strftime("%Y-%m-%d")
    cut30 = (now.date() - timedelta(days=30)).strftime("%Y-%m-%d")

    labels = _TOTALS_BUCKET_ORDER if market == "totals" else _SPREAD_BUCKET_ORDER
    ln_b, d7_b, d30_b = _window_maps(
        rows, ln_key=ln_key, cut7=cut7, cut30=cut30, today=today, labels=labels
    )

    # Spread: omit rows that are empty in every window (except always keep All 3 disagree)
    if market == "spread":
        always = {"All 3 agree", "All 3 disagree", "PL = Books", "PL = XSharp", "Books = XSharp"}
        show_labels = [
            lab
            for lab in labels
            if lab in always or ln_b.get(lab) or d7_b.get(lab) or d30_b.get(lab)
        ]
        show_zero = False
    else:
        show_labels = list(labels)  # always show all four totals rows
        show_zero = True

    body = []
    for label in show_labels:
        body.append(
            "<tr>"
            f'<td class="bucket">{html.escape(label)}</td>'
            f"<td>{_cell(ln_b.get(label, []), show_zero=show_zero)}</td>"
            f"<td>{_cell(d7_b.get(label, []), show_zero=show_zero)}</td>"
            f"<td>{_cell(d30_b.get(label, []), show_zero=show_zero)}</td>"
            "</tr>"
        )

    if market == "totals":
        title = "Prediction Lab · XSharp — Totals (vs book line)"
        sub_html = (
            "<p class=\"sub\">Prediction Lab (PL) and XSharp are compared against the "
            "book’s Over/Under line.</p>"
            "<ul class=\"sub-list\">"
            "<li><strong>3/3</strong> = PL and XSharp agree with the same side of the book line</li>"
            "<li><strong>2/3</strong> = only one leans; the other sits on the book line "
            "(line counted as the third outcome)</li>"
            "<li><strong>1/3</strong> = opposite disagreement (each graded on its own side)</li>"
            "</ul>"
            "<ul class=\"sub-list\">"
            "<li>PL = Prediction Lab’s published Over/Under pick</li>"
            "<li>XSharp = Over if its projected total is above the book line; Under if below</li>"
            "<li>Books = the book’s total line only — it is not a third pick</li>"
            "</ul>"
            "<p class=\"sub\">0-0 = none in that window; 0-0-N = pushes only.</p>"
        )
    else:
        title = "Books · Prediction Lab · XSharp — Run Line"
        sub_html = (
            "<p class=\"sub\">Books / Prediction Lab / XSharp each pick a run-line side (−1.5). "
            "Pair rows are inclusive. Exact 2/3 = those two agree and the third differs; "
            "exact 1/3 = the lone disagreeing voice. "
            "<strong>All 3 disagree</strong> = all three present and not unanimous "
            "(graded on the Books side). "
            "Table is withheld if the pre-render accuracy checker fails.</p>"
        )

    ln_hdr = f"Last night ({ln_key})" if ln_key else "Last night"
    return f"""
    <div class="pl-consensus-records pl-three-way-records" id="pl-{'spread' if market=='spread' else 'totals'}-three-way">
      <h2>{html.escape(title)}</h2>
      {sub_html}
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th style="text-align:left">Agreement</th>
              <th>{html.escape(ln_hdr)}</th>
              <th>Past 7 days</th>
              <th>Past 30 days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(body)}
          </tbody>
        </table>
      </div>
      <style>
        .pl-three-way-records .cons-bar{{height:4px;background:#e2e8f0;border-radius:99px;margin:6px auto 0;max-width:7.5rem;overflow:hidden}}
        .pl-three-way-records .cons-bar i{{display:block;height:100%;border-radius:99px}}
        .pl-three-way-records .sub-list{{margin:6px 0 10px;padding-left:1.2rem;color:#64748b;font-size:.82rem;line-height:1.45}}
        .pl-three-way-records .sub-list li{{margin:2px 0}}
      </style>
    </div>
    """
