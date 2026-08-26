#!/usr/bin/env python3
"""Minimal chrome stubs for iso_hub modules running inside work2.

Work2 injects research_header + burger via cfl_golf_work2._inject_chrome_into_page.
"""
from __future__ import annotations


def ensure_canonical_chrome(html: str, sport: str = "", *, which: str = "picks") -> str:
    return html or ""


def sport_section_tabs(sport: str, *, which: str = "picks") -> str:
    sport = (sport or "").lower()
    picks = f"/{sport}-picks"
    results = f"/{sport}-results"
    pa = "active" if which == "picks" else ""
    ra = "active" if which == "results" else ""
    return (
        '<div class="section-tabs" role="navigation" aria-label="Sport pages">'
        f'<a href="{picks}" class="tab {pa}">Picks</a>'
        f'<a href="{results}" class="tab {ra}">Results</a>'
        "</div>"
    )


def strip_hub_links(html: str) -> str:
    return html or ""


def strip_cfl_nfl_remnants(html: str) -> str:
    return html or ""


def replace_balanced_container_inner(html: str, inner: str) -> str:
    return html or inner or ""
