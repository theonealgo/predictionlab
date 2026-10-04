# Blog publisher notes (owner)

**Local only until you Manual Deploy.** Agents must not `git push`.

## What broke (Aug 2026)

Google Trends RSS (`_fetch_google_trends`) was turned into blog posts titled
`…: Google Trends Betting Angle` with boilerplate like “after 200+ Google searches”.
The CA “sports” feed still returned non-sports queries (Disney, stocks, weather, etc.),
and even sports queries (Washington Open, ATP Montreal) were branded as Google Trends —
unacceptable on a sports site.

Those posts were quarantined to `data/blog_posts_quarantine_2026-08-03.json`
(including live leftovers like Washington Open).

## What runs now

- **Trends → blog: permanently OFF** (`_BLOG_AUTO_TRENDS_ENABLED = False` in `NHL77FINAL.py`).
  Fetch / generate helpers always return empty even if the flag is flipped.
- **On every `/blog` load:** `_purge_google_trends_from_blog_disk()` strips Trends /
  Betting Angle posts from memory **and rewrites** `data/blog_posts.json` on disk
  (quarantine → `data/blog_posts_quarantine_trends.json`). This is what kills stale
  Render JSON after Manual Deploy even if the volume still had spam.
- **Game-day previews** for in-season gated sports: `MLB`, `WNBA`, `UFC` (event days only).
  Not tennis / Washington Open / ATP unless you expand `_BLOG_GAME_DAY_SPORTS` later.
- Titles look like `Team A at Team B — Month Day, Year preview` (UFC uses `vs`).
- One post per game from ESPN **scoreboard** schedule facts + CTA to Prediction Lab picks.
- **Soro “Trending in Sports” embed stays on `/blog`** (restored — it was wrongly
  removed during the Trends purge; only Google Trends auto-posts should be off).
- **Soro embed is a paid product — do NOT remove it.** Killing Google Trends
  auto-blogs must never touch the Soro `#soro-blog` / `app.trysoro.com` embed.
- **Google Trends auto-blogs are forbidden** (`…: Google Trends Betting Angle`
  titles, Trends RSS → posts). Keep `_BLOG_AUTO_TRENDS_ENABLED = False`.

Config knobs in `NHL77FINAL.py`:

- `_BLOG_GAME_DAY_SPORTS` — allowlist
- `_BLOG_GAME_DAY_AUTO_PUBLISH` — merge/refresh when `/blog` loads
- `_BLOG_GAME_DAY_MAX_POSTS` — safety cap

## Run locally

```bash
cd ~/Documents/Personal/predictionlabfix_work
python3 scripts/generate_game_day_blog.py --dry-run
python3 scripts/generate_game_day_blog.py
```

Then open `/blog` on a local app process to review titles/bodies.

## Ship to live (Trends kill — Manual Deploy checklist)

**Live still shows old Trends until you Manual Deploy.** Agents never push.

### Must-deploy files (Trends die only if these ship)

1. `NHL77FINAL.py` — `_BLOG_AUTO_TRENDS_ENABLED = False`, generators return `[]`,
   and **`_purge_google_trends_from_blog_disk()` runs on every `/blog` load** and
   **rewrites** `data/blog_posts.json` on the Render disk if spam remains.
2. `data/blog_posts.json` — clean archive (no Google Trends / Betting Angle posts).
3. `data/BLOG_OWNER_NOTES.md` — optional notes only (not required for purge).

Do **not** remove Soro (`#soro-blog` / `app.trysoro.com`) — paid product.

### After Manual Deploy — watch

1. Open `https://predictionlab.io/blog` — confirm **zero** “Google Trends Betting Angle” cards.
2. Confirm **Soro “Trending in Sports”** embed still present.
3. Hit a stale Trends slug if known (e.g. `…-google-trends-betting-angle-…`) —
   expect **301 → /blog** with `noindex`; first `/blog` hit also purges Render JSON.
4. Quarantine append (server disk, not user-facing): `data/blog_posts_quarantine_trends.json`.
