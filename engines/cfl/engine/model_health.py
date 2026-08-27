"""CFL Model Health content — shared MLB analytics chrome classes only.

No vendor/IP labels. Used by sandbox hub `/cfl/model-health` and results analytics.
"""
from __future__ import annotations

import math
import sqlite3
import sys
import types
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = Path(__import__("os").environ.get("CFL_SANDBOX_DB") or (ROOT / "database" / "cfl_sandbox.db"))


def _load(name: str, rel: str):
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / rel
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _load_completed() -> list[dict[str, Any]]:
    with _connect() as con:
        rows = con.execute(
            """
            SELECT * FROM cfl_games
            WHERE lower(status) IN ('complete','final','closed')
              AND home_score IS NOT NULL AND away_score IS NOT NULL
            ORDER BY game_date ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _units(w: int, l: int) -> float:
    return round(w * (100 / 110) - l, 1)


def _window_rows(rows: list[dict], days: int | None) -> list[dict]:
    if days is None:
        return rows
    if not rows:
        return []
    last = max(r.get("game_date") or "" for r in rows)
    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    cut = last_dt - timedelta(days=days)
    out = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r.get("game_date")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt >= cut:
            out.append(r)
    return out


def _streak(results: list[bool]) -> dict[str, Any]:
    if not results:
        return {"label": "—", "n": 0}
    cur = results[-1]
    n = 0
    for ok in reversed(results):
        if ok == cur:
            n += 1
        else:
            break
    return {"label": ("W" if cur else "L") + str(n), "n": n, "win": cur}


def _market_block(rows: list[dict], market: str) -> dict[str, Any]:
    """Grade ML / spread-sign / totals-vs-league-proxy for health cards."""
    results: list[bool] = []
    probs: list[float] = []
    confs: list[float] = []
    for r in rows:
        y = r.get("actual_home_win")
        if y not in (0.0, 1.0):
            continue
        if market == "ml":
            winner = r["home_team"] if y == 1.0 else r["away_team"]
            ok = r.get("pick_ml") == winner
            results.append(bool(ok))
            probs.append(float(r.get("home_win_prob") or 0.5))
            confs.append(float(r.get("confidence") or 0))
        elif market == "spread":
            em = float(r.get("expected_margin_home") or 0)
            am = float(r.get("actual_margin_home") or 0)
            if abs(em) < 1.0:
                continue
            ok = (em > 0) == (am > 0)
            results.append(ok)
            confs.append(float(r.get("spread_confidence") or 0))
        else:  # totals — directional vs model total (projection error sign)
            mt = float(r.get("model_total") or 0)
            at = float(r.get("actual_total") or 0)
            # "hit" if abs error within 1 sigma (honest tightness), else lean direction vs 49.5 proxy
            sigma = float(r.get("total_sigma") or 10)
            ok = abs(at - mt) <= sigma
            results.append(ok)
            confs.append(min(0.95, abs(mt - 49.5) / 14.0))

    w = sum(1 for x in results if x)
    l = sum(1 for x in results if not x)
    n = w + l
    pct = round(100.0 * w / n, 1) if n else None
    # conf dist buckets
    buckets = {"50-55": 0, "55-60": 0, "60-65": 0, "65-70": 0, "70+": 0}
    for c in confs:
        fav = max(c, 1 - c) if c <= 1 else c
        # confidence already 0-1 scale of conviction
        p = fav if fav <= 1 else fav / 100.0
        if p < 0.55:
            buckets["50-55"] += 1
        elif p < 0.60:
            buckets["55-60"] += 1
        elif p < 0.65:
            buckets["60-65"] += 1
        elif p < 0.70:
            buckets["65-70"] += 1
        else:
            buckets["70+"] += 1

    agree_vals = [int(r.get("agree_n") or 0) for r in rows if r.get("agree_n") is not None]
    consensus = round(sum(agree_vals) / len(agree_vals), 2) if agree_vals else None

    return {
        "w": w,
        "l": l,
        "n": n,
        "pct": pct,
        "record": f"{w}-{l}" if n else "—",
        "units": _units(w, l) if n else None,
        "roi": round((_units(w, l) / n), 3) if n else None,
        "streak": _streak(results),
        "confidence_dist": buckets,
        "consensus": consensus,
        "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
    }


def build_model_health() -> dict[str, Any]:
    v2 = _load("cfl_models_v2_health", "engine/models_v2.py")
    games = _load_completed()
    rows = v2.walk_forward_predictions(games, min_train=8)
    feat = v2.feature_importance_report(games, min_train=8)

    def pack(days: int | None) -> dict[str, Any]:
        rr = _window_rows(rows, days)
        return {
            "ml": _market_block(rr, "ml"),
            "spread": _market_block(rr, "spread"),
            "totals": _market_block(rr, "totals"),
        }

    # Best performing windows from ML
    def best_row(block: dict) -> dict | None:
        ml = block.get("ml") or {}
        if not ml.get("n"):
            return None
        return {
            "name": "Prediction Lab",
            "pct": ml.get("pct"),
            "record": ml.get("record"),
            "units": ml.get("units"),
        }

    season = pack(None)
    l7 = pack(7)
    l30 = pack(30)
    # today = last slate date
    last_dates = sorted({str(r.get("game_date") or "")[:10] for r in rows if r.get("game_date")})
    today_rows = [r for r in rows if str(r.get("game_date") or "")[:10] == (last_dates[-1] if last_dates else "")]
    today = {
        "ml": _market_block(today_rows, "ml"),
        "spread": _market_block(today_rows, "spread"),
        "totals": _market_block(today_rows, "totals"),
    }

    return {
        "ok": True,
        "model_name": getattr(v2, "MODEL_NAME", "cfl_v3_cal_blend"),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "best_performing": {
            "today": best_row(today),
            "last_7": best_row(l7),
            "season": best_row(season),
        },
        "windows": {
            "today": today,
            "last_7": l7,
            "last_30": l30,
            "season": season,
        },
        "feature_importance": feat,
        "feature_catalog": getattr(v2, "FEATURE_CATALOG", []),
        "notes": {
            "market_odds": "missing",
            "injury": "missing",
            "weather": "missing",
            "epa": "missing",
        },
    }


def _card(label: str, block: dict[str, Any]) -> str:
    n = int(block.get("n") or 0)
    pct = block.get("pct")
    acc = f"{pct}%" if pct is not None else ("—" if n <= 0 else "0%")
    rec = block.get("record") or ("—" if n <= 0 else "0-0")
    units = block.get("units")
    units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ("—" if n <= 0 else "+0.0u")
    streak = (block.get("streak") or {}).get("label") or "—"
    cons = block.get("consensus")
    cons_s = f"{cons}" if cons is not None else "—"
    dist = block.get("confidence_dist") or {}
    dist_s = " · ".join(f"{k}:{v}" for k, v in dist.items() if v)
    return (
        f'<div class="pl-analytics-card">'
        f'<div class="pl-analytics-k">{label}</div>'
        f'<div class="pl-analytics-v">{acc}</div>'
        f'<div class="pl-analytics-sub">Record {rec} · Units {units_s} · Streak {streak}</div>'
        f'<div class="pl-analytics-sub">L window n={n} · Consensus {cons_s}</div>'
        f'<div class="pl-analytics-sub">Conf {dist_s or "—"}</div>'
        f"</div>"
    )


def model_health_page() -> str:
    """HTML fragment/page using shared pl-analytics-* classes (MLB chrome language)."""
    data = build_model_health()
    best = data.get("best_performing") or {}
    windows = data.get("windows") or {}
    feat = data.get("feature_importance") or []

    def best_card(label: str, row: dict | None) -> str:
        if not row:
            return (
                f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
                f'<div class="pl-analytics-v muted">—</div></div>'
            )
        units = row.get("units")
        units_s = f"{units:+.1f}u" if isinstance(units, (int, float)) else ""
        return (
            f'<div class="pl-analytics-card"><div class="pl-analytics-k">{label}</div>'
            f'<div class="pl-analytics-name">{row.get("name") or "—"}</div>'
            f'<div class="pl-analytics-v">{row.get("pct")}%</div>'
            f'<div class="pl-analytics-sub">{row.get("record") or ""}'
            f'{" · " + units_s if units_s else ""}</div></div>'
        )

    feat_rows = "".join(
        f"<tr><td>{r.get('feature')}</td><td>{r.get('status')}</td>"
        f"<td>{r.get('mean_abs')}</td><td>{r.get('sign_agree')}</td></tr>"
        for r in feat
    ) or "<tr><td colspan=4>Insufficient sample</td></tr>"

    sections = []
    for wlabel, wkey in (("Today", "today"), ("Last 7", "last_7"), ("Last 30", "last_30"), ("Season", "season")):
        w = windows.get(wkey) or {}
        sections.append(
            f'<h3 class="pl-analytics-title">Model Health — {wlabel}</h3>'
            f'<div class="pl-analytics-grid">'
            f'{_card("Moneyline", w.get("ml") or {})}'
            f'{_card("Spread", w.get("spread") or {})}'
            f'{_card("Totals", w.get("totals") or {})}'
            f"</div>"
        )

    body = f"""
    <section class="pl-mlb-analytics" aria-label="CFL model health">
      <h2 class="pl-analytics-title">Best Performing Model</h2>
      <div class="pl-analytics-grid">
        {best_card("Today", best.get("today"))}
        {best_card("Last 7", best.get("last_7"))}
        {best_card("Season", best.get("season"))}
      </div>
      {''.join(sections)}
      <h3 class="pl-analytics-title">Feature Importance</h3>
      <div class="pl-analytics-card" style="text-align:left">
        <table style="width:100%;border-collapse:collapse;font-size:.9rem">
          <thead><tr><th align="left">Feature</th><th align="left">Status</th>
          <th align="right">Mean |abs|</th><th align="right">Sign agree</th></tr></thead>
          <tbody>{feat_rows}</tbody>
        </table>
      </div>
      <p class="pl-analytics-sub" style="margin-top:12px;text-align:center">
        Walk-forward health from locked pre-game ratings. Missing feeds stay neutral — no fabricated books.
      </p>
    </section>
    <style>
      .pl-mlb-analytics{{margin:18px 0 22px;padding:16px;border:1px solid rgba(15,23,42,.12);border-radius:12px;background:#f8fafc}}
      .pl-analytics-title{{margin:16px 0 10px;font-size:1.05rem;color:#0f172a;text-align:center}}
      .pl-analytics-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px}}
      .pl-analytics-card{{background:#fff;border:1px solid rgba(15,23,42,.1);border-radius:10px;padding:12px;text-align:center}}
      .pl-analytics-k{{font-size:.75rem;color:#475569;margin-bottom:4px}}
      .pl-analytics-name{{font-weight:700;color:#0f172a;margin-bottom:2px}}
      .pl-analytics-v{{font-size:1.45rem;font-weight:800;color:#0f172a}}
      .pl-analytics-v.muted{{color:#94a3b8}}
      .pl-analytics-sub{{font-size:.8rem;color:#475569;margin-top:4px}}
      @media(max-width:720px){{.pl-analytics-grid{{grid-template-columns:1fr}}}}
    </style>
    """
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
        "<title>CFL Model Health | Prediction Lab</title>"
        '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
        '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>'
        '<link rel="stylesheet" href="/static/css/sports-chrome.css"/>'
        "</head><body class='research-site' data-sandbox-sports-chrome='1'>"
        f"<div class='container'><h1>CFL Model Health</h1>"
        f"<p><a href='/cfl/results'>Results</a> · <a href='/cfl/'>Picks</a></p>"
        f"{body}</div></body></html>"
    )


def api_model_health() -> dict[str, Any]:
    return build_model_health()
