"""Shared PL statistical prop calculations; no network, randomness or database access."""
import hashlib
import json
import math
import statistics

VERSION = "pl-props-2"
COUNT_MARKETS = {
    "points", "rebounds", "assists", "threes", "steals", "blocks", "hits", "runs",
    "rbis", "home_runs", "walks", "strikeouts", "receptions", "shots", "shots_on_goal",
    "shots_on_target", "goals", "touchdowns", "tds",
}
YARD_MARKETS = {"passing_yards", "rushing_yards", "receiving_yards"}


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _cdf(value, mean, sigma):
    return 0.5 * (1 + math.erf((value - mean) / (sigma * math.sqrt(2))))


def _probabilities(over, under, push):
    values = [max(0.0, min(1.0, value)) for value in (over, under, push)]
    total = sum(values)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid probability distribution")
    return dict(zip(("over", "under", "push"), (value / total for value in values)))


def normal_probabilities(mean, variance, line, nonnegative=True):
    sigma = math.sqrt(max(variance, 0.25))
    low = _cdf(-0.5, mean, sigma) if nonnegative else 0.0
    def cdf(boundary):
        if nonnegative and boundary < -0.5:
            return 0.0
        return max(0.0, min(1.0, (_cdf(boundary, mean, sigma) - low) / (1-low)))
    below = cdf(math.ceil(line) - 0.5)
    through = cdf(math.floor(line) + 0.5)
    return _probabilities(1-through, below, max(0, through-below))


def count_probabilities(mean, variance, line):
    """Negative-binomial predictive counts; retain at least Poisson variance."""
    mean = max(mean, 1e-8)
    variance = max(variance, mean * 1.05)
    shape = mean * mean / (variance - mean)
    success = shape / (shape + mean)
    def cdf(k):
        if k < 0:
            return 0.0
        if k > 10000:
            raise ValueError("Count threshold outside supported calculation range")
        term = math.exp(shape * math.log(success))
        total = term
        for value in range(1, k+1):
            term *= (value - 1 + shape) / value * (1-success)
            total += term
        return min(1.0, total)
    below = cdf(math.ceil(line)-1)
    through = cdf(math.floor(line))
    return _probabilities(1-through, below, max(0, through-below))


def fair_odds(probability, push):
    """Fair price conditional on a win/loss; pushes return the stake."""
    if not 0 <= push < 1:
        return None
    chance = probability / (1-push)
    if not 0 < chance < 1:
        return None
    return round(-100*chance/(1-chance)) if chance >= 0.5 else round(100*(1-chance)/chance)


def calculate(player_stats, sport, prop_type, real_line=None):
    if prop_type not in COUNT_MARKETS | YARD_MARKETS:
        raise ValueError("This prop type has no supported probability calculation")
    values = player_stats.get("recent_form")
    if values is None:
        values = (player_stats.get("stat_history") or {}).get(prop_type)
    if not isinstance(values, list) or len(values) < 5:
        raise ValueError("At least five verified recent appearances are required")
    values = [finite(value) for value in values[:20]]
    if any(value is None for value in values):
        raise ValueError("Recent stats contain missing or invalid values")
    count = prop_type in COUNT_MARKETS
    if any(not value.is_integer() or (count and value < 0) for value in values):
        raise ValueError("Stat values do not match this market's units")
    # Inputs are per-game observations: never apply NBA minutes/usage scales to them.
    mean = 0.7 * statistics.mean(values[:5]) + 0.3 * statistics.mean(values[:10])
    n = len(values)
    predictive_mean = mean + (0.5/n if count else 0)
    empirical_variance = statistics.variance(values)
    variance = max(empirical_variance, predictive_mean * (1+1/n) if count else 1.0)
    pl_line = round(mean*2)/2
    if count:
        pl_line = max(0.5, pl_line)
    line = finite(real_line) if real_line is not None else pl_line
    if line is None:
        raise ValueError("Line is invalid")
    distributions = [
        ("pl_projection", "PL Projection", normal_probabilities(predictive_mean, variance, line, count)),
    ]
    if count:
        distributions.append(("pl_count", "PL Count", count_probabilities(predictive_mean, variance, line)))
    forecasts = []
    for identity, name, probs in distributions:
        side = "OVER" if probs["over"] > probs["under"] else "UNDER" if probs["under"] > probs["over"] else None
        forecasts.append({"model_id": identity, "model_name": name, "model_version": VERSION,
                          "probabilities": probs, "side": side,
                          "probability": 100*probs[side.lower()] if side else None,
                          "projection": predictive_mean, "sample": n})
    average = {key: statistics.mean(row["probabilities"][key] for row in forecasts)
               for key in ("over", "under", "push")}
    side = "OVER" if average["over"] > average["under"] else "UNDER" if average["under"] > average["over"] else None
    probability = average[side.lower()] if side else None
    strong = bool(side and n >= 10 and probability > 0.61 and all(row["side"] == side for row in forecasts))
    return {"available": True, "model_version": VERSION, "line": line, "fair_line": pl_line,
            "line_source": "provided_market" if real_line is not None else "pl_model",
            "projection": predictive_mean, "model_forecasts": forecasts, "probabilities": average,
            "pick": side, "pick_probability": probability*100 if probability is not None else None,
            "strong_pick": strong,
            "odds_over": fair_odds(average["over"], average["push"]),
            "odds_under": fair_odds(average["under"], average["push"]),
            "sample": n, "input_values": values, "input_hash": hashlib.sha256(json.dumps(values).encode()).hexdigest()}
