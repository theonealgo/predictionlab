"""Results pages carry no emoji. The win and loss marks become plain check and cross symbols, and the
other emoji are dropped. NHL77FINAL.py applies this to the results pages' HTML on the way out."""
import re

_MARKS = {'✅': '✓', '❌': '✗'}  # white check mark -> check, cross mark -> cross
# Pictographs, the misc symbols block (leaving the star and the plain symbols alone), the star emoji,
# the emoji variation selector and the zero-width joiner.
_EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-☄☆-⛿⭐️‍]')


def clean_results_html(html):
    for emoji, mark in _MARKS.items():
        html = html.replace(emoji, mark)
    return _EMOJI.sub('', html)
