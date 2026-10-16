# 10-Day Ladder Picks

Page: `/ladder-picks` (alias `/ladder`). Code: `ladder_picks/`. Template:
`templates/ladder_picks.html`. Tests: `tests/test_ladder_picks.py`.

## How it runs

A background thread inside the web app runs one pass every
`LADDER_INTERVAL_MINUTES` (default 20). Each pass:

1. **Settle** the open day: final score from the `games` table first, then the
   event feed. Tie = push. Postponed / canceled = void. No final 72 h after the
   start = void. A loss ends the ladder; a win through Day 10 completes it.
2. **Expire** an active ladder only when `LADDER_MAX_CALENDAR_DAYS` is set and
   has run out (off by default).
3. **Select** today's pick (from 11:00 ET) when the previous day is settled.
   If nothing qualifies by 18:00 ET (or every candidate was checked and failed),
   the date is recorded as "no pick" and a running ladder pauses until the next
   qualifying pick. Ladder days count picks, not calendar days.
4. **Social**: queue and send posts (off unless enabled).

Every step is safe to repeat. A lock file stops two passes from running at
once, and the database refuses a second active ladder, a second pick for the
same day, or a second settlement.

By hand:

```
python -m ladder_picks.jobs            # one pass
python -m ladder_picks.jobs --status   # current ladder
python -m ladder_picks.jobs --dry-run  # today's best pick, nothing saved
python -m ladder_picks.jobs --types    # pick types that currently qualify
```

## Pick rules

Supported sports: NFL, NBA, MLB, NHL, NCAAF, NCAAB, NCAAW, WNBA. Candidates
are our moneyline (Consensus), spread (PL, XSharp) and total (PL, XSharp)
picks against the book line (`ladder_picks/markets.py`).

Every pick is judged by its **pick type**: sport + market + model + side
(favorite / underdog, over / under) + how far our number is from the book's
(cumulative buckets: 0 / 2 / 4 / 7 points, or 0 / 0.5 / 1 / 1.5 for NHL and
MLB; 50 / 55 / 60% for moneylines). A pick qualifies only when:

- it is a locked published prediction for today (ET), not started, at least
  30 minutes before the start;
- its pick type, over the last 365 days against the last book line stored
  before 16:00 UTC on game day, has at least 40 graded picks, a 60%+ hit rate,
  and 55%+ in both the older and newer half of those picks (pushes left out);
- the posted price at pick time is -150 to +150.

The best record wins (shrunk toward 50% by 10 picks so a small sample does not
beat a large one), then return, then earliest start. Spread / total prices are
not stored historically, so returns for those assume -110; moneyline returns
use the stored price. The pick, line, odds, record and wager are saved once
and never recalculated.

`python -m ladder_picks.jobs --types` lists the pick types that qualify today.

## Data

Separate SQLite file; existing tables are not changed.

- `LADDER_DB_PATH`, else `/data/ladder.db` when `/data` exists (Render disk),
  else `data/ladder.db`.
- Tables: `ladder_runs`, `ladder_days`, `ladder_no_pick_dates`,
  `ladder_social_posts`.
- Predictions / scores are read (read-only) from `LADDER_MAIN_DB_PATH`,
  `DATABASE_PATH`, or the app database.

## Settings (environment, all optional)

| Variable | Default |
|---|---|
| `LADDER_SCHEDULER` | `1` (set `0` to turn the thread off) |
| `LADDER_INTERVAL_MINUTES` | `20` (minimum 5) |
| `LADDER_START_CENTS` | `1000` |
| `LADDER_DAYS` | `10` |
| `LADDER_MAX_CALENDAR_DAYS` | `0` (no limit) |
| `LADDER_SELECT_HOUR` / `LADDER_NO_PICK_HOUR` | `11` / `18` (ET) |
| `LADDER_MIN_LEAD_MINUTES` | `30` |
| `LADDER_MIN_TYPE_GAMES` / `LADDER_MIN_TYPE_HIT_RATE` / `LADDER_MIN_HALF_HIT_RATE` | `40` / `0.60` / `0.55` |
| `LADDER_MIN_DECIMAL_ODDS` / `LADDER_MAX_DECIMAL_ODDS` | `1.6666` (-150) / `2.50` (+150) |
| `LADDER_VOID_AFTER_HOURS` | `72` |

## Social posting (optional)

Off unless `LADDER_SOCIAL_ENABLED=1`. `LADDER_SOCIAL_PLATFORMS` limits the
list (default `x,facebook,bluesky,discord`). A platform is used only when all
its variables are set. Credentials live only in the environment.

| Platform | Variables | Notes |
|---|---|---|
| X | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` | App with write access (paid API tier) |
| Facebook Page | `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN` | Long-lived Page token with `pages_manage_posts` |
| Bluesky | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | App password, not the account password |
| Discord | `LADDER_DISCORD_WEBHOOK_URL` | Channel webhook |

Instagram and TikTok are not automated (their official APIs need media and app
review); use the copy text instead.

Posts never name the pick or odds. "Day N is live" is queued only after the
day's pick is saved, and is skipped if the day has already settled. A result
post is queued only after a ladder completes or fails. Each post is claimed
before sending, retried up to 5 times on failure, never sent twice, and moved
to `needs_review` if a send was interrupted mid-way.

Copy text for manual posting is shown to admins at the bottom of the page,
e.g.:

> PredictionLab Ladder — Day 1 is live. Today's pick is available to premium
> members at PredictionLab.io.
