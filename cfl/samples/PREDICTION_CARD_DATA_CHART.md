# Prediction card — data map (no duplicate display)

**Rule: each fact appears exactly once on the card.**

---

## Where each fact lives (exclusive)

| Fact | Show once here | Never also show in |
|------|----------------|-------------------|
| Book moneyline (American +170 / -205) | **Top** — under each team name | Odds table, confidence boxes |
| Book spread | **Odds table → Book row** | Top, PL/XSharp rows |
| Book total | **Odds table → Book row** | Top, PL/XSharp rows |
| PL Model spread | **Odds table → PL Model row** | Top, Book, XSharp, boxes |
| PL Model total | **Odds table → PL Model row** | same |
| PL Model projected score | **Odds table → PL Model row** | same |
| PL Model win % (ensemble / sharp consensus) | **Pick Confidence → Sharp Consensus box** | Odds table, top |
| XSharp spread / total / projected score | **Odds table → XSharp row** | Confidence box, Book, PL rows |
| XSharp win % | **Pick Confidence → XSharp box** | Odds table, top |
| Grinder2 win % | **Pick Confidence → Grinder2 box** | anywhere else |
| Takedown win % | **Pick Confidence → Takedown box** | anywhere else |
| Edge win % | **Pick Confidence → Edge box** | anywhere else |
| H2H last 10, EV, Total EV, Best EV pick | **Footer** | table, top, boxes |
| Team logos + names | **Top** | boxes (no logos in boxes) |

**Not in Odds table:** Grinder2, Takedown, Edge (no lines).  
**Not in Odds table:** any win % (those stay in confidence boxes only).  
**Not in Odds table:** Book moneyline (already at top).

---

## Cavs @ Knicks — values by slot

### Top (teams + book ML only)

| Team | Book ML |
|------|---------|
| Cleveland Cavaliers | +170 |
| New York Knicks | -205 |

### Pick Confidence (win % + pick side only — 5 boxes)

| Box | Value |
|-----|-------|
| Grinder2 | 52.7% · Knicks |
| Takedown | 53.0% · Cavaliers |
| Edge | 52.6% · Knicks |
| XSharp | 70.3% · Cavaliers |
| Sharp Consensus | 72.0% · Knicks |

### Odds & lines (3 rows — prices/lines/scores only, no %)

| Source | Spread | Total | Projected score |
|--------|--------|-------|-----------------|
| **Book** (`pl_book_odds_api`) | NYK -5.5 | 215.5 | — |
| **PL Model** | NYK -7.0 | 233.0 | CLE 113 – NYK 120 |
| **XSharp** | NYK -5.5 | 215.5 | 110.5 – 105.0 |

No “Moneyline” column in this table (ML is only at top for Book).

### Footer

H2H 233.0 (3 games) · EV -4.0% · Total EV +54.5% · Best EV pick: Total

---

## Layer → backend keys (for wiring)

| Slot | Keys |
|------|------|
| Top Book ML | `book_away_moneyline`, `book_home_moneyline` |
| Book spread/total | `book_spread`, `book_total`, `disp_book_spread` |
| PL row | `our_spread`, `our_total`, `our_home_pts`, `our_away_pts` |
| XSharp row | `xgb_spread`, `xgb_total`, `xgb_home_score`, `xgb_away_score` |
| Boxes | `glicko2_prob`, `trueskill_prob`, `elo_prob`, `xgb_prob`, `ensemble_prob` |

API `GET /api/pl-book-odds/...` feeds **top ML + Book row spread/total** only.

---

## Card wireframe (no duplication)

```
┌ TOP ─────────────────────────────────────┐
│ [logo] Cavaliers          +170         │
│ [logo] Knicks             -205    ▶    │
└────────────────────────────────────────┘
┌ PICK CONFIDENCE (5 boxes, % only) ─────┐
└────────────────────────────────────────┘
┌ ODDS & LINES ──────────────────────────┐
│          │ Spread │ Total │ Proj score │
│ Book     │ -5.5   │ 215.5 │ —          │
│ PL Model │ -7.0   │ 233   │ 113-120    │
│ XSharp   │ -5.5   │ 215.5 │ 110-105    │
└────────────────────────────────────────┘
┌ FOOTER (H2H, EV, …) ───────────────────┐
└────────────────────────────────────────┘
```

---

## What to remove from current prod card

- Premium side panel (duplicates consensus %, XSharp spread/total/score)
- “PL” second price at top if it repeats model or book
- Book / PL / XSharp **%** in any “moneyline” column
- Grinder2 / Takedown / Edge in Odds table
- Same metric in table and confidence box (e.g. 72% in both Consensus box and PL row)
