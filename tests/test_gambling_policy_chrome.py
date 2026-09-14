from site_chrome import ensure_gambling_policy_chrome


def test_gambling_policy_chrome_adds_age_and_helpline():
    html = "<html><head><title>t</title></head><body><p>hi</p></body></html>"
    out = ensure_gambling_policy_chrome(html)
    assert 'id="pl-age-bar"' in out
    assert "21+" in out
    assert "1-800-GAMBLER" in out
    assert "/responsible-gaming" in out
    assert "not an online gambling operator" in out.lower()
    assert "do not provide online gambling services" in out.lower()
    assert "/terms" in out
    assert "/privacy" in out
    assert ensure_gambling_policy_chrome(out) == out
