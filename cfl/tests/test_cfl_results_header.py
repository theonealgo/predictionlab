"""CFL results header: one working site nav + Predictions|Results + Cards|Chart."""

from __future__ import annotations

from cfl_live import (
    _dedupe_research_nav_scripts,
    _finalize_cfl_html,
    _inject_chrome_into_page,
    _reorder_cfl_results_headers,
    _restore_global_nav_mlb_links,
    _strip_research_nav_scripts,
)


_DUP_NAV_SCRIPTS = """
<script>
/* ACCOUNT MENU: opens the person icon menu and closes it on outside click/Escape. */
(function () { /* orphan A */ })();
/* NAV DROPDOWNS: Sports / Models / Results open a menu on click. */
(function () { /* orphan A */ })();
</script>
<script>
/* ACCOUNT MENU: opens the person icon menu and closes it on outside click/Escape. */
(function () { /* orphan B */ })();
/* NAV DROPDOWNS: Sports / Models / Results open a menu on click. */
(function () { /* orphan B */ })();
</script>
"""


def test_strip_research_nav_scripts_removes_orphans():
    html = "<body>" + _DUP_NAV_SCRIPTS + "<main/>"
    out = _strip_research_nav_scripts(html)
    assert "NAV DROPDOWNS" not in out


def test_dedupe_research_nav_scripts_keeps_one():
    html = "<body>" + _DUP_NAV_SCRIPTS + "<main/>"
    out = _dedupe_research_nav_scripts(html)
    assert out.count("NAV DROPDOWNS") == 1


def test_restore_global_nav_mlb_links():
    html = (
        '<header class="pl2-header">'
        '<a href="/cfl-picks">MLB</a>'
        '<a href="/cfl-results">MLB</a>'
        '<a href="/cfl-picks">CFL</a>'
        "</header>"
        '<div class="section-tabs">'
        '<a href="/cfl-picks" class="tab">Predictions</a>'
        "</div>"
    )
    out = _restore_global_nav_mlb_links(html)
    assert 'href="/mlb-picks">MLB</a>' in out
    assert 'href="/mlb-results">MLB</a>' in out
    assert 'href="/cfl-picks">CFL</a>' in out
    assert 'href="/cfl-picks" class="tab">Predictions</a>' in out


def test_reorder_puts_tabs_under_title():
    html = (
        '<div class="section-tabs"><a href="/cfl-picks">Predictions</a></div>'
        '<div class="pl-view-toggle"><a href="/cfl-results">Cards</a>'
        '<a href="/cfl-results?view=chart">Chart</a></div>'
        "<h1 class=\"page-title\">CFL Results</h1>"
        "<main>body</main>"
    )
    out = _reorder_cfl_results_headers(html)
    h1 = out.find("<h1")
    tabs = out.find('class="section-tabs"')
    toggle = out.find('class="pl-view-toggle"')
    assert h1 < tabs < toggle


def test_finalize_restores_mlb_after_shell_rewrite():
    html = (
        '<header class="pl2-header">'
        '<a href="/cfl-picks">MLB</a>'
        '<a href="/nhl-picks">NHL</a>'
        "</header>"
    )
    out = _finalize_cfl_html(html)
    assert 'href="/mlb-picks">MLB</a>' in out
    assert 'href="/nhl-picks">NHL</a>' in out
