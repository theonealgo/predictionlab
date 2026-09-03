"""JSON API for Tennis."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from tennis.backend.predictor import TennisPredictor
from tennis.results.grader import grade_pending, performance
from tennis.ml import FEATURE_NAMES
from tennis.ml.models import TennisModel
from shared.features import permutation_importance_stub

SPORT = "TENNIS"
SLUG = "tennis"


def create_blueprint() -> Blueprint:
    bp = Blueprint("tennis_api", __name__)

    @bp.get("/api/tennis/predict")
    def api_predict():
        a = request.args.get("a", "Player A")
        b = request.args.get("b", "Player B")
        surface = request.args.get("surface", "hard")
        return jsonify(TennisPredictor().predict_match(a, b, surface))

    @bp.get("/api/tennis/results")
    def api_results():
        preds = TennisPredictor().list_predictions()
        return jsonify({"sport": SPORT, "predictions": preds})

    @bp.get("/api/tennis/performance")
    def api_performance():
        return jsonify(performance())

    @bp.post("/api/tennis/grade")
    def api_grade():
        return jsonify({"graded": grade_pending()})

    @bp.get("/api/tennis/feature-importance")
    def api_fi():
        return jsonify({"features": permutation_importance_stub(FEATURE_NAMES)})

    @bp.get("/api/tennis/models")
    def api_models():
        m = TennisModel()
        m.fit_demo()
        return jsonify({"models": list(m.ensemble.models.keys()) + ["ensemble"], "fitted": True})

    return bp
