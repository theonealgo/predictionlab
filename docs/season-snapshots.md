# Season result snapshots

Heavy results pages re-grade every completed game on each request. For finished regular seasons that is wasteful and can time out (NHL ~1,300 games). Snapshots freeze banner stats at season end; only active phases stay live.

## When to snapshot

| Phase | Page behavior |
|-------|----------------|
| Regular season in progress | Live DB grading for banner + last 30 days of game cards |
| Regular season complete | **Frozen JSON** for banner (ML, spread, O/U, model tallies) |
| Playoffs | Frozen regular-season banner + **live** playoff cards and playoff stats |
| Playoffs complete | Run snapshot for `playoffs` phase; both blocks frozen next year |

## End-of-season operations

### Regular season (e.g. NHL 2025-26, after Apr 30)

1. Ensure production DB has final scores and prediction columns populated.
2. Build snapshot (Render one-off job or locally against a DB copy):

   ```bash
   python scripts/build_season_snapshot.py --sport NHL --season 2025-26 --phase regular
   ```

   Snapshot builds set `PL_SNAPSHOT_BUILD=1` (full v2 ML + XGB spread/total). Do not use `PL_SKIP_V2_FOR_RESULTS` for snapshots.

3. Review `data/season_snapshots/NHL_2025-26_regular.json` (games_in_scope, ML/spread/O/U).
4. Commit the JSON (not `sports_predictions_original.db`), merge, deploy.
5. During playoffs the results route keeps the regular-season block from JSON and grades playoff games live only.

### After playoffs

1. `python scripts/build_season_snapshot.py --sport NHL --season 2025-26 --phase playoffs`
2. Commit `NHL_2025-26_playoffs.json`, deploy.
3. Until next October, NHL results are fully static (no DB recompute on page load).

### Next sport

Reuse the same JSON schema (`schema_version`, `sport`, `season`, `phase`, `overall_stats`, `spread_total_stats`, `season_perf`, `ou_summary`). Wire `sport_results` for that sport when its regular season ends.

## File layout

```
data/season_snapshots/
  NHL_2025-26_regular.json
  NHL_2025-26_playoffs.json   # after Stanley Cup
```

Built by `scripts/build_season_snapshot.py` using the same grading helpers as the results page (`_banner_daily_results_for_range`, `_compute_spread_total_for_daily`, etc.).
