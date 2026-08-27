# CFL LOCKED — signed off

**Status:** Owner signed off **2026-08-13**. Pick/results **HTML** stays frozen.

**Do not modify** `engine/render.py`, `engine/display_fade.py`, or CFL results/picks HTML unless the owner explicitly unlocks.

Canonical review surface: backup `http://127.0.0.1:5052/cfl-picks` and `/cfl-results`. Live clone wires the same engine via `predictionlabfix_work/cfl_live.py`.

Results use the MLB template. Do not invent CFL-only chrome. Fade is picks-only; totals never fade.

## Schedule source (unlocked 2026-08-21)

ESPN has **no CFL**. Slate + scores come from CFL.ca official `rounds.json` (`engine/fetch.py` → `cflscoreboard.cfl.ca/json/scoreboard/rounds.json`). Refresh:

```bash
python3 ~/Documents/Personal/cfl/scripts/sync_and_predict.py
```

Skip TBD playoff teams. Do not invent book MLs. Durable check notes (scores, Week 12, fade): `predictionlabfix_work/notes/CFL.md`.
