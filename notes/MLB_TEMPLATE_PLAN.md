# MLB → sport template plan (after production push)

MLB is the reference implementation for team-sport **predictions** and **results** pages.

## Frozen sources (do not edit for template work)

| Copy | Path |
|------|------|
| Archive | `Sports Sandbox/independent_sports/_archives/mlb_DONE_20260828/` |
| Premerge duplicate | `Sports Sandbox/mlb_DONE_premerge_20260828/` |

Snapshots: `mlb-picks.snapshot.html`, `mlb-results.snapshot.html` in each folder.

## Live merge (2026-08-28)

Merged into `predictionlabfix_work/`:

- `iso_hub/team_tabbed_results.py` — consensus, analytics insert, 3/6 ⓘ
- `mlb_ui_fixup.py` — picks/results card layout, chart Total EV strip
- `mlb_results_ui.py` — `fix_mlb_results_display` delegates to iso_hub
- `static/js/picks-chart.js`, `static/css/team-results.css`

## Template build (post-push)

Extract shared pieces into reusable templates/modules:

1. **Predictions** — card grid (3-up), pick-conf 6-up, Cards\|Chart + market tabs, no Total EV on totals chart
2. **Results** — daily tallies (3-col), Moneyline Accuracy analytics placement, consensus table, chart view (table-only, no duplicate cards)

Wire other team sports (WNBA, Soccer where applicable) to the same CSS/JS contracts without copying MLB-specific copy.
