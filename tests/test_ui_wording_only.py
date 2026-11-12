import re
from html.parser import HTMLParser

from site_words import rewrite_html, rewrite_json_strings, rewrite_policy


def structure(source):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, [(key, value) for key, value in attrs
                                    if key not in ('title', 'alt', 'aria-label', 'placeholder')]))

        def handle_endtag(self, tag):
            self.tags.append(('/' + tag, []))
    parser = Parser()
    parser.feed(source)
    return parser.tags


def test_visible_labels_change_without_touching_prices_or_card_structure():
    source = (
        '<div class="game-card" data-m-consensus="59.2" data-books-spread="-3.5">'
        '<div class="team-name">Books FC</div>'
        '<span class="ml-src books">Books</span><b>-160</b>'
        '<span class="ml-src pl">Prediction Lab</span><b>+136</b>'
        '<span>Sharp Consensus</span><b>59.2%</b>'
        '<p>PL vs Books</p><p>Odds &amp; Lines</p></div>'
    )
    result = rewrite_html(source)
    assert structure(result) == structure(source)
    assert re.findall(r'[-+]?\d+(?:\.\d+)?', result) == re.findall(r'[-+]?\d+(?:\.\d+)?', source)
    assert '<span class="ml-src books">Market</span>' in result
    assert '<span class="ml-src pl">Predictionlab</span>' in result
    assert 'Market Consensus' in result and 'PL vs Market' in result
    assert '<div class="team-name">Books FC</div>' in result
    assert rewrite_html(result) == result


def test_players_and_performance_ladder_leave_link_targets_unchanged():
    source = '<nav><a href="/player-props">Player Props</a><a href="/ladder">Ladder</a></nav>'
    result = rewrite_html(source)
    assert structure(result) == structure(source)
    assert '>Players</a>' in result and '>Performance Ladder</a>' in result


def test_internal_models_and_scripts_do_not_get_display_renames():
    data = {'market': 'spread', 'model': 'Sharp Consensus', 'name': 'Books FC',
            'odds': -160, 'probability': 59.2, 'loggedIn': True, 'premium': False}
    assert rewrite_json_strings(data) == data
    source = '<script>const model="Sharp Consensus"; const odds=-160;</script><p>Books</p>'
    assert '<script>const model="Sharp Consensus"; const odds=-160;</script>' in rewrite_html(source)


def test_locked_details_stay_locked_and_never_receive_private_values():
    source = (
        '<div class="game-card"><span>Books</span>'
        '<div class="card-details" hidden>'
        '<div class="odds-pricing-locked">Lines &amp; projections locked. '
        '<a href="/login">Log in</a> or <a href="/plans">unlock premium</a>.'
        '</div></div></div>'
    )
    result = rewrite_html(source)
    assert structure(result) == structure(source)
    assert 'odds-pricing-locked' in result and 'hidden' in result
    assert 'unlock premium' in result and 'Log in' in result
    assert 'pc-box' not in result


def test_policy_prose_and_quotes_stay_verbatim():
    source = '<header><a href="/player-props">Player Props</a></header><main>We are not a licensed sportsbook.</main>'
    result = rewrite_policy(source)
    assert '<main>We are not a licensed sportsbook.</main>' in result
    assert '>Players</a>' in result
    quote = '<blockquote>Books +136, Sharp Consensus 59.2%</blockquote>'
    assert quote in rewrite_html(quote)


def test_saved_card_text_upgrade_preserves_access_and_data_fields():
    from web_api import _reword_cards
    payload = {'words': 1, 'cards': [{'home': 'Home', 'away': 'Away',
               'html': '<div data-pick="Books"><span>PL Odds</span><b>+136</b></div>'}],
               'css': '<style>.game-card{display:block}</style>', 'premium': False}
    result = _reword_cards(payload)
    assert result['words'] == 2 and result['premium'] is False
    assert result['css'] == payload['css']
    assert 'data-pick="Books"' in result['cards'][0]['html']
    assert '<span>Predictionlab</span><b>+136</b>' in result['cards'][0]['html']
