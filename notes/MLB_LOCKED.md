# MLB LOCKED — signed off 2026-08-17

**Status:** MLB picks / results / PL spread fade signed off **2026-08-17**.

Product fade: `pick_spread_side()` takes raw favorite −1.5, then bets the **other side +1.5**. NO BET stays NO BET. One invert, cached once (`_mlb_spread_pick_mod`).

**Do not modify** MLB models, spread pick/grade, results tallies, or MLB UI unless the owner explicitly unlocks.

## Covered surfaces

- `mlb_spread_pick.py`
- `NHL77FINAL.py` spread pick/grade / `_mlb_spread_pick_mod` cache / MLB season pin
- `sports/MLB.py` spread fade wiring
- Related MLB results tallies only if owner unlocks

## Isolation

Isolation remains `~/Documents/Personal/mlb/`.

## Agent rule

No further MLB edits. Do not git push MLB again unless the owner confirms a production deploy.

Do not put this lock text on user-facing HTML.
