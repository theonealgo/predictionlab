# PredictionLab UI Architecture

PredictionLab uses a **component-driven UI architecture**. The goal is to
centralize the existing product UI — not to redesign it.

This is the lock-in before any sport page is restyled or copied.

## Global site shell

Every page uses the **same** header, hamburger/mobile nav, footer, typography,
page width, spacing, buttons, cards, tabs, filters, loading states, and error
states.

Do **not** create sport-specific copies of Header, Footer, HamburgerMenu,
Navigation, or PageShell. A header change must appear on every sport.

Sport links come from **one** registry (`SPORTS` / site chrome). Header,
hamburger, and footer must consume that same source.

## Three columns (every sport picks page) — hard layout lock

Owner 2026-11-17. Do not punch holes in this layout while doing props, speed,
chrome, or “while I’m here” work.

| Column | Role |
|--------|------|
| **Left** | Sports list / nav (picks). Props has its **own** left bar on `/player-props` only. |
| **Middle** (`pl-center` / prediction box) | **Upcoming prediction cards for the current slate only** (this week / upcoming days). |
| **Right** | Results board / tracking (and related right-rail content). |

### Middle column — prediction cards only

- The middle box is for **prediction cards** for **any** sport.
- Finished games, graded cards, and old nights (e.g. NFL Nov 8 finals) must
  **not** sit where this week’s upcoming prediction cards belong.
- “Recent results” finished cards, if shown on the picks page at all, stay in
  their own **Recent results** block — never mixed into the upcoming-day
  prediction sections as if they were this week’s picks.
- Do **not** “fix” a sport by dumping historical finals into the middle
  prediction area. Do **not** change shared card routing for one sport while
  working on another surface (props, left nav, PageSpeed, etc.).

If the middle column shows the wrong slate, stop and ask the owner. Do not
rewrite SportPage / card feeds across sports to chase a single miss.

## Sport groups

### Team sports — one Predictions template + one Results template

MLB, NHL, NBA, NCAAB, NCAAW, NFL, NCAAF, WNBA, CFL.

Canonical implementations in this repo (do not invent a new design):

- Predictions: shared picks card template (`espn_predictions_template` / pick-conf-grid)
- Results: `DAILY_RESULTS_TEMPLATE` (MLB is the signed-off reference)
- Results chart: MLB-shaped Consensus Based Betting Records (6/6 + all-but for
  6-model sports; WNBA/NBA stay 4-model)

Do **not** create `NFLPredictionCard`, `MLBAccuracyChart`, weekly NFL-only
boards, or a one-card “last night only” results page. Sport differences belong
in configuration and data adapters.

### Individual competition — one shared template

Tennis and UFC. Do not build two independent page systems.

### Soccer — its own template family

1X2 / draw / league structure. Same global chrome. One soccer template across
EPL, La Liga, Serie A, Bundesliga, MLS, etc. League is data, not a new UI.

### Golf — its own template

Do not force golf into the team-sports card.

## Rules

1. Global UI components have **one** canonical implementation.
2. Team sports use the shared Team Sports predictions and results templates.
3. Tennis and UFC use the shared Individual Competition templates.
4. Soccer uses the shared Soccer templates.
5. Golf uses the Golf template.
6. Do not create sport-specific copies of shared components.
7. Do not duplicate Header or Footer.
8. Do not duplicate prediction/result card components.
9. Sport differences belong in configuration or data adapters whenever possible.
10. Do not redesign colors, cards, spacing, or navigation while centralizing.
11. Do not change prediction models, grading math, schemas, Stripe, or auth to
    “make the UI match.”
12. Before any UI change, answer: is this shared? does a component exist? can
    config/data solve it? If it is shared, edit the shared component.

## Checker

`qa/chart_shape.py` `team_results_template_issues` / `team_picks_template_issues`
/ `team_chart_template_issues` **FAIL** when a team sport leaves the shared
template (weekly frankenstein, last-night-only slate, missing game cards,
chart view still showing the card board).

If the checker catches a miss, unlock that miss and fix it. Do not hide the
miss to keep ship green.

## Migration

Do not rewrite the frontend. Use the existing MLB-shaped daily results template
and shared picks cards. Migrate team sports onto those. Then Tennis/UFC, soccer,
golf, then chrome consolidation.
