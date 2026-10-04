"""
MLB Over/Under cold-streak calibration layer.

Runs AFTER the Prediction Lab total projection is generated but BEFORE the live
MLB O/U pick is displayed. It NEVER touches historical grading, moneyline, or
spread — it only adjusts the *live* O/U pick + confidence when the totals model
is running cold.

Rules (see project spec):
  * Inverse mode: last-30 graded O/U win rate < 48% over >= 30 picks AND season
    win rate >= 52%. Per-pick, only flip when |projected edge| >= 1.5 runs.
  * Caution mode (safer alternative): last-7-day O/U win rate < 50% and inverse
    mode did not qualify -> hold the pick, drop confidence by 5.
  * Inverse flip -> drop confidence by 8 (floor 52).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

log = logging.getLogger("mlb_ou_calibration")

# Thresholds
INVERSE_MIN_SAMPLE = 30      # need at least this many graded picks
INVERSE_RECENT_MAX = 48.0    # last-30 win rate below this => cold
INVERSE_SEASON_MIN = 52.0    # only invert if the season is still strong
INVERSE_MIN_EDGE = 1.5       # only flip a pick whose projected edge >= this (runs)
CAUTION_RECENT_MAX = 50.0    # last-7-day below this => caution
CONF_FLOOR = 52
INVERSE_CONF_DROP = 8
CAUTION_CONF_DROP = 5

BADGE_TEXT = "O/U Cold Streak Adjustment Active"


def compute_ou_performance(results, today=None):
    """
    results: iterable of (date_str 'YYYY-MM-DD', correct: bool) for GRADED,
             non-push MLB totals (most recent ordering not required).
    Returns the three windows used by the rules.
    """
    today = today or datetime.now()
    parsed = []
    for date_str, correct in results:
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        parsed.append((dt, bool(correct)))
    parsed.sort(key=lambda x: x[0])

    def rate(items):
        n = len(items)
        if n == 0:
            return None, 0
        wins = sum(1 for _, c in items if c)
        return round(wins / n * 100.0, 1), n

    season_rate, season_n = rate(parsed)
    last30_rate, last30_n = rate(parsed[-30:])
    cutoff = today - timedelta(days=7)
    last7 = [x for x in parsed if x[0] >= cutoff]
    recent7_rate, recent7_n = rate(last7)

    return {
        "season_rate": season_rate, "season_n": season_n,
        "last30_rate": last30_rate, "last30_n": last30_n,
        "recent7_rate": recent7_rate, "recent7_n": recent7_n,
    }


def evaluate_ou_mode(perf: dict) -> dict:
    """Decide 'inverse' | 'caution' | 'normal' from the performance windows."""
    last30_rate = perf.get("last30_rate")
    last30_n = perf.get("last30_n") or 0
    season_rate = perf.get("season_rate")
    recent7_rate = perf.get("recent7_rate")

    if (last30_rate is not None and last30_rate < INVERSE_RECENT_MAX
            and last30_n >= INVERSE_MIN_SAMPLE
            and season_rate is not None and season_rate >= INVERSE_SEASON_MIN):
        return {
            "mode": "inverse",
            "recent_win_rate": last30_rate,
            "reason": (f"Inverse mode: last-30 O/U {last30_rate:.1f}% "
                       f"(< {INVERSE_RECENT_MAX:.0f}%) over {last30_n} picks; "
                       f"season {season_rate:.1f}% (>= {INVERSE_SEASON_MIN:.0f}%)."),
        }

    if recent7_rate is not None and recent7_rate < CAUTION_RECENT_MAX:
        return {
            "mode": "caution",
            "recent_win_rate": recent7_rate,
            "reason": (f"Caution mode: last-7-day O/U {recent7_rate:.1f}% "
                       f"(< {CAUTION_RECENT_MAX:.0f}%); confidence reduced."),
        }

    return {
        "mode": "normal",
        "recent_win_rate": last30_rate,
        "reason": "Normal: O/U performance within tolerance.",
    }


def _flip_pick(pick: str) -> str:
    return {"OVER": "UNDER", "UNDER": "OVER"}.get(pick, pick)


def _flip_label(label):
    if not label:
        return label
    if label.startswith("Over"):
        return "Under" + label[len("Over"):]
    if label.startswith("Under"):
        return "Over" + label[len("Under"):]
    return label


def apply_mlb_ou_calibration(games, perf: dict) -> dict:
    """
    Mutate live MLB game cards in place. Each card is expected to carry:
      total_pick        : 'OVER' | 'UNDER' | 'PUSH' | None
      total_edge        : projected total edge in runs (signed)
      total_confidence  : numeric confidence (optional)
      total_pick_label  : e.g. 'Over 8.5' (rewritten on flip)
    Returns a summary used for the front-end badge + logging.
    """
    decision = evaluate_ou_mode(perf)
    mode = decision["mode"]
    recent = decision["recent_win_rate"]
    flips = 0
    cautioned = 0

    for g in games:
        original = g.get("total_pick")
        if original not in ("OVER", "UNDER"):
            continue

        g["original_total_pick"] = original
        g["ou_recent_win_rate"] = recent
        g["ou_inverse_mode"] = False
        g["totals_caution_mode"] = False
        g["ou_reason"] = decision["reason"]
        conf = g.get("total_confidence")
        edge = abs(g.get("total_edge") or 0.0)

        if mode == "inverse" and edge >= INVERSE_MIN_EDGE:
            adjusted = _flip_pick(original)
            g["adjusted_total_pick"] = adjusted
            g["total_pick"] = adjusted
            g["total_pick_label"] = _flip_label(g.get("total_pick_label"))
            g["ou_inverse_mode"] = True
            if conf is not None:
                g["total_confidence"] = max(CONF_FLOOR, conf - INVERSE_CONF_DROP)
            flips += 1
            log.warning("[MLB O/U FLIP] game=%s %s->%s edge=%.2f recent=%.1f%% :: %s",
                        g.get("game_id"), original, adjusted, edge,
                        recent if recent is not None else -1.0, decision["reason"])
        elif mode == "inverse":
            # Cold streak qualifies, but this pick's edge is too small to flip.
            g["adjusted_total_pick"] = original
            g["totals_caution_mode"] = True
            g["ou_reason"] = (f"Cold streak active but edge {edge:.2f} < {INVERSE_MIN_EDGE} runs "
                              f"— pick held, confidence reduced.")
            if conf is not None:
                g["total_confidence"] = max(CONF_FLOOR, conf - CAUTION_CONF_DROP)
            cautioned += 1
            log.info("[MLB O/U HOLD] game=%s %s edge=%.2f < %.1f — held, conf reduced",
                     g.get("game_id"), original, edge, INVERSE_MIN_EDGE)
        elif mode == "caution":
            g["adjusted_total_pick"] = original
            g["totals_caution_mode"] = True
            if conf is not None:
                g["total_confidence"] = max(CONF_FLOOR, conf - CAUTION_CONF_DROP)
            cautioned += 1
        else:
            g["adjusted_total_pick"] = original

    summary = {
        "ou_inverse_mode": flips > 0,
        "totals_caution_mode": (mode == "caution") or (mode == "inverse" and cautioned > 0),
        "ou_recent_win_rate": recent,
        "ou_reason": decision["reason"],
        "badge_text": BADGE_TEXT if (flips > 0 or cautioned > 0) else None,
        "flips": flips,
        "cautioned": cautioned,
        "mode": mode,
    }
    log.info("[MLB O/U calibration] mode=%s flips=%d cautioned=%d last30=%s/%s season=%s/%s recent7=%s",
             mode, flips, cautioned, perf.get("last30_rate"), perf.get("last30_n"),
             perf.get("season_rate"), perf.get("season_n"), perf.get("recent7_rate"))
    return summary
