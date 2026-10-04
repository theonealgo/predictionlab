# Tennis LOCKED — signed off (:5052)

**Status:** Tennis isolation UI copied from `:5081` into `:5052` and locked **2026-09-03**.

**Do not modify** tennis UI unless the owner says **UNLOCK TENNIS**.

## Covered surfaces

- `/tennis-picks`, `/tennis-results` (cards and chart) on `:5052`
- `/tennis/share.jpg` share JPEG
- Copied render: `_sandbox_hub_run/hub/tennis_page.py`, `tennis_live.py`
- Pick Confidence 3×2 grid (`tennis-mlb-grid-fix`)
- Date picker + Cards|Chart placement (`_sandbox_hub_run/hub/mlb_page_template.py`)

## Agent rule

Leave tennis routes and tennis HTML/CSS alone. Port to live (#1) only when the owner asks. See `qa/TENNIS_SIGNED_OFF.txt`.
