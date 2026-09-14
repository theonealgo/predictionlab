from site_chrome import ensure_gambling_policy_chrome


def test_gambling_policy_stays_in_existing_footer():
    html = (
        "<html><head><title>t</title></head><body>"
        "<footer><p>Site</p></footer>"
        "</body></html>"
    )
    out = ensure_gambling_policy_chrome(html)
    assert 'id="pl-age-bar"' not in out
    assert 'id="pl-rg-policy"' not in out
    assert 'id="pl-rg-fineprint"' in out
    assert out.index("pl-rg-fineprint") < out.index("</footer>")
    assert "21+" in out
    assert "1-800-GAMBLER" in out
    assert "/responsible-gaming" in out
    assert "not an online gambling operator" in out.lower()
    assert ensure_gambling_policy_chrome(out) == out


def test_gambling_policy_does_not_duplicate_existing_footer_copy():
    html = (
        "<html><head></head><body>"
        "<footer><span>21+ (18+ where required). Not an online gambling operator. "
        "We do not take bets. "
        '<a href="/responsible-gaming">Responsible Gaming</a> · '
        '<a href="tel:18005224700">1-800-GAMBLER</a></span></footer>'
        "</body></html>"
    )
    out = ensure_gambling_policy_chrome(html)
    assert 'id="pl-age-bar"' not in out
    assert 'id="pl-rg-policy"' not in out
    assert 'id="pl-rg-fineprint"' not in out


def test_gambling_policy_strips_old_grafts():
    html = (
        "<html><head><style id=\"pl-rg-policy-css\">.x{}</style></head><body>"
        '<div id="pl-age-bar" class="pl-age-bar">21+ only</div>'
        "<footer><span>21+ (18+ where required). Not an online gambling operator. "
        "We do not take bets. "
        '<a href="/responsible-gaming">RG</a> · '
        '<a href="tel:18005224700">1-800-GAMBLER</a></span></footer>'
        '<div id="pl-rg-policy" class="pl-rg-policy">second footer</div>'
        "</body></html>"
    )
    out = ensure_gambling_policy_chrome(html)
    assert 'id="pl-age-bar"' not in out
    assert 'id="pl-rg-policy"' not in out
    assert "second footer" not in out
