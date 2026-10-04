# Implementation prompt: team-sports pages from signed-off MLB

Use this document to implement **NCAAF / NFL / CFL / NHL** (and any other 6-model team sport) so they match **signed-off MLB** on `:5052`. MLB is the reference. Do **not** open, copy, or edit MLB source to “make it match.”

**Do not edit:** any `mlb_*.py`, `locked_pages/mlb/*`, `static/css/mlb-*.css`, `static/js/mlb-*.js`, or any `NHL77FINAL.py` function / branch whose name or job is MLB (`render_mlb_*`, `_apply_mlb_*`, `_mlb_*`).

**Do use:** the shared picks template, the shared results card include, the shared chart JS, and the existing consensus builders (called with `sport=`). Sport differences are **labels and data adapters**, not new pages.

Verified against live `:5052` on 2026-09-13 (`/mlb-picks`, `/mlb-results`, `/mlb-results?view=chart`, `?market=spread`, `?market=totals`) plus `docs/UI_ARCHITECTURE.md`, `templates/espn_predictions_template.html`, `templates/includes/game_card_body.html`, `templates/team_results.html`, `static/js/team-results.js`, `qa/chart_shape.py`, and `mlb_consensus_hub.py` function names.

---

## 0. Goal and hard rules

Ship one shared Team Sports UI:

| Surface | Shared implementation |
|---|---|
| Picks | `templates/espn_predictions_template.html` + `templates/includes/game_card_body.html` (`is_results=false`) |
| Results cards | Same `game_card_body.html` (`is_results=true`) inside the daily results page |
| Results chart | Shared chart (`templates/team_results.html` + `static/js/team-results.js`) with SSR consensus boards |

**Hard rules**

1. **Do not invent Books, Edge, or model numbers.** If books did not post a line, show `—`. If a model has no rating, show honest `N/A`. Never fill blanks with another model’s %, a coin-flip 50%, or a guessed American odds number.
2. **Do not restyle locked cards.** Reuse existing classes. No `NFLPredictionCard`, no weekly frankenstein, no sport-colored redesign.
3. **One shared template.** If the checker reports a team-sports template miss, fix the page to match this spec. Do not weaken the checker.
4. **Efficiency is its own model.** It must not copy Edge / XSharp / Sharp Consensus / `disp_ml_prob` just to fill the box. Missing Efficiency → `N/A` / `N/A` with a reason, never a copied % and an N/A side.
5. **Unlock one sport, one miss.** Do not patch sport B while sport A is the unlock. Do not touch MLB.
6. **No lock / sandbox / vendor text on user-facing HTML.** No “TheOddsAPI”, “Elo + market blend”, isolation banners, or agent notes.
7. **Do not change model math** to make the UI look full. Wire real stored values or honest empties.

**6-model sports (this spec):** MLB (reference), NFL, NHL, NCAAF, CFL.

**4-model exception (do not apply this spec’s 6-box grid):** WNBA, NBA — Edge / XSharp / Sharp Consensus / Efficiency only.

**Sport-only label swaps** (same anatomy, different words):

| Concept | MLB | NHL | NFL / NCAAF / CFL |
|---|---|---|---|
| ATS market | Run Line | Puck Line | Spread |
| Face chip | `Books run line` | `Books puck line` | `Books spread` |
| Odds table row | `Run Line` | `Puck Line` | `Spread` |
| Chart tab | `Spread / Run Line` or `Run Line` | `Puck Line` | `Spread` |
| Consensus spread board | `Books · Prediction Lab · XSharp — Run Line` | same shape, “Puck Line” | `Books · Prediction Lab · XSharp — Spread` |

---

## 1. Global chrome (do not invent)

Every picks, results, and **chart** page uses the **same site shell** as MLB picks. A thin research-site `Picks | Results` bar is a fail.

### 1.1 Header — `header.pl2-header`

Required pieces (classes and labels are literal):

- `button.pl2-burger#navHamburger` — hamburger / TV drawer
- `a.pl2-brand` → `/` — `PL /` + `PREDICTION LAB`
- `nav.pl2-nav` with three `div.pl2-navdrop` triggers (`button.pl2-nav-trigger`):
  - **Sports** → `/nba-picks`, `/nfl-picks`, `/mlb-picks`, `/nhl-picks`, `/soccer-picks`, `/ncaab-picks`, `/ncaaf-picks`, `/ncaaw-picks`, `/wnba-picks`, `/cfl-picks`, `/tennis-picks`, `/ufc-picks`, `/golf-picks`
  - **Models** → `/performance`, `/our-model-vs-sportsbooks`, `/ai-sports-betting-picks-today`, `/tutorial`
  - **Results** → `/all-sports-results`, each `/{sport}-results`, `/daily-report`, `/results/downloads`
- `a` **Blog** → `/blog`
- `a` **Pricing** → `/plans`
- `a.pl2-search` → `/search`
- `div.pl2-account` with Login / Sign Up / Pricing / Manage billing

Hamburger drawer (`tv-drawer`) uses the same registry: Picks & Predictions, Props & Models, Results & Tracking, Company. Do not invent a second menu.

Checker: `site_chrome.chrome_gaps` / `header_matches_site_chrome`. Fail labels include `thin Picks|Results bar instead of Sports/Models/Results`, `header is missing Sports / Models / Results`, `Sports menu missing …`, `header is missing search or account`, `header is missing Blog or Pricing`.

### 1.2 Footer — `footer.site-directory-footer`

Locked partial `templates/partials/site_directory_footer.html`.

Visible copy:

- Label: `Knowledge Directory`
- Wordmark: `PREDICTIONLABS.IO`
- Sub: `AI-powered sports betting picks. Daily. Transparent. Data-driven.`
- Columns: **AI Picks by Sport**, **Research Tools**, **Access**, **Company**
- Company must include `Affiliate Program` (`/affiliate`), Contact, Privacy, Terms, Refund Policy, Responsible Gaming

Checker: footer is not the locked `site-directory-footer` → fail.

### 1.3 Page width / type

Shared product chrome (`sports-chrome.css` / `picks-nav-overrides.css`). White page, max-width container ~1400px. Do not create a sport header, sport footer, or sport hamburger.

---

## 2. Picks page

**Route:** `/{sport}-picks`  
**Template:** `espn_predictions_template.html`  
**Card include:** `includes/game_card_body.html` with `is_results=false`  
**Title pattern:** `{Sport} Predictions Today | predictionlab.io` (or `… Predictions for {date}` when the slate is a single non-today date)

Live MLB heading: `⚾ MLB AI Picks, Predictions and Model Probabilities` — `{icon} {Sport} AI Picks, Predictions and Model Probabilities`.

### 2.1 Page chrome under the header

1. SEO blurb (sport-specific paragraph already in the shared template — do not add sandbox notes).
2. `.section-tabs`
   - `a.tab.active` → picks: `📊 Predictions`
   - `a.tab` → results: `🎯 Results`
   - Non-premium: `⭐ Join Premium` → `/plans`
3. Non-premium strip: `Unlock spreads, totals, and projected scores — Join Premium →`
4. `.date-nav`
   - `.nav-arrow` `‹` / `›` (`previousWeek` / `nextWeek`)
   - `#dateBubbles.date-bubbles`
   - optional `#datePicker`
5. `.picks-view-controls`
   - `.pv-toggle`: `📊 Cards` (`#pvCardsBtn`) | `📈 Chart` (`#pvChartBtn`)
   - `📋 Copy All` (`#pvCopyBtn`)
   - When chart is active: `nav.picks-market-tabs#picksMarketTabs` — **Moneyline** | **Spread** (or Run Line / Puck Line) | **Totals**
6. Per-date `.date-section#date-YYYY-MM-DD` with `.date-header` (`📅 YYYY-MM-DD` + `TODAY` badge when applicable) and `.games-grid`

Each stack: `div.game-card-stack[data-pick-card]` wrapping `div.game-card.pick-card.is-expanded`.

Required `data-pick-card` attributes (chart + copy): `data-away`, `data-home`, `data-league`, `data-time`, `data-pick`, `data-conf`, `data-h2h`, `data-edge`, `data-books-spread`, `data-books-total`, `data-pl-spread`, `data-xs-spread`, `data-total-ev`, and when premium: `data-m-grinder2`, `data-m-takedown`, `data-m-edge`, `data-m-xsharp`, `data-m-efficiency`, `data-m-consensus`, `data-pl-proj`, `data-xs-proj`.

Checker: `team_picks_template_issues` fails without `data-pick-card` / `pick-conf-grid` / `pc-name`.

### 2.2 Card face anatomy (must exist)

```
header.pick-card-header
  span.league-badge     e.g. "⚾ MLB" / sport name (soccer uses league name)
  span.game-time        e.g. "2:10 PM ET" or "FINAL"

div.matchup-row
  div.team-slot[.favored]          away
    img.team-logo
    div.team-name
    div.model-tag                  "Sharp Consensus" (face model)
    div.win-pct                    e.g. 60.0 + span.unit %
    div.ml-stack.face-ml-stack
      div.ml-line.face-books-ml
        span.ml-src.books          "Books"
        span.ml-num.fav|.dog       American odds, + for dogs
      div.ml-line.face-pl-ml
        span.ml-src.pl             "Prediction Lab"
        span.ml-num.fav|.dog
  div.matchup-at                   "@"
  div.team-slot[.favored]          home  (same stack)

div.lines-strip
  div.line-chip                    Books run line / puck line / spread
  [MLB] div.line-chip.rl-confidence-chip   Run Line Confidence 0–100
  div.line-chip                    Books total  "O/U 8"
  div.line-chip.edge-chip          Edge  "4/6 · 40%" (agree_n/6 · edge%)

footer.card-footer
  button.view-details-btn          "Less details" / "View details"
```

Favored team slot gets `.favored` (green border). Face win% is **Sharp Consensus** (`face_home_prob` / `face_away_prob`), not a random model.

Edge info button tip (locked MLB): `Edge calculated using Model Consensus.` Run Line Confidence tip: it is the run-line lean 0–100, **not** a moneyline or totals pick.

### 2.3 Expanded details (`div.card-details`)

Premium / logged-in:

**Odds & Lines** — `div.odds-pricing-section` > `div.odds-pricing-title` = `Odds & Lines`

| Market | Books `th.col-books` / `td.val-books` | Prediction Lab `th.col-pl` / `td.val-pl` | XSharp `th.col-xs` / `td.val-xs` |
|---|---|---|---|
| Run Line / Puck Line / Spread | e.g. `Cleveland Guardians -1.5` | team + line | team + line |
| Total | e.g. `8` | e.g. `6.5` | e.g. `6.5` |

**Projected Score** — `div.proj-score-box` > `div.proj-score-title` = `Projected Score`

- `span.proj-model.pl` **Prediction Lab** — `Away 2.5 – Home 4`
- `span.proj-model.xs` **XSharp** — same format

**Pick Confidence** — `div.pick-conf-bar` > `div.pick-conf-title` = `Pick Confidence`  
Grid: `div.pick-conf-grid` (6 columns desktop, 3 on narrow).

Canonical **picks** box order (from the shared template `conf_models`):

| `.pc-name` | Internal key | Source field |
|---|---|---|
| Grinder2 | `glicko2` | `glicko2_prob` |
| Takedown | `trueskill` | `trueskill_prob` |
| Edge | `elo` | `elo_prob` |
| XSharp | `xgb` | `xgb_prob` |
| Efficiency | `efficiency` | `efficiency_prob` **only** |
| Sharp Consensus | `consensus` (`.pc-box.consensus`) | `ensemble_prob` |

Each box: `.pc-val` (win% of the picked side, always ≥ 50) + `.pc-side.home|.away` (team name). If `prob` is 40 for home, display `60.0%` and the away team.

**Footer extras** — `div.odds-extras-footer` > `.sf-item`

- `H2H Last 10` — e.g. `6.5 (10 games)` or `First meeting`
- `EV` — moneyline EV, signed percent
- `Total EV` — totals EV vs books O/U
- `Best EV` — `Spread` / `Total` / `ML` when present

Non-premium details: `🔒 Lines & projections locked.` + login / plans. Face Books ML + win% stay visible.

Analysis toggle (`Analysis ▾`) may include injuries. Do not put pipeline / vendor IP there.

### 2.4 Picks chart view (same page, not a new route)

`.date-section.chart-mode` hides `.games-grid` and shows `.chart-table-wrap` > `table.picks-chart-table`.

Market tabs switch columns (from the shared JS in the picks template):

**Moneyline:** Matchup, Time, Pick, H2H L10, Edge, Books Spr, Books Tot, then Grinder2 / Takedown / Edge / XSharp / Efficiency / Sharp Cons., then PL Proj / XSharp Proj when present. Books ML column when real books ML exists.

**Spread:** Matchup, Time, Books run line / puck line / spread, Prediction Lab, XSharp (no invented lines).

**Totals:** Matchup, Time, Books total, H2H L10, Prediction Lab total, XSharp total, Total EV.

### 2.5 What must never be blank vs honest N/A

**Never blank / never fake when the data exists**

| Field | Rule |
|---|---|
| Team names + `img.team-logo` | Real ESPN (or sport) logo. Fail if `src` empty, `#`, `pl-logo`, `placeholder`, or `/500/.png`. |
| `span.game-time` | Real clock (`2:10 PM ET`) or `FINAL`. Fail if the slate is `Upcoming` / `TBD` / no digit when kickoffs are known (`picks_clock_issues`). |
| Face `.win-pct` | Real Sharp Consensus %. Fail if ≥90% of faces are `50.0` (`picks_placeholder_issues` “coin-flip slate”). |
| Books ML `.ml-num` | Show posted American odds. Fail if ≥90% of face Books ML are `—` on a slate that has books. |
| Books spread / total chips + Odds table Books column | Show the posted line. Blank Books **while books posted a line** is a fail. |
| Pick Confidence (6 boxes) | All six names present. Real % when that model rated the game. |
| Efficiency | Own `efficiency_prob`. Fail if a `%` is shown with side `N/A` (`efficiency_copied_na_issues`). |
| H2H Last 10 | Number + game count when meetings exist. Fail if some/all cards are `—` while others have values (`h2h_gap_issues`). |
| Prediction Lab ML | Face `face-pl-ml` must not be all `—` on an in-season slate. |

**Honest empty (allowed)**

| Situation | Show |
|---|---|
| Books never posted ML / spread / total | `—` (em dash). Do **not** invent DK-style numbers. |
| Model has no rating for this sport/game | `.pc-val` + `.pc-side` = `N/A` with `title` reason (`{Model} rating is not available for {Sport} yet`) |
| Efficiency not computed | `N/A` / `N/A` — **do not** fall back to `disp_ml_prob` / Edge / Consensus |
| First meeting | `First meeting` (this is **not** a blank H2H) |
| No projection yet | Projected Score `—` |
| Pick’em / no run-line edge | Do not invent a −1.5 side; omit grade |
| Game not final | No W/L checkmarks |

`—` = we looked and there is no posted/computed value.  
`N/A` = this model/stat does not apply yet.  
`50%` = a real coin-flip rating from that model, not a placeholder. A whole slate of 50s is a placeholder fail.

---

## 3. Results cards page

**Route:** `/{sport}-results` (no `view=chart`)  
**Title:** `{Sport} Results | predictionlab.io`  
**H1:** `{icon} {Sport} Results, Performance and Model Accuracy`

This is the **daily** template, not a weekly board and not last-night-only.

### 3.1 Toggles (cards page only)

1. `.pl-view-toggle` — `a.pl-view-btn.active` **Cards** → `/{sport}-results`; **Chart** → `/{sport}-results?view=chart`
2. `.section-tabs` — `📊 Predictions` → picks; `🎯 Results` active

### 3.2 Last Night / Last 7 tallies — all 6 models

`div.daily-tally` + `div.daily-tally-grid` + `div.daily-tally-card`

Headings (literal patterns):

- `Last Night's {Sport} Results — YYYY-MM-DD (N games)`  
  MLB example: `Last Night's MLB Results — 2026-09-12 (15 games)`  
  NCAAF: `Last Night's NCAA Football Results — …`
- `Last 7 Days {Sport} Results — YYYY-MM-DD to YYYY-MM-DD (N games)`

Subhead: `MONEYLINE`

Six moneyline tiles, this order, these display names:

| Class | Display |
|---|---|
| `.daily-model` | `⭐ Grinder2` |
| | `🎯 Takedown` |
| | `📊 Edge` |
| | `🤖 XSharp` |
| | `🏆 Sharp Consensus` (`.daily-tally-card.highlight`) |
| | `⚡ Efficiency` |

Each tile: `.daily-acc` (e.g. `33.3%`), `.daily-rec` (`3-6`), `.model-units` (`-3.3u` / `+4.4u`).

Under the six: two extra tiles — `📈 Spread` and `🎲 Over/Under` with acc + rec (pushes allowed, e.g. `62-28-7`).

If other models have a W-L, **Grinder2 / Takedown / Efficiency must not be `—`** (`blank_moneyline_model_issues`). Missing Efficiency as a deleted tile is a fail (`team_results_tally_model_issues`). Show a dash only when that model truly has zero graded games **and** siblings are also empty.

Last Night date must agree across moneyline / spread / totals (`last_night_window_mismatch_issues`). Super Bowl / Week 22 leftovers on an in-season football page are a fail.

### 3.3 ROI — `💰 Model Performance (Flat Unit Tracking)`

Copy: `Percentages are unit ROI (profit per $1 risked), not moneyline win rate.`

Three columns: **Moneyline**, **Spread**, **Total (O/U)** — each with **7 Days** and **Season** (ROI %, W-L-P, units).

### 3.4 Season Performance + Moneyline Accuracy

`🏆 Season Performance` — three hero cards:

- `🎯 Moneyline (Sharp Consensus)` — season % + W-L
- `📈 Spread (XSharp)` — season ATS
- `🎲 O/U (XSharp)` — season totals

Then `h3` **Moneyline Accuracy by Model** — `.model-grid` > `.model-card` for all six (Efficiency may read `⚡ Efficiency Season`). Each: `.model-acc`, `.model-rec`, `.model-units`.

Checker requires the heading `Season Performance` and all six names in Last Night, Last 7, and Moneyline Accuracy.

### 3.5 Market tabs on the cards page

`nav.picks-market-tabs.pl-results-market-tabs` inside `#pl-results-markets`:

- **Moneyline** → `?market=moneyline`
- **Run Line** / **Spread** / **Puck Line** → `?market=spread`
- **Totals** → `?market=totals`

Switching tabs shows the matching consensus board (below). It must not wipe the game cards.

### 3.6 Consensus 6/6 dissent table

Built by `mlb_consensus_hub.build_consensus_records_html` / `inject_consensus_records_html` (pass `sport=`). Wrapper: `div.pl-consensus-records#pl-consensus-records`.

**H2:** `Consensus Based Betting Records`

**Subcopy (6-model, literal idea):**  
`Moneyline on the pregame majority among the 6 live models. Each row is one dissent combination (model(s) that broke from the majority). 0-0 means that combination had no graded games in the window. Even splits are omitted.`

| Agreement | Last night (YYYY-MM-DD) | Past 7 days | Past 30 days |
|---|---|---|---|
| `6/6 unanimous` | W-L + % + `.cons-bar` | … | … |
| `5/6 — all but Edge` | … | … | … |
| `5/6 — all but Efficiency` | | | |
| `5/6 — all but XSharp` | | | |
| `5/6 — all but Grinder2` | | | |
| `5/6 — all but Takedown` | | | |
| `4/6 — all but Edge and Efficiency` | (and every other 2-dissent combo that occurred) | | |

Row labels use `td.bucket` and `_consensus_combo_label_html` (`N/N unanimous` or `K/N — all but {names}`). Color bars: `div.cons-bar > i`. Empty window: `0-0` (not a missing row). Even 3/3 splits are omitted, not graded.

**Forbidden (checker `six_model_chart_issues`):**

- `among the 4 live models` / `4/4` / `5/5` without `6/6`
- WNBA rows `2/4 Split / no consensus` or `1/4 Strong disagreement`
- Missing `6/6 unanimous` or `all but`
- Missing Agreement / Last night / Past 7 days / Past 30 days
- Missing `.cons-bar`
- Missing Moneyline | Spread | Totals tabs
- Last-night consensus all `0-0` while Last Night tally has games (`consensus_empty_vs_tally_issues`)

Panel size is 6. Helper names: `consensus_models_for_sport`, `_consensus_combo_label_html`, `_consensus_agreements_from_finals`, `_fold_agree_n`. Default `MODEL_ORDER`:

`Grinder2`, `Takedown`, `Edge`, `XSharp`, `Sharp Consensus`, `Efficiency`

WNBA/NBA override to 4 models — do not use that override for NFL/NCAAF/CFL/NHL.

### 3.7 PL vs Sportsbook

`build_pl_vs_books_records_html` → `div.pl-consensus-records.pl-books-pl-records#pl-books-pl-records`

**H2:** `PL vs Sportsbook`

**Subcopy:** compares the Prediction Lab favorite with the sportsbook favorite. Books favorite from American sportsbook odds. PL favorite from Prediction Lab moneyline odds, projected-score lean as fallback.

| Signal | Last night | Past 7 days | Past 30 days |
|---|---|---|---|
| Books favorite | | | |
| PL favorite | | | |
| PL vs Books disagree | | | |
| PL and Books agree | | | |

Fail if last night is all `0-0` while the daily tally graded games, or Books favorite is `0-0` while PL favorite has a record (`pl_vs_books_empty_vs_tally_issues`).

### 3.8 Spread / Totals consensus boards (not leftover ML)

Inside `[data-market-panel="spread"]` and `[data-market-panel="totals"]`:

**Spread —** `div.pl-consensus-records.pl-three-way-records#pl-spread-three-way`  
H2: `Books · Prediction Lab · XSharp — Run Line` (or `… — Spread` / `… — Puck Line`)

Agreement rows (MLB shape): `PL = Books`, `PL = XSharp`, `Books = XSharp`, `All 3 agree`, `All 3 disagree`, `2/3 — PL + XSharp`, `1/3 — Books only`. Same Last night / Past 7 / Past 30 + `.cons-bar`.

Also available: `build_pl_xs_records_html(..., market="spread"|"totals")` → `Prediction Lab & XSharp — Spread` / `— Totals` with Model column (Prediction Lab, XSharp).

**Totals —** `#pl-totals-three-way`  
H2: `Prediction Lab · XSharp — Totals (vs book line)`

Rows: `3/3 — PL = XSharp`, `2/3 — vs book line`, `1/3 — PL only`, `1/3 — XSharp only`. Books is the O/U **line**, not a third pick.

Switching to Spread/Totals must **not** leave the moneyline Edge-pick table visible.

### 3.9 Best Performing Model (cards page)

`section.pl-mlb-analytics` (reuse the class; do not invent a sport-specific analytics skin) or the shared `.pl-analytics-*` block.

**H3/H2:** `Best Performing Model`  
Three equal cards — **Today**, **Last 7**, **Season** — each: model name, %, W-L, units.

Also on MLB: `Efficiency by Market` (ML / Spread / Total). Keep the same three-up grid. The Best Performing row must be **as wide as the sibling tally boxes** (see §6).

### 3.10 Date strip + game cards

After analytics: `.date-nav` (same arrows + bubbles as picks).  
`div.date-section#date-YYYY-MM-DD` > `.date-header` > `.games-grid` > `.game-card`.

In-season football (Aug–Dec): **more than one date** and **at least 3 game cards**. Last-night-only is a `team_results_template_issues` fail.

**Results card anatomy** (`game_card_body.html` `is_results=true`):

```
div.card-hero
  div.card-hero-meta-line     FINAL  (or UPCOMING · time)
  div.teams-split
    away / @ / home
      img.team-logo
      div.team-name
      div.final-score[.score-winner]     actual score

div.odds-pricing-section
  Odds & Lines table (Market / Books / Prediction Lab Odds / XSharp)
  Projected Score (PL + XSharp) — the pregame projection, not the final

div.pick-conf-bar
  Pick Confidence 6-box grid
  .pc-box.correct | .wrong
  side shows ✅ / ❌

div.odds-extras-footer
  Spread pick    e.g. "Los Angeles Dodgers -1.5 ❌"
  Total pick     e.g. "Over 8.0 ★ ❌"
  H2H Last 10
  Actual total   e.g. 5
  Edge           when present
```

`results_card_parameter_issues` fails when cards drop Odds & Lines, a required model box, H2H Last 10, or show Efficiency as N/A/blank on a graded slate.

Grades compare **actual score to our line and the books line**. Example: Books −1.5, final 3–2 → favorite failed to cover → spread ❌ even if ML ✅. Totals: actual sum vs Books O/U and vs PL/XSharp projected totals.

---

## 4. Results chart view

**Route:** `/{sport}-results?view=chart`  
**Markets:** `&market=moneyline` (default), `&market=spread`, `&market=totals`  
**Must be a different page from Cards.** Same HTML as cards = `team_chart_same_as_cards_issues` fail.

Chart is **not** leftover cards. Forbidden on chart HTML:

- `game-card` / `data-pick-card` dumps (≥3 cards = fail)
- Cards-page leftovers: `Season Performance`, `Moneyline Accuracy by Model`, `Overall Model Performance`, `Last Night's {SPORT} Results`, `Last 7 Days {SPORT} Results`, `ncaafTopDates` / `ncaaf-top-dates`, `Model Performance (Flat Unit Tracking)`
- Date strip / ROI board
- Weekly `week-section` / `NFL - Week by Week`

Allowed chart chrome: site header/footer, Cards|Chart toggle (Chart active), market tabs, window tallies, Best Performing, consensus boards, **one** detail table.

Set `body[data-ssr-chart="1"]` and `body[data-market]`. Hide `#finals.cards` (`hidden` + `aria-hidden`). `static/js/team-results.js` already hides the card grid on chart.

### 4.1 Market tabs

`nav.market-tabs#market-tabs` **and** `#pl-results-markets .pl-results-market-tabs`:

| `data-market` | Button label |
|---|---|
| `moneyline` | Moneyline |
| `spread` | Spread / Run Line (MLB), Puck Line (NHL), Spread (football) |
| `totals` | Totals (O/U) |

Hydrate via `/{sport}/api/picks` (or sport API) with `markets.moneyline|spread|totals`. Empty shells that invent blank Efficiency spread/total cards are forbidden (`ml_only` sports only).

### 4.2 Window tallies (chart)

`#tallies.tally-wrap` — three `section.tally` blocks (short titles, **not** the cards-page “Last Night's MLB Results — date” H2):

- `Last Night (N games)` — 6 `.tally-card.daily-tally-card` (Grinder2 … Efficiency)
- `Last 7 (N games)` — same 6
- `Season (N games)` — same 6

`team_chart_window_tally_issues`: missing Last Night / Last 7 / Season, or fewer than 6 model cards.

### 4.3 Best Performing Model (full width)

`section.tally.pl-analytics` > `h2` **Best Performing Model** > `.tally-grid` of three `.tally-card`: **Today**, **Last 7**, **Season** (name, %, W-L).

Must be **as wide as the other tally boxes**. Checker `best_performing_width_issues` fails unless the Best Performing grid is stretched (`section.pl-analytics .tally-grid { width: 100% }` or equivalent). A narrow centered pill next to wide Last Night rows is a fail.

Optional under it: `Efficiency by Market` (do not invent spread/total Efficiency cards when `ml_only`).

### 4.4 Consensus boards per market

Same boards as §3.6–3.8. Moneyline panel: 6/6 dissent + PL vs Sportsbook. Spread panel: Books · PL · XSharp board. Totals panel: PL · XSharp vs book line.

`six_model_chart_issues` runs on 6-model chart HTML.

### 4.5 Detail tables (signed-off MLB columns)

Use `static/js/team-results.js` MLB column contract (`mlRowHtml` / `souRowHtml` / `isMlb()` branch). Other 6-model sports must render **the same columns**, with “run line” relabeled to spread / puck line.

#### Moneyline — `table.results-table`

| Date | League | Match | Score | Edge pick | % | Result | Models |
|---|---|---|---|---|---|---|---|
| 2026-09-12 | MLB | Away @ Home | `away–home` | team | `55%` | Correct / Wrong / — | `Grinder2 LAD ✓ Takedown …` |

Heading: `Moneyline games (N)` or `Moneyline records`.  
`#ssr-finals` on moneyline may host this table; hide it when market ≠ moneyline.

#### Spread / Run Line — 9 columns

| Date | Time | Match | Score | Books run line | Prediction Lab | XSharp | Published pick | Result |
|---|---|---|---|---|---|---|---|---|
| 2026-09-12 | Final | Away @ Home | 2–3 | Home -1.5 | Home -1.5 | Away -1.5 | Home -1.5 | Correct / Wrong / Push |

Football/NHL: header `Books spread` / `Books puck line` instead of `Books run line`.  
**Must compare actual score to the lines** (cover / fail / push vs Books and vs PL). A table that is only Date / Match / Edge pick / Result is a fail (`team_chart_sou_table_issues`: `{market} chart still showing the moneyline Edge pick table`).

#### Totals — 13 columns

| Date | Time | Match | Score | Books total | H2H L10 | Prediction Lab total | Prediction Lab projected score | XSharp total | XSharp projected score | Total EV | Published pick | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-12 | Final | Away @ Home | 2–3 | 8 | 6.5 (10 games) | 6.5 | 2.5–4 | 6.5 | 4–2.5 | −1.7% | Under 8 | Correct |

**Must compare actual total (away+home) to Books and to our totals.** Honest `N/A` for H2H when first meeting; `—` when a projection/line was never stored. Do not invent EV.

Empty slate copy: `No records for this market on the current slate.`

Score format: **away–home** (MLB `mlbScore`). Result marks: `Correct` / `Wrong` / `Push` / `—`.

---

## 5. Bottom advertising / share card

On the **picks** page, after the educational blocks, before the footer:

```
div.social-export-wrap  data-share-picks="{N}"     N >= 2
  div.social-export-head
    div.social-export-title     "{Sport} Predictions Image"
    a.social-export-btn         Download image
    a.social-export-btn.primary Open fullscreen
  a.social-image-link > img     /share/predictions/{id}.jpg
```

Then `.share-strip` (X, Facebook, Instagram, …) and the site footer.

**Requirements**

- `share_ad_card_issues`: missing `.social-export-wrap` → fail; missing `data-share-picks` → fail; `N < 2` → `Share card has {n} pick(s); need at least 2`.
- **All text fits.** Team names, odds, and pick lines must not clip, overflow, or collide on the generated image. Prefer ≥2 complete pick rows (matchup + pick + line). Do not emit a one-pick or empty poster when the slate has two or more games.
- Image URL may be `/share/predictions/{hash}.jpg` or `/{sport}/share.jpg`. Checker `ship_parity` fails picks-with-cards and no share image.

---

## 6. What the checker must FAIL on

Implement so these `qa/chart_shape.py` / chrome / speed checks stay red until the page matches MLB.

| Checker | Fail when |
|---|---|
| `team_picks_template_issues` | No `data-pick-card` / `pick-conf-grid` / `pc-name` |
| `team_results_template_issues` | Weekly frankenstein (`NFL - Week by Week`, `week-section`); no Last Night tally; no `Season Performance`; no `game-card`; no `date-nav`; no `Consensus Based Betting Records`; in-season football with ≤1 date or <3 cards |
| `team_chart_template_issues` | Chart missing consensus; chart still shows cards-page headings; leftover card board |
| `team_chart_leftover_board_issues` | `ncaafTopDates`, ROI heading, Moneyline Accuracy, or ≥3 `game-card`s on chart |
| `team_chart_same_as_cards_issues` | Cards HTML == Chart HTML |
| `team_chart_window_tally_issues` | Chart missing Last Night / Last 7 / Season or <6 model tiles |
| `six_model_chart_issues` | Not 6/6 dissent (see §3.6) |
| `team_results_tally_model_issues` | Last Night / Last 7 / Moneyline Accuracy missing Grinder2, Takedown, Edge, XSharp, Sharp Consensus, or Efficiency |
| `results_card_parameter_issues` | Results cards missing a model box, Odds & Lines, H2H Last 10, or Efficiency N/A/blank |
| `blank_moneyline_model_issues` | G2 / Takedown / Efficiency are `—` while Edge/XSharp/Consensus have W-L |
| `picks_placeholder_issues` | Coin-flip 50% faces; Edge/XSharp/Consensus stuck at 50%; Books ML blank on the slate |
| `picks_clock_issues` | Kickoff `Upcoming` / TBD / no clock |
| `picks_logo_issues` | Missing / placeholder logos |
| `h2h_gap_issues` | H2H `—` on some/all cards when it should be a number or `First meeting` |
| `efficiency_copied_na_issues` | Efficiency shows `NN%` with side `N/A` |
| `team_chart_sou_table_issues` | Spread/Totals still show moneyline `Edge pick` table; missing Books column; no actual-vs-lines compare |
| `share_ad_card_issues` | No share wrap, no `data-share-picks`, or `< 2` picks |
| `best_performing_width_issues` | Best Performing narrower than sibling tally boxes |
| `consensus_empty_vs_tally_issues` / `pl_vs_books_empty_vs_tally_issues` | Boards 0-0 while Last Night graded games |
| `ncaaf_chart_api_issues` | `/ncaaf/api/picks` missing moneyline/spread/totals |
| `chrome_gaps` | Thin Picks\|Results bar; incomplete Sports menu; no directory footer |
| Page speed | Any of these routes **> 5s** (`qa/page_speed.py` `PAGE_SPEED_BUDGET`, message `Pages must load in 5s.`) |

Do not hide a miss by skipping the checker, special-casing the sport out of `_SIX_MODEL_SPORTS` / `_TEAM_SPORTS_SHARED_UI`, or inventing numbers so the regex passes.

---

## 7. Shared builders (call; do not fork MLB)

Use these **function names** with `sport="ncaaf"|"nfl"|"cfl"|"nhl"`. Do not add MLB-only branches and do not edit MLB-named renderers.

| Function | Job |
|---|---|
| `consensus_models_for_sport` | 6 names (or 4 for WNBA/NBA only) |
| `build_consensus_records_html` | 6/6 + all-but dissent table |
| `build_pl_vs_books_records_html` | PL vs Sportsbook |
| `build_pl_xs_records_html` | PL & XSharp spread/totals windows |
| `inject_consensus_records_html` | Insert boards + `_wrap_results_markets` tabs |
| `build_results_analytics` | Best Performing + Efficiency by Market payload |
| `_consensus_combo_label_html` | `6/6 unanimous` / `5/6 — all but X` |
| `_normalize_results_market` | `moneyline` / `spread` / `totals` |

Chart client: `static/js/team-results.js` — `DEFAULT_ORDER`, `MARKET_ORDER`, `mlRowHtml`, `souRowHtml`, `analyticsHtml`, `setActiveTab`. Prefer extending the **shared** MLB column branch to all 6-model sports rather than a thinner NCAAF-only table. The signed-off contract is the MLB 8 / 9 / 13 column tables in §4.5.

Picks/results cards: only `includes/game_card_body.html`. Set `spread_label` / `force_rl` from sport (MLB run line stays MLB; football `Spread`; NHL `Puck Line`).

---

## 8. Implementation sequence (for the agent doing the sport)

1. Confirm **UNLOCK {SPORT}** and the named miss. Lock every other sport.
2. Point `{sport}-picks` at `espn_predictions_template.html`. Confirm Predictions\|Results, date-nav, Cards\|Chart, `data-pick-card`, 6-box `pick-conf-grid`, Odds & Lines, H2H, clocks, logos.
3. Point `{sport}-results` at the **daily** results template (not week-by-week). Last Night / Last 7 / Season / Moneyline Accuracy with all 6 models + units; ROI; consensus 6/6; PL vs Sportsbook; date strip; full game cards.
4. Point `{sport}-results?view=chart` at the chart shell. Confirm it is **not** the cards HTML. Moneyline\|Spread\|Totals. Window tallies. Full-width Best Performing. Consensus per market. Detail tables from §4.5 with actual-vs-line compare.
5. Share card: `social-export-wrap` + `data-share-picks >= 2` + text fits.
6. Chrome: full Sports / Models / Results header + directory footer on picks, results, **and** chart.
7. Run `qa/chart_shape.py` helpers and chrome/speed checks locally against `:5052`. Fix the miss only. Do not invent Books/Edge/Efficiency. Do not touch MLB files.
8. When the miss is done, the sport is locked again.

### Local smoke (do not push)

```
/{sport}-picks
/{sport}-results
/{sport}-results?view=chart
/{sport}-results?view=chart&market=spread
/{sport}-results?view=chart&market=totals
```

Each must return 200 in ≤5s. Chart spread/totals must not show `Edge pick` + `Models` as the only market table.

---

## 9. Honest-empty vs invented (quick test)

Before calling a card “done,” walk one game:

1. If the book posted −7 / 45.5 / −150 — those numbers appear on face + Odds & Lines. If the book did not — `—`, not a guessed line.
2. Six Pick Confidence boxes exist. Efficiency % ≠ Edge % unless the models truly agreed. Efficiency never shows a % with side N/A.
3. H2H is a number + `(N games)` or `First meeting`, not an empty span.
4. Results: ML grade, ATS grade, and total grade can disagree. Spread/total grades use **actual score vs Books and vs PL/XSharp**.
5. Chart spread row includes Books line, PL, XSharp, published pick, result — not the moneyline Edge-pick table.

If any step requires making up a number, stop and leave the honest empty.
