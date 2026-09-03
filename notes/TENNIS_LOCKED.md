# Tennis LOCKED — signed off (:5052)

**Status:** Tennis isolation UI ported to `:5052` and locked **2026-09-02**.

**Do not modify** tennis UI unless the owner says **UNLOCK TENNIS**.

## Covered surfaces

- `/tennis-picks`, `/tennis-results` (cards and chart) on `:5052`
- `/tennis/share.jpg` share JPEG
- Isolation render: `iso_hub/tennis_page.py`, `tennis_live.py`
- Pick Confidence 3×2 grid (in `tennis-mlb-grid-fix`)
- Date picker + Cards|Chart placement (`iso_hub/mlb_page_template.py`)
- Tennis dissent-combination consensus (`iso_hub/tennis_consensus.py`)

## Agent rule

Leave tennis routes and tennis HTML/CSS alone. Next move is #3 → #1 only when owner asks. See `qa/TENNIS_SIGNED_OFF.txt`.
