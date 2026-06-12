def test_google_trends_rss_feed_is_parsed_and_cached(monkeypatch):
    import NHL77FINAL as N

    xml = b"""<?xml version="1.0"?>
    <rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
      <item><title>Dodgers vs Pirates</title><ht:approx_traffic>100K+</ht:approx_traffic></item>
    </channel></rss>"""

    class Response:
        content = xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(N.requests, "get", lambda *args, **kwargs: Response())
    N._TRENDS_CACHE.update({"ts": 0.0, "items": []})

    assert N._fetch_google_trends() == [
        {"query": "Dodgers vs Pirates", "traffic": "100K+"}
    ]


def test_trending_sports_route_links_matching_prediction(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(
        N,
        "_fetch_google_trends",
        lambda: [{"query": "Dodgers vs Pirates", "traffic": "100K+"}],
    )
    monkeypatch.setattr(
        N,
        "_trend_match_index",
        lambda: [{
            "sport": "MLB",
            "date": "2026-06-11",
            "away": "Los Angeles Dodgers",
            "home": "Pittsburgh Pirates",
            "away_tok": "dodgers",
            "home_tok": "pirates",
            "away_full": "los angeles dodgers",
            "home_full": "pittsburgh pirates",
            "url": "/mlb-picks/dodgers-vs-pirates-2026-06-11",
            "pick": "Dodgers",
            "pct": 57.1,
        }],
    )

    with N.app.test_client() as client:
        response = client.get("/trending-sports")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Dodgers vs Pirates" in html
    assert "/mlb-picks/dodgers-vs-pirates-2026-06-11" in html
    assert "100K+ searches" in html
