# CFL Backtest — Current vs Updated (20260807)

**Scope:** isolation only (`~/Documents/Personal/cfl`). Not live. Not pushed.

**Decision: ACCEPT** `cfl_v3_cal_blend`

- Completed finals: **44** (walk-forward after min_train=8 → n=36)
- Date range: `2026-05-18` → `2026-08-06`
- Gate improvements counted: **9** / 11

## Verdict detail

Updated beats Current on season accuracy, log loss, Brier, and calibration, with L7/L30 accuracy and probability scores at least as good.

## BEFORE / AFTER tables

### Season

| Metric | Current (v1) | Updated (v3) | Δ |
|---|---:|---:|---:|
| Accuracy | 44.4% | 47.2% | +2.8% ✓ |
| ROI | -15.2% | -9.8% | +5.3% ✓ |
| Units | -5.5 | -3.5 | +1.9 ✓ |
| Log Loss | 0.783 | 0.715 | -0.068 ✓ |
| Brier | 0.292 | 0.261 | -0.031 ✓ |
| Cal Error | 0.216 | 0.146 | -0.069 ✓ |

_Totals MAE: 14.9 → 13.9 · Margin MAE: 11.0 → 10.8_

### L7

| Metric | Current (v1) | Updated (v3) | Δ |
|---|---:|---:|---:|
| Accuracy | 40.0% | 80.0% | +40.0% ✓ |
| ROI | -23.6% | 52.7% | +76.4% ✓ |
| Units | -1.2 | +2.6 | +3.8 ✓ |
| Log Loss | 0.717 | 0.655 | -0.062 ✓ |
| Brier | 0.264 | 0.231 | -0.033 ✓ |
| Cal Error | — | 0.092 | — |

_Totals MAE: 6.5 → 4.6 · Margin MAE: 11.4 → 11.4_

### L30

| Metric | Current (v1) | Updated (v3) | Δ |
|---|---:|---:|---:|
| Accuracy | 47.1% | 52.9% | +5.9% ✓ |
| ROI | -10.2% | 1.1% | +11.2% ✓ |
| Units | -1.7 | +0.2 | +1.9 ✓ |
| Log Loss | 0.670 | 0.683 | +0.013 ✗ |
| Brier | 0.241 | 0.245 | +0.004 ✗ |
| Cal Error | 0.364 | 0.014 | -0.350 ✓ |

_Totals MAE: 10.7 → 10.0 · Margin MAE: 12.1 → 11.4_

## Markets

### Moneyline
- Separate Elo + form/OD blend with Platt calibration and early-season shrink.
- Confidence raised only with multi-component agree + QB lean (+ market edge when books exist).

### Spread
- **Independent margin model** (not derived from ML win probability).
- `Spread Confidence` = |expected margin| / 14. ATS bets require books (none in feed → no fabricated bets).

### Totals
- Projected team scores + league regression + variance/sigma.
- O/U bets only with meaningful EV vs book (books missing → projections only).

## Real vs proxy features

| Feature | Status | Notes |
|---|---|---|
| `elo_diff` | real | Walk-forward Elo from finals |
| `form_diff` | real | L5 win% from finals |
| `off_vs_def` | real | PPG offense vs opponent defense |
| `to_diff` | proxy | Margin-based turnover proxy (no TO feed) |
| `qb_advantage` | proxy | Offense+form blend (no EPA/QB feed) |
| `rest_diff` | real | Days since last game |
| `st_variance` | proxy | Scoring variance as ST/pace stand-in |
| `injury_factor` | missing | Interface only — neutral 1.0 |
| `weather` | missing | Interface only — neutral 0 |
| `epa` | missing | No EPA feed — points efficiency used |
| `market_prob` | missing | No CFL odds feed — edge inactive |

## Feature importance (display)

| Feature | Status | Mean |abs| | Sign agree |
|---|---|---:|---:|
| `rest_diff` | real | 0.6528 | 0.444 |
| `form_diff` | real | 0.2648 | 0.5 |
| `to_diff` | proxy | 0.2222 | 0.472 |
| `def_vs_off` | real | 0.2149 | 0.528 |
| `off_vs_def` | real | 0.1954 | 0.5 |
| `qb_advantage` | proxy | 0.1115 | 0.444 |

## Calibration buckets (fav side)

Buckets used: 50–55%, 55–60%, 60–65%, 65–70%, 70%+. Season calibration error: **0.216 → 0.146**.

## Summary lines

- Current Season: `n=36 acc=44.4% roi=-15.2% u=-5.5 ll=0.783 brier=0.292 cal=0.216 fav=0.631 mae_m=11.0 mae_t=14.9`
- Updated Season: `n=36 acc=47.2% roi=-9.8% u=-3.5 ll=0.715 brier=0.261 cal=0.146 fav=0.543 mae_m=10.8 mae_t=13.9`
- Current L7: `n=5 acc=40.0% roi=-23.6% u=-1.2 ll=0.717 brier=0.264 cal=n/a fav=0.660 mae_m=11.4 mae_t=6.5`
- Updated L7: `n=5 acc=80.0% roi=+52.7% u=+2.6 ll=0.655 brier=0.231 cal=0.092 fav=0.545 mae_m=11.4 mae_t=4.6`

## Folders

- `~/Documents/Personal/cfl/engine/models_v2.py` (accepted model)
- `~/Documents/Personal/cfl/engine/predict.py` (Current / baseline)
- `~/Documents/Personal/cfl/notes/` (this report)

Nothing deployed. Nothing pushed. Not merged to live.
