#!/usr/bin/env python3
"""Unit tests for generic contract auditors + expanded PSI/a11y helpers."""
from __future__ import annotations

from contract_auditors import (
    ads_analytics_issues,
    global_leakage_issues,
    logo_size_issues,
    picks_functional_issues,
    seo_page_issues,
    share_card_contract_issues,
)
from chart_shape import picks_pagespeed_a11y_issues


def test_global_leakage_traceback_and_localhost():
    html = "<html><body>Traceback (most recent call last): boom</body></html>"
    assert any("traceback" in i.lower() for i in global_leakage_issues(html, base_url="https://predictionlab.io"))
    html2 = '<a href="http://127.0.0.1:5052/mlb-picks">x</a>'
    assert any("localhost" in i.lower() for i in global_leakage_issues(html2, base_url="https://predictionlab.io"))
    # Local audits may include localhost links
    assert not global_leakage_issues(html2, base_url="http://127.0.0.1:5052")


def test_seo_page_issues_core():
    bad = "<html><head></head><body></body></html>"
    issues = seo_page_issues(bad, "/mlb-picks")
    assert any("title" in i for i in issues)
    assert any("canonical" in i for i in issues)
    assert any("lang" in i for i in issues)


def test_logo_size_mlb_500():
    html = '<img src="https://a.espncdn.com/i/teamlogos/mlb/500/nyy.png">'
    assert logo_size_issues(html, "MLB")
    html2 = '<img src="https://a.espncdn.com/i/teamlogos/mlb/100/nyy.png">'
    assert logo_size_issues(html2, "MLB")  # raw /100/ 404s for many teams
    html3 = (
        '<img src="https://a.espncdn.com/combiner/i?'
        'img=/i/teamlogos/mlb/500/nyy.png&h=100&w=100">'
    )
    assert not logo_size_issues(html3, "MLB")
    # NFL not in LOGO_CARD_SIZE_BY_SPORT yet
    assert not logo_size_issues(html.replace("/mlb/", "/nfl/"), "NFL")


def test_share_and_ads():
    html = '''
    <div class="social-export-wrap">
      <a class="social-image-link" href="/share/predictions/view/abc">
        <img src="/share/predictions/abc.jpg" alt="">
      </a>
    </div>
    <script async src="https://www.googletagmanager.com/gtag/js?id=AW-18345189026"></script>
    '''
    assert share_card_contract_issues(html, "MLB")
    assert ads_analytics_issues(html)


def test_picks_functional_placeholder():
    html = '<div class="game-card">TODO pick</div><div class="win-pct">—</div><div class="win-pct">—</div>'
    assert picks_functional_issues(html, "MLB")


def test_pagespeed_helper_detects_and_clears():
    bad = '''<!DOCTYPE html><html><head>
    <link rel="stylesheet" href="/static/css/picks-chart.css">
    <script src="https://www.googletagmanager.com/gtag/js?id=AW-1"></script>
    </head><body>
    <div class="pv-toggle" role="tablist">
      <button id="pvCardsBtn" class="active">Cards</button>
      <button id="pvChartBtn">Chart</button>
    </div>
    <img class="team-logo" src="https://a.espncdn.com/i/teamlogos/mlb/500/nyy.png">
    <a class="social-image-link" href="/x"><img src="/share/predictions/a.jpg" alt=""></a>
    </body></html>'''
    issues = picks_pagespeed_a11y_issues(bad, "MLB")
    assert issues
    assert any("lang" in i for i in issues)
    assert any("main" in i for i in issues)
    assert any("role=tab" in i for i in issues)
