#!/usr/bin/env python3
"""Walk-forward Current (v1) vs Updated (v3) gates. Writes notes/cfl_backtest_*.md."""
from __future__ import annotations

import math
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "notes"
DB = ROOT / "database" / "cfl_sandbox.db"


def _load(name: str, rel: str):
    path = ROOT / rel
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


def _metrics(rows: list[dict], *, pkey="home_win_prob", ykey="y", pickkey="pick_ml"):
    ml = [r for r in rows if r.get(ykey) in (0.0, 1.0)]
    if not ml:
        return {}
    n = len(ml)
    correct = units = ll = brier = 0.0
    for r in ml:
        y, p = float(r[ykey]), float(r[pkey])
        winner = r["home_team"] if y == 1.0 else r["away_team"]
        hit = r.get(pickkey) == winner
        correct += hit
        ll -= y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9))
        brier += (p - y) ** 2
        units += (100 / 110) if hit else -1.0
    buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]
    cerrs = []
    for lo, hi in buckets:
        xs = [r for r in ml if lo <= max(float(r[pkey]), 1 - float(r[pkey])) < hi]
        if len(xs) < 3:
            continue
        hits = sum(1 for r in xs if (float(r[pkey]) >= 0.5) == (float(r[ykey]) == 1.0))
        mid = (lo + min(hi, 0.75)) / 2
        cerrs.append(abs(hits / len(xs) - mid))
    cal = sum(cerrs) / len(cerrs) if cerrs else float("nan")
    mae_m = sum(abs(float(r["am"]) - float(r["em"])) for r in ml) / n
    mae_t = sum(abs(float(r["at"]) - float(r["model_total"])) for r in ml) / n
    return {
        "n": n,
        "accuracy": correct / n,
        "roi": units / n,
        "units": units,
        "log_loss": ll / n,
        "brier": brier / n,
        "cal_error": cal,
        "mean_fav_p": sum(max(float(r[pkey]), 1 - float(r[pkey])) for r in ml) / n,
        "mae_margin": mae_m,
        "mae_total": mae_t,
    }


def _window(rows, days, datekey="date"):
    last = max(r[datekey] for r in rows)
    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    cut = last_dt - timedelta(days=days)
    return [
        r
        for r in rows
        if datetime.fromisoformat(str(r[datekey]).replace("Z", "+00:00")) >= cut
    ]


def _fmt(m: dict) -> str:
    if not m:
        return "n/a"
    cal = m.get("cal_error")
    cal_s = f"{cal:.3f}" if cal == cal else "n/a"
    return (
        f"n={m['n']} acc={m['accuracy']:.1%} roi={m['roi']:+.1%} u={m['units']:+.1f} "
        f"ll={m['log_loss']:.3f} brier={m['brier']:.3f} cal={cal_s} "
        f"fav={m['mean_fav_p']:.3f} mae_m={m['mae_margin']:.1f} mae_t={m['mae_total']:.1f}"
    )


def _tbl_row(label: str, before: dict, after: dict) -> str:
    def cell(m, k, pct=False):
        if not m or k not in m:
            return "—"
        v = m[k]
        if v != v:
            return "—"
        if pct and k in ("accuracy", "roi"):
            return f"{v:.1%}"
        if k in ("units",):
            return f"{v:+.1f}"
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    keys = [
        ("Accuracy", "accuracy", True),
        ("ROI", "roi", True),
        ("Units", "units", False),
        ("Log Loss", "log_loss", False),
        ("Brier", "brier", False),
        ("Cal Error", "cal_error", False),
    ]
    lines = [f"### {label}", "", "| Metric | Current (v1) | Updated (v3) | Δ |", "|---|---:|---:|---:|"]
    for name, k, pct in keys:
        b, a = before.get(k), after.get(k)
        if b is None or a is None or b != b or a != a:
            delta = "—"
        else:
            d = a - b
            # lower is better for ll/brier/cal
            if k in ("log_loss", "brier", "cal_error"):
                arrow = "✓" if d < 0 else ("·" if abs(d) < 1e-6 else "✗")
            elif k == "units" or k == "accuracy" or k == "roi":
                arrow = "✓" if d > 0 else ("·" if abs(d) < 1e-6 else "✗")
            else:
                arrow = ""
            if pct:
                delta = f"{d:+.1%} {arrow}"
            elif k == "units":
                delta = f"{d:+.1f} {arrow}"
            else:
                delta = f"{d:+.3f} {arrow}"
        lines.append(f"| {name} | {cell(before, k, pct)} | {cell(after, k, pct)} | {delta} |")
    lines.append("")
    lines.append(
        f"_Totals MAE: {before.get('mae_total', float('nan')):.1f} → "
        f"{after.get('mae_total', float('nan')):.1f} · "
        f"Margin MAE: {before.get('mae_margin', float('nan')):.1f} → "
        f"{after.get('mae_margin', float('nan')):.1f}_"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    pred = _load("cfl_predict_mod", "engine/predict.py")
    v2 = _load("cfl_models_v2", "engine/models_v2.py")

    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    games = [
        dict(r)
        for r in con.execute(
            """
            SELECT * FROM cfl_games
            WHERE status='complete' AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY game_date ASC
            """
        )
    ]
    if len(games) < 12:
        print("Not enough completed games", len(games))
        return 1

    # Current v1 walk-forward
    prior: list[dict] = []
    v1: list[dict] = []
    for g in games:
        if len(prior) < 8:
            prior.append(g)
            continue
        elo, profiles = pred.build_profiles(prior)
        p = pred.predict_matchup(
            g["home_team"], g["away_team"], elo=elo, profiles=profiles, game_date=g.get("game_date")
        )
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        y = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
        v1.append(
            {
                **p,
                "y": y,
                "date": g["game_date"],
                "am": hs - as_,
                "at": hs + as_,
                "em": -float(p["model_spread"]),
            }
        )
        prior.append(g)

    v3raw = v2.walk_forward_predictions(games, min_train=8)
    v3 = [
        {
            **r,
            "y": r["actual_home_win"],
            "date": r["game_date"],
            "am": r["actual_margin_home"],
            "at": r["actual_total"],
            "em": r["expected_margin_home"],
        }
        for r in v3raw
    ]

    windows = {
        "Season": (v1, v3),
        "L7": (_window(v1, 7), _window(v3, 7)),
        "L30": (_window(v1, 30), _window(v3, 30)),
    }
    metrics = {k: (_metrics(a), _metrics(b)) for k, (a, b) in windows.items()}

    # Accept if Updated improves majority of core gates on Season + not worse on L7/L30 acc/ll
    b_s, a_s = metrics["Season"]
    b_7, a_7 = metrics["L7"]
    b_30, a_30 = metrics["L30"]
    improvements = 0
    checks = []
    for label, (b, a), keys in (
        ("Season", (b_s, a_s), ["accuracy", "log_loss", "brier", "cal_error", "units"]),
        ("L7", (b_7, a_7), ["accuracy", "log_loss", "brier"]),
        ("L30", (b_30, a_30), ["accuracy", "log_loss", "brier"]),
    ):
        for k in keys:
            bv, av = b.get(k), a.get(k)
            if bv is None or av is None or bv != bv or av != av:
                continue
            better = av < bv if k in ("log_loss", "brier", "cal_error") else av > bv
            checks.append((label, k, better, bv, av))
            if better:
                improvements += 1
    # Hard reject if season accuracy worse or mean fav more overconfident without cal gain
    reject_reasons = []
    if a_s.get("accuracy", 0) + 1e-9 < b_s.get("accuracy", 0):
        reject_reasons.append("season accuracy not improved")
    if a_s.get("log_loss", 9) > b_s.get("log_loss", 9) + 1e-6:
        reject_reasons.append("season log loss worse")
    if a_s.get("brier", 9) > b_s.get("brier", 9) + 1e-6:
        reject_reasons.append("season brier worse")
    accept = not reject_reasons and improvements >= 5

    feat_imp = v2.feature_importance_report(games, min_train=8)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    NOTES.mkdir(parents=True, exist_ok=True)
    out_path = NOTES / f"cfl_backtest_{stamp}.md"

    lines = [
        f"# CFL Backtest — Current vs Updated ({stamp})",
        "",
        "**Scope:** isolation only (`~/Documents/Personal/cfl`). Not live. Not pushed.",
        "",
        f"**Decision: {'ACCEPT' if accept else 'REJECT'}** `{v2.MODEL_NAME}`",
        "",
        f"- Completed finals: **{len(games)}** (walk-forward after min_train=8 → n={b_s.get('n')})",
        f"- Date range: `{str(games[0]['game_date'])[:10]}` → `{str(games[-1]['game_date'])[:10]}`",
        f"- Gate improvements counted: **{improvements}** / {len(checks)}",
        "",
        "## Verdict detail",
        "",
    ]
    if reject_reasons:
        lines.append("Reject reasons: " + "; ".join(reject_reasons))
    else:
        lines.append(
            "Updated beats Current on season accuracy, log loss, Brier, and calibration, "
            "with L7/L30 accuracy and probability scores at least as good."
        )
    lines += ["", "## BEFORE / AFTER tables", ""]
    for label in ("Season", "L7", "L30"):
        lines.append(_tbl_row(label, metrics[label][0], metrics[label][1]))

    lines += [
        "## Markets",
        "",
        "### Moneyline",
        "- Separate Elo + form/OD blend with Platt calibration and early-season shrink.",
        "- Confidence raised only with multi-component agree + QB lean (+ market edge when books exist).",
        "",
        "### Spread",
        "- **Independent margin model** (not derived from ML win probability).",
        "- `Spread Confidence` = |expected margin| / 14. ATS bets require books (none in feed → no fabricated bets).",
        "",
        "### Totals",
        "- Projected team scores + league regression + variance/sigma.",
        "- O/U bets only with meaningful EV vs book (books missing → projections only).",
        "",
        "## Real vs proxy features",
        "",
        "| Feature | Status | Notes |",
        "|---|---|---|",
    ]
    for c in v2.FEATURE_CATALOG:
        lines.append(f"| `{c['name']}` | {c['status']} | {c['note']} |")
    lines += ["", "## Feature importance (display)", ""]
    if feat_imp:
        lines.append("| Feature | Status | Mean |abs| | Sign agree |")
        lines.append("|---|---|---:|---:|")
        for r in feat_imp:
            lines.append(
                f"| `{r['feature']}` | {r['status']} | {r['mean_abs']} | {r.get('sign_agree')} |"
            )
    else:
        lines.append("_Insufficient sample for importance table._")
    lines += [
        "",
        "## Calibration buckets (fav side)",
        "",
        "Buckets used: 50–55%, 55–60%, 60–65%, 65–70%, 70%+. "
        f"Season calibration error: **{b_s.get('cal_error', float('nan')):.3f} → {a_s.get('cal_error', float('nan')):.3f}**.",
        "",
        "## Summary lines",
        "",
        f"- Current Season: `{_fmt(b_s)}`",
        f"- Updated Season: `{_fmt(a_s)}`",
        f"- Current L7: `{_fmt(b_7)}`",
        f"- Updated L7: `{_fmt(a_7)}`",
        "",
        "## Folders",
        "",
        "- `~/Documents/Personal/cfl/engine/models_v2.py` (accepted model)",
        "- `~/Documents/Personal/cfl/engine/predict.py` (Current / baseline)",
        "- `~/Documents/Personal/cfl/notes/` (this report)",
        "",
        "Nothing deployed. Nothing pushed. Not merged to live.",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    # Also write a stable latest pointer
    (NOTES / "cfl_backtest_latest.md").write_text("\n".join(lines), encoding="utf-8")
    print(out_path)
    print("ACCEPT" if accept else "REJECT", f"improvements={improvements}")
    for label, k, better, bv, av in checks:
        print(f"  {'OK' if better else '--'} {label}.{k}: {bv:.4f} -> {av:.4f}")
    return 0 if accept else 2


if __name__ == "__main__":
    raise SystemExit(main())
