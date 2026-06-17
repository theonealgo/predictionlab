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

    assert '<span class="directory-wordmark">PREDICTIONLABS.IO</span>' in footer
    assert '<a href="/" class="directory-wordmark"' not in footer
    assert "<img" not in footer


def test_homepage_spacing_transparency_and_glossary_contrast_are_locked():
    homepage = _read("templates/homepage_preview.html")

    assert "background: hsl(45 35% 99.5% / .78);" in homepage
    assert "margin-left: max(160px" in homepage
    assert "backdrop-filter: none !important;" in homepage
    assert ".pl2-breadcrumb { display: none; }" not in homepage
    assert "display: block; left: 10px; width: 36px" in homepage
    assert "background: hsl(45 25% 99% / .84);" in homepage
    assert ".pl2-sec.band { background: hsl(45 18% 97% / .78); }" in homepage
    assert "width: 2px; height: 38px;" in homepage
    assert "background: var(--pl-accent);" in homepage
    assert "background: transparent;" in homepage
    assert ".pl2-stat b .pl2-countup" in homepage
    assert ".pl2-stat > span" in homepage
    assert "padding: clamp(280px, 19vw, 360px) 0 180px;" in homepage
    assert "@media (min-width: 981px) and (max-height: 950px)" not in homepage
    assert 'aria-label="Live season performance"' not in homepage
    assert "background: #0b0b0a" in homepage
    assert "color: var(--pl-accent)" in homepage


def test_shared_theme_preserves_logo_header_and_translucent_page_shells():
    theme = _read("static/css/research-theme.css")

    assert "body.research-site .navbar-content > .logo img" in theme
    assert 'content: "PREDICTION LAB"' not in theme
    assert "background: rgba(251,251,248,.82);" in theme
    assert ".directory-wordmark" in theme
    assert "font: 800 12px/1.4" in theme


def test_shared_header_matches_research_reference_navigation():
    # HEADER CONTRACT: sport dropdown navigation plus one account-icon menu.
    header = _read("templates/partials/research_header.html")
    theme = _read("static/css/research-theme.css")

    assert "<span>PL /</span><b>PREDICTION LAB</b>" in header
    assert 'aria-label="Open menu"' in header
    assert 'aria-label="Search"' in header
    assert '<button type="button" class="pl2-nav-trigger" aria-haspopup="true" aria-expanded="false">Picks</button>' in header
    assert 'href="/mlb-picks">MLB</a>' in header
    assert 'aria-label="Account menu"' in header
    assert 'href="/login">Login</a>' in header
    assert 'href="/signup">Sign Up</a>' in header
    assert 'href="/logout">Sign Out</a>' in header
    assert 'href="/blog">Blog</a>' in header
    assert "background: var(--pl-neon);" in theme


def test_homepage_has_short_faq_before_full_faq_link():
    # HOMEPAGE FAQ CONTRACT: compact trust answers live on /, deep help stays on /faq.
    homepage = _read("templates/homepage_preview.html")

    assert 'id="sec-faq"' in homepage
    assert "Questions, answered honestly." in homepage
    for question in (
        "How do your AI sports betting picks work?",
        "What makes your picks different from sportsbooks?",
        "How do you find value bets?",
        "What does the probability percentage mean?",
        "Do your models agree on every pick?",
        "What sports do you cover?",
        "Are your results tracked publicly?",
        "Are your picks guaranteed to win?",
    ):
        assert question in homepage
    assert 'href="/faq">Full FAQ' in homepage


def test_homepage_today_board_uses_compact_moneyline_cards():
    # TODAY'S BOARD CONTRACT: homepage previews real moneyline picks as mini cards.
    homepage = _read("templates/homepage_preview.html")

    assert "Live Predictions, Next Slate." in homepage
    assert "Moneyline-only preview. Green highlights the model's selection." in homepage
    assert 'class="pl2-live-board"' in homepage
    assert 'class="pl2-pick-card"' in homepage
    assert "tp.is_live" in homepage
    assert "pl2-pick-card-live" in homepage
    assert "tp.home_prob" in homepage
    assert "tp.away_prob" in homepage
    assert "Full Board" not in homepage
    assert 'href="/{{ active_sport_slug }}">See All Predictions' in homepage


def test_homepage_how_it_works_has_data_art_cards():
    # HOW IT WORKS CONTRACT: each process card has a small AI/data visual above copy.
    homepage = _read("templates/homepage_preview.html")

    assert "pl2-step-art data" in homepage
    assert "pl2-step-art model" in homepage
    assert "pl2-step-art project" in homepage
    assert "pl2-step-art consensus" in homepage
    assert ".pl2-step-art::before" in homepage


def test_shared_header_contains_july_premium_offer():
    header = _read("templates/partials/research_header.html")
    theme = _read("static/css/research-theme.css")

    assert 'id="plJulyOffer"' in header
    assert "JULYFREE" in header
    assert "now.getFullYear() === 2026 && now.getMonth() === 6" in header


def test_shared_header_keeps_hamburger_visible_on_desktop():
    # HEADER CONTRACT: the full-site burger menu must never be mobile-only.
    header = _read("templates/partials/research_header.html")
    theme = _read("static/css/research-theme.css")

    assert 'class="pl2-burger" id="navHamburger"' in header
    assert "function tvToggle" in _read("templates/base.html")
    assert "display: flex; /* Always visible: primary full-site menu access. */" in theme
    assert "border-radius: 8px;" in theme
    assert "min-height: 100dvh;" in theme
    assert "display: none; /* desktop uses the nav dropdowns; burger is mobile-only */" not in theme
    assert "predictionlab_july_2026_offer_dismissed" in header
    assert "preview_july_offer" in header
    assert 'href="/plans"' in header
    assert ".pl-july-offer__card" in theme
    assert "body.pl-july-offer-open" in theme


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
