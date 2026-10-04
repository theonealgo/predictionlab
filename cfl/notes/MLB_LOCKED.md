# MLB LOCKED — models (2026-08-22) and :5052 UI (2026-09-04)

**Status:** MLB is **LOCKED**. Models stay locked after the 2026-08-22 results
correction. The `:5052` picks/results UI (including both consensus charts) is
also locked as of 2026-09-04.

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

- `/mlb-picks`, `/mlb-results`, `/mlb-results?view=chart` on `:5052`
- `mlb_live.py`, `locked_pages/mlb/picks.html`, `results.html`, `results_chart.html`
- Both results consensus charts (moneyline + Books · Prediction Lab · XSharp)
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
out of those diffs. Production hotfixes (blog 500, affiliate 404, NFL/NCAAF
hangs, soccer, checker) must not edit MLB functions, snapshots, or `mlb_*`
files. Do not patch MLB blocks inside `NHL77FINAL.py` while fixing other routes.

Do not put this lock text on user-facing HTML.
