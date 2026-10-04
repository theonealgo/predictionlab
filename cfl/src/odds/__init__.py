"""Parallel book-reference helpers (does not touch PL production pricing)."""

from .book_odds_engine import BOOK_REFERENCE_LEGEND, build_book_odds_bundle

__all__ = ["BOOK_REFERENCE_LEGEND", "build_book_odds_bundle"]
