from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_homepage_inherits_canonical_header_and_protected_research_layout():
    app_source = _read("NHL77FINAL.py")
    homepage = _read("templates/homepage_preview.html")
    base = _read("templates/base.html")

    assert "return render_template('homepage_preview.html'" in app_source
    assert 'partials/research_header.html' not in homepage
    assert '{% include "partials/research_header.html" %}' in base
    assert ".navbar, .share-strip, .site-footer, .join-premium-bar" in homepage


def test_shared_footer_uses_text_wordmark_not_logo_image():
    footer = _read("templates/partials/site_directory_footer.html")

    assert "predictionlabs.io" in footer
    assert "directory-wordmark" in footer
    assert "<img" not in footer


def test_homepage_spacing_transparency_and_glossary_contrast_are_locked():
    homepage = _read("templates/homepage_preview.html")

    assert "background: hsl(45 25% 99% / .56);" in homepage
    assert "margin-left: max(140px" in homepage
    assert ".pl2-breadcrumb { display: none; }" not in homepage
    assert "display: block; left: 10px; width: 36px" in homepage
    assert "background: hsl(45 25% 99% / .68);" in homepage
    assert ".pl2-sec.band { background: hsl(0 0% 92% / .62); }" in homepage
    assert "background: hsl(0 0% 92% / .68);" in homepage
    assert "backdrop-filter: blur(5px);" in homepage
    assert "background: #0b0b0a" in homepage
    assert "color: var(--pl-accent)" in homepage


def test_shared_theme_preserves_logo_header_and_translucent_page_shells():
    theme = _read("static/css/research-theme.css")

    assert "body.research-site .navbar-content > .logo img" in theme
    assert 'content: "PREDICTION LAB"' not in theme
    assert "background: rgba(251,251,248,.82);" in theme
    assert ".directory-wordmark" in theme
    assert "font: 800 12px/1.4" in theme


def test_primary_layouts_load_the_shared_research_theme():
    for template in (
        "templates/base.html",
        "templates/espn_predictions_template.html",
        "templates/underdogs_layout.html",
    ):
        source = _read(template)
        assert "/static/css/research-theme.css" in source
        assert 'class="research-site"' in source


def test_public_page_families_use_canonical_header_and_footer():
    for path in (
        "templates/base.html",
        "templates/includes/picks_nav_chrome.html",
        "templates/underdogs_layout.html",
        "NHL77FINAL.py",
        "auth_system.py",
    ):
        assert 'partials/research_header.html' in _read(path)

    for path in (
        "templates/base.html",
        "templates/espn_predictions_template.html",
        "templates/underdogs_layout.html",
        "NHL77FINAL.py",
        "auth_system.py",
    ):
        source = _read(path)
        assert 'partials/site_directory_footer.html' in source


def test_picks_page_cache_accepts_real_card_class_names():
    app_source = _read("NHL77FINAL.py")

    assert "and 'class=\"game-card' in rendered" in app_source
    assert "rendered.count('class=\"game-card\"')" not in app_source
