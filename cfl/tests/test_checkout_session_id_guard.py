"""Guard: never retrieve Stripe Checkout with the {CHECKOUT_SESSION_ID} placeholder."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_auth_helpers():
    """Load only the session-id guard without importing the full Flask app stack."""
    path = Path(__file__).resolve().parents[1] / "auth_system.py"
    text = path.read_text(encoding="utf-8")
    # Extract the helper by exec'ing a minimal slice (avoid heavy imports).
    start = text.index("def _is_real_checkout_session_id")
    end = text.index("\n@auth_bp.route('/checkout/success')", start)
    ns: dict = {}
    exec(text[start:end], ns)  # noqa: S102 — test-only extract of pure helper
    return ns["_is_real_checkout_session_id"]


def test_rejects_placeholder_and_junk():
    fn = _load_auth_helpers()
    assert fn(None) is False
    assert fn("") is False
    assert fn("{CHECKOUT_SESSION_ID}") is False
    assert fn("%7BCHECKOUT_SESSION_ID%7D") is False
    assert fn("session_id={CHECKOUT_SESSION_ID}") is False
    assert fn("cs_") is False
    assert fn("sub_1234567890") is False


def test_accepts_real_checkout_session_ids():
    fn = _load_auth_helpers()
    assert fn("cs_test_a1b2c3d4e5f6") is True
    assert fn("cs_live_a1b2c3d4e5f6") is True
    assert fn("  cs_test_a1b2c3d4e5f6  ") is True
