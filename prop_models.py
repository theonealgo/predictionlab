"""Player-stat models, distinct from the site's team-win model artifacts."""
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sqlite3
import sys
from zoneinfo import ZoneInfo

from odds_engine.model.prop_math import COUNT_MARKETS, normal_probabilities
from prop_forecast_store import _aware

VERSION = "player-props-v1"
REGISTRY = (("glicko2", "Grinder2"), ("trueskill", "Takedown"), ("elo", "Edge"),
            ("xgboost", "XSharp"), ("ensemble", "Sharp Consensus"), ("efficiency", "Efficiency"))
ET = ZoneInfo("America/New_York")
_TRAINED = {}


@lru_cache(maxsize=1)
def _rating_types():
    path = Path(__file__).resolve().parent/"prediction_system_v2/rating_engines.py"
    spec = importlib.util.spec_from_file_location("_pl_prop_rating_algorithms", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def observations(player, prop_type, as_of):
    if as_of.tzinfo is None:
        raise ValueError("Forecast cutoff must be timezone-aware")
    result = {}
    for row in (player.get("stat_observations") or {}).get(prop_type) or []:
        stamp = _aware(row.get("date"))
        try:
            value = float(row["actual"])
        except (KeyError, ValueError, TypeError):
            continue
        if not stamp or stamp.astimezone(ET).date() >= as_of.astimezone(ET).date() \
                or not math.isfinite(value) or not value.is_integer() \
                or (prop_type in COUNT_MARKETS and value < 0) or not str(row.get("event_id") or "").isdigit():
            continue
        result[str(row["event_id"])] = {"date": stamp.astimezone(timezone.utc).isoformat(), "actual": value,
                                      "event_id": str(row["event_id"])}
    return sorted(result.values(), key=lambda row: (row["date"], row["event_id"]))[-60:]


def _exceed(values, boundary, family, nonnegative):
    if nonnegative and boundary < 0:
        return 1.0
    if family == "elo":
        rating = 1500.0
        for value in values:
            expected = 1/(1+10**((1500-rating)/400))
            rating += 20*(int(value > boundary)-expected)
        return 1/(1+10**((1500-rating)/400))
    types = _rating_types()
    if family == "glicko2":
        model = types.Glicko2Rating()
        for value in values:
            model.ratings["threshold"] = types.Glicko2Player(mu=1500, sigma=0.01)
            model.update_ratings("player", "threshold", int(value > boundary))
        model.ratings["threshold"] = types.Glicko2Player(mu=1500, sigma=0.01)
        return model.win_probability("player", "threshold")[0]
    if family == "trueskill":
        model = types.TrueSkillRating()
        for value in values:
            model.ratings["threshold"] = (25.0, 0.001)
            winner, loser = ("player", "threshold") if value > boundary else ("threshold", "player")
            model.update_ratings(winner, loser)
        model.ratings["threshold"] = (25.0, 0.001)
        return float(model.win_probability("player", "threshold"))
    raise ValueError("Unknown player-stat rating family")


def _distribution(exceed, line):
    below_boundary, above_boundary = math.ceil(line)-0.5, math.floor(line)+0.5
    over = float(exceed(above_boundary))
    under = 1-float(exceed(below_boundary))
    push = 1-over-under
    if any(not math.isfinite(value) or value < -1e-7 or value > 1+1e-7 for value in (over, under, push)):
        raise ValueError("The model did not produce a consistent integer-stat distribution")
    values = [max(0, min(1, value)) for value in (over, under, push)]
    return dict(zip(("over", "under", "push"), (value/sum(values) for value in values)))


def _features(values, line):
    mean5, mean10 = statistics.mean(values[-5:]), statistics.mean(values[-10:])
    sigma = max(statistics.stdev(values[-10:]) if len(values) > 1 else 1, 0.5)
    return [mean5, mean10, sigma, values[-1], statistics.mean(values[-3:]),
            line, (line-mean5)/sigma, min(len(values), 60)]


def train_xgboost(league, prop_type, players, as_of):
    histories = [(str(player["player_id"]), observations(player, prop_type, as_of)) for player in players]
    fingerprint = hashlib.sha256(json.dumps([VERSION, league, prop_type, sorted(histories)], sort_keys=True).encode()).hexdigest()
    if fingerprint in _TRAINED:
        return _TRAINED[fingerprint]
    dataset = []
    count = prop_type in COUNT_MARKETS
    for player_id, history in histories:
        for target in history:
            target_day = _aware(target["date"]).astimezone(ET).date()
            prior = [row["actual"] for row in history
                     if _aware(row["date"]).astimezone(ET).date() < target_day]
            if len(prior) < 5:
                continue
            mean = statistics.mean(prior[-5:])
            sigma = max(statistics.stdev(prior[-10:]), 0.5)
            thresholds = {math.floor(mean+z*sigma)+0.5 for z in (-1, -0.5, 0, 0.5, 1)}
            for threshold in sorted(thresholds):
                if count and threshold < 0:
                    continue
                features = _features(prior, threshold)
                baseline = normal_probabilities(mean, sigma*sigma, threshold, count)["over"]
                dataset.append((target_day.isoformat(), features,
                                int(target["actual"] > threshold), baseline))
    dates = sorted({row[0] for row in dataset})
    artifact = {"model": None, "fingerprint": fingerprint, "league": league, "prop_type": prop_type,
                "training_snapshot": histories,
                "available": False, "reason": "Not enough verified historical observations are available for this market."}
    if len(dates) < 3:
        _TRAINED[fingerprint] = artifact
        return artifact
    cutoff = dates[min(len(dates)-1, max(1, int(len(dates)*0.8)))]
    train = [row for row in dataset if row[0] < cutoff]
    validation = [row for row in dataset if row[0] >= cutoff]
    if len(train) < 100 or len(validation) < 40 or len({row[2] for row in train}) < 2:
        _TRAINED[fingerprint] = artifact
        return artifact
    try:
        import numpy as np
        from xgboost import XGBClassifier
        def classifier():
            return XGBClassifier(n_estimators=100, max_depth=2, learning_rate=0.05, min_child_weight=5,
                                 reg_lambda=10, random_state=42, n_jobs=int(os.getenv("PL_PROP_MODEL_THREADS", "2")), objective="binary:logistic",
                                 eval_metric="logloss", monotone_constraints=(0, 0, 0, 0, 0, -1, -1, 0))
        model = classifier()
        model.fit(np.asarray([row[1] for row in train]), np.asarray([row[2] for row in train]))
        predicted = model.predict_proba(np.asarray([row[1] for row in validation]))[:, 1]
        labels = np.asarray([row[2] for row in validation])
        brier = float(np.mean((predicted-labels)**2))
        baseline_brier = float(np.mean((np.asarray([row[3] for row in validation])-labels)**2))
        artifact.update(train_rows=len(train), validation_rows=len(validation), validation_from=cutoff,
                        trained_before=cutoff, brier=brier, baseline_brier=baseline_brier,
                        evaluation="Chronological synthetic-threshold validation, not published betting results")
        if not math.isfinite(brier) or brier > 0.25:
            artifact["reason"] = "This model could not produce a reliable probability for this market."
        else:
            model = classifier()
            model.fit(np.asarray([row[1] for row in dataset]), np.asarray([row[2] for row in dataset]))
            artifact.update(model=model, available=True, reason=None,
                            training_last_date=max(row[0] for row in dataset))
    except (ImportError, ValueError, RuntimeError) as error:
        artifact["error_type"] = type(error).__name__
    _TRAINED[fingerprint] = artifact
    while len(_TRAINED) > 128:
        _TRAINED.pop(next(iter(_TRAINED)))
    return artifact


def persist_training(original_db, validation):
    """Store native model JSON and exact input history once; never touch original tables."""
    from contextlib import closing
    path = Path(original_db).resolve().with_name("prop_model_artifacts.sqlite")
    records = []
    for metadata in validation.values():
        artifact = _TRAINED.get(metadata.get("fingerprint"))
        if not artifact or not artifact.get("available"):
            continue
        native = artifact["model"].get_booster().save_raw(raw_format="json").decode()
        details = {key: value for key, value in artifact.items() if key != "model"}
        records.append((artifact["fingerprint"], json.dumps(details, allow_nan=False), native))
    if not records:
        return
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS models(fingerprint TEXT PRIMARY KEY,metadata TEXT,model_json TEXT)")
        conn.executemany("INSERT OR IGNORE INTO models VALUES(?,?,?)", records)
        conn.commit()


def forecast(player, prop_type, line, as_of, xgboost=None):
    records = observations(player, prop_type, as_of)
    values = [row["actual"] for row in records]
    available, unavailable = [], []
    if len(values) < 5:
        return [], [{"model_id": key, "model_name": name,
                     "reason": "At least five verified dated appearances are required."} for key, name in REGISTRY]
    for identity, name in REGISTRY[:3]:
        try:
            probs = _distribution(lambda boundary: _exceed(values, boundary, identity, prop_type in COUNT_MARKETS), line)
            available.append(_row(identity, name, probs, len(values)))
        except (ArithmeticError, ValueError, ImportError):
            unavailable.append({"model_id": identity, "model_name": name,
                                "reason": "A consistent player-stat forecast could not be calculated."})
    if xgboost and xgboost.get("available"):
        import numpy as np
        def exceed(boundary):
            if prop_type in COUNT_MARKETS and boundary < 0:
                return 1.0
            return float(xgboost["model"].predict_proba(np.asarray([_features(values, boundary)]))[0, 1])
        probs = _distribution(exceed, line)
        row = _row("xgboost", "XSharp", probs, len(values))
        row["model_version"] += ":"+xgboost["fingerprint"][:12]
        available.append(row)
    else:
        unavailable.append({"model_id": "xgboost", "model_name": "XSharp",
                            "reason": (xgboost or {}).get("reason") or
                            "Not enough verified historical observations are available for this market."})
    if len(available) == 4:
        probs = {key: statistics.mean(row["probabilities"][key] for row in available)
                 for key in ("over", "under", "push")}
        available.append(_row("ensemble", "Sharp Consensus", probs, len(values)))
    else:
        unavailable.append({"model_id": "ensemble", "model_name": "Sharp Consensus",
                            "reason": "All four component prop models must be available."})
    unavailable.append({"model_id": "efficiency", "model_name": "Efficiency",
                        "reason": "Efficiency is a game-winner model, not a player-stat model."})
    return available, unavailable


def _row(identity, name, probs, sample):
    side = "OVER" if probs["over"] > probs["under"] else "UNDER" if probs["under"] > probs["over"] else None
    return {"model_id": identity, "model_name": name, "model_version": VERSION+":"+identity,
            "probabilities": probs, "side": side, "probability": 100*probs[side.lower()] if side else None,
            "sample": sample}
