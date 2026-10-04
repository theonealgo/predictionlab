"""Isolation-proven MLB ML IP gate — no Efficiency / spread / totals."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mlb_ml_v2 import blend_named, ip_gate_scale, mix_named_ml_v2  # noqa: E402


def test_ip_gate_missing_ids_keeps_v1_scale():
    assert ip_gate_scale(100, 100, None, 123) == 0.0
    assert ip_gate_scale(100, 100, 1, None) == 0.0


def test_ip_gate_thin_sample_uses_floor():
    assert abs(ip_gate_scale(8, 90, 1, 2) - 0.35) < 1e-9


def test_ip_gate_full_sample_is_one():
    assert ip_gate_scale(90, 85, 1, 2) == 1.0


def test_ip_gate_ramps_between_12_and_80():
    mid = ip_gate_scale(46, 46, 1, 2)  # halfway 12→80
    assert 0.60 < mid < 0.75


def test_blend_scale_zero_returns_v1():
    g2, ts, elo, xgb, ens = blend_named(0.60, 0.58, 0.55, 0.52, 0.40, w={
        "Grinder2": 0.0, "Takedown": 0.0, "Edge": 0.0, "XSharp": 0.0,
    })
    assert abs(g2 - 0.60) < 1e-9
    assert abs(elo - 0.55) < 1e-9


def test_grading_source_does_not_call_named_ml_v2():
    """Results path must keep apply_named_ml_v2 out of _model_probs_for_grading."""
    text = (ROOT / "NHL77FINAL.py").read_text(encoding="utf-8")
    start = text.find("def _model_probs_for_grading(")
    end = text.find("\ndef _banner_daily_results_for_range(")
    body = text[start:end]
    assert "apply_named_ml_v2" not in body
    assert "mix_named_ml_v2" not in body
    assert "mlb_ml_v2" in body  # comment documenting the exclusion


def test_mix_without_sp_row_falls_back_to_v1():
    g2, ts, elo, xgb, ens = mix_named_ml_v2(
        "NO_SUCH_MLB_GAME",
        0.61, 0.59, 0.54, 0.52, 0.55,
        game_date="2099-01-01", home="Nowhere", away="Nobody",
    )
    assert abs(g2 - 0.61) < 1e-9
    assert abs(elo - 0.54) < 1e-9
