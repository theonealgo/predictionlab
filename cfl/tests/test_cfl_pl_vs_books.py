"""CFL PL vs Books chip uses posted results. It does not invent a line."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cfl_live import (
    cfl_pl_vs_books_signals,
    cfl_pl_vs_books_text,
    stamp_cfl_pl_vs_books,
)


def _signals():
    rows = [
        {"book_side": "HOME", "pl_side": "HOME", "book_grade": "WIN", "pl_grade": "WIN"},
        {"book_side": "AWAY", "pl_side": "HOME", "book_grade": "LOSS", "pl_grade": "WIN"},
        {"book_side": None, "pl_side": "AWAY", "book_grade": None, "pl_grade": "LOSS"},
    ]
    return cfl_pl_vs_books_signals(rows)


def _card(day: str, pick: str) -> str:
    return (
        f'<div data-pick-card data-home="Ottawa RedBlacks" data-away="Hamilton Tiger-Cats" '
        f'data-pick="{pick}" data-time="{day} · 7:00 PM ET">'
        '<div class="lines-strip">'
        '<div class="line-chip"><div class="line-chip-label">Spread Confidence</div>'
        '<div class="line-chip-val">59</div></div></div></div>'
    )


def test_signals_count_only_posted_sides():
    signals = _signals()
    assert signals["books"][1] == "1-1"
    assert signals["pl"][1] == "2-1"
    assert signals["agree"][1] == "1-0"
    assert signals["disagree"][1] == "1-0"


def test_no_book_side_uses_pl_record_only():
    text = cfl_pl_vs_books_text(None, "AWAY", _signals())
    assert text == "PL favorite: 2-1 (67%) — Past 30 Days"


def test_agreeing_sides_use_the_agree_record():
    text = cfl_pl_vs_books_text("HOME", "HOME", _signals())
    assert text == "PL and Books agree: 1-0 (100%) — Past 30 Days"


def test_future_card_gets_record_and_yesterday_is_left_alone():
    signals = _signals()
    books = {("ottawa redblacks", "hamilton tiger-cats", "2026-10-02"): "AWAY"}
    future = stamp_cfl_pl_vs_books(
        _card("2026-10-02", "Hamilton Tiger-Cats"),
        signals=signals,
        books=books,
        today="2026-09-29",
    )
    assert "PL vs Books" in future
    assert "1-0 (100%)" in future
    assert "60.5" not in future
    old = stamp_cfl_pl_vs_books(
        _card("2026-09-26", "Hamilton Tiger-Cats"),
        signals=signals,
        books=books,
        today="2026-09-29",
    )
    assert "PL vs Books" not in old


def test_missing_both_sides_does_not_invent_a_record():
    assert cfl_pl_vs_books_text(None, None, _signals()) is None
