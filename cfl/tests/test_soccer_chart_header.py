"""Soccer results title must not be a second <header> next to pl2-header."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import inject_soccer_results_page_title


def test_soccer_results_title_is_h1_not_header():
    html = (
        '<html><body class="research-site">'
        '<header class="pl2-header">SITE</header>'
        "<main></main></body></html>"
    )
    out = inject_soccer_results_page_title(html)
    assert out.count("<header") == 1
    assert 'class="pl2-header"' in out
    assert 'id="soccer-results-page-title"' in out
    assert "<h1" in out and "Soccer Results" in out
    assert 'header class="top"' not in out


def test_soccer_results_title_upgrades_legacy_header_top():
    html = (
        '<html><body>'
        '<header class="pl2-header">SITE</header>'
        '<main>'
        '<header class="top" id="soccer-results-page-title">'
        '<div class="brand">Soccer Results</div></header>'
        "</main></body></html>"
    )
    out = inject_soccer_results_page_title(html)
    assert out.count("<header") == 1
    assert 'header class="top"' not in out
    assert '<h1 class="page-title" id="soccer-results-page-title">' in out
