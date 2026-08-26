# MLB LOCKED — permanently hands-off after 2026-08-22 results correction

**Status:** MLB is **permanently LOCKED**.

Do **not** modify MLB again unless the owner explicitly says:

**UNLOCK MLB**

This applies to Moneyline (Grinder2, Takedown, Edge, XSharp, Sharp Consensus),
Spread / run-line, O/U, Efficiency, prediction generation, grading, aggregation,
ROI, historical queries, strategy IDs, and MLB-specific transformations.

Product fade (unchanged): `pick_spread_side()` takes raw favorite −1.5, then bets
the **other side +1.5**. NO BET stays NO BET. One invert, cached once
(`_mlb_spread_pick_mod`).

Efficiency ML on results uses the stored PL / H2H `our_spread` (favorite wins).
It must not use the faded run-line pick as the moneyline side.

Season O/U face % must be computed from the same W-L the banner shows
(`wins / (wins+losses)`). Do not keep a stale `total_pct` that disagrees
with 113-101 (that produced the 55.4% bug).

## Covered surfaces

- `mlb_spread_pick.py`
- `sports/MLB.py`
- `mlb_results_ui.py` / `mlb_ui_fixup.py`
- `sports/team_efficiency_attach.py` (MLB branch of `_efficiency_spread_for_grading`)
- `NHL77FINAL.py` MLB spread pick/grade, Efficiency results grade, last-7
  calendar window, `_pinned_market_side` / season pin, `_mlb_spread_pick_mod`

## Isolation

Isolation remains `~/Documents/Personal/mlb/`.

## Agent rule

No further MLB edits. Do not git push MLB unless the owner confirms a
production deploy.

Fixing or merging another sport is **not** an MLB unlock. Leave MLB files
out of those diffs.

Do not put this lock text on user-facing HTML.
