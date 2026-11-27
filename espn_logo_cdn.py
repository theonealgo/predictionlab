"""Serve ESPN team logos through our origin with a long cache lifetime.

PageSpeed flags a.espncdn.com Cache-TTL (hours, not weeks). We cannot change ESPN's
headers; caching a copy under predictionlab.io with Cache-Control: max-age=30d fixes
the audit for homepage logos without rewriting every sport card.

ESPN often ignores ?w=&h= on /i/teamlogos/.../500/*.png and returns 500×500. We
resize here to the requested edge (default 56) and prefer WebP so homepage previews
(~28px display) stop shipping 90+ KiB PNGs.
"""
from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from flask import Blueprint, Response, abort, request

logger = logging.getLogger(__name__)

cdn = Blueprint('espn_logo_cdn', __name__)

_ROOT = Path(__file__).resolve().parent
_CACHE = _ROOT / '.cache' / 'espn_logos'
_ALLOWED = re.compile(
    r'^https://a\.espncdn\.com/(?:combiner/i\?img=/i/teamlogos/|i/teamlogos/)',
    re.I,
)
_MAX_BYTES = 250_000
_SIZE_RE = re.compile(r'[?&]w=(\d+)', re.I)
# Browser-like UA — ESPN sometimes 403s a custom bot agent.
_UA = (
    'Mozilla/5.0 (compatible; PredictionLab/1.0; +https://predictionlab.io) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def _cache_path(url: str, edge: int, fmt: str) -> Path:
    digest = hashlib.sha256(f'{url}|{edge}|{fmt}'.encode('utf-8')).hexdigest()
    return _CACHE / f'{digest}.{fmt}'


def _requested_edge(url: str) -> int:
    match = _SIZE_RE.search(url or '')
    if not match:
        return 56
    try:
        value = int(match.group(1))
    except ValueError:
        return 56
    return max(16, min(value, 128))


def _fetch(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': _UA,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://www.espn.com/',
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        ctype = (resp.headers.get('Content-Type') or 'image/png').split(';')[0].strip()
        if not ctype.startswith('image/'):
            raise ValueError(f'non-image content-type {ctype}')
        data = resp.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError('logo too large')
    return data, ctype or 'image/png'


def _shrink(data: bytes, edge: int, prefer_webp: bool) -> tuple[bytes, str]:
    """Downscale ESPN logos; PageSpeed flags full 500×500 for 28px homepage imgs."""
    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        im = im.convert('RGBA')
        im.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        buf = BytesIO()
        if prefer_webp:
            im.save(buf, format='WEBP', quality=82, method=4)
            ctype = 'image/webp'
        else:
            im.save(buf, format='PNG', optimize=True)
            ctype = 'image/png'
        out = buf.getvalue()
    return out, ctype


def _pixel_png() -> bytes:
    return (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
        b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )


@cdn.route('/cdn/team-logo')
def team_logo():
    """Proxy an ESPN team logo; long-lived cache so repeat visits skip espncdn."""
    url = (request.args.get('u') or '').strip()
    if not url or not _ALLOWED.match(url):
        abort(404)
    edge = _requested_edge(url)
    accept = (request.headers.get('Accept') or '').lower()
    prefer_webp = 'image/webp' in accept
    fmt = 'webp' if prefer_webp else 'png'
    path = _cache_path(url, edge, fmt)
    ctype = f'image/{fmt}'
    try:
        if path.is_file() and path.stat().st_size > 0:
            data = path.read_bytes()
        else:
            raw, _src_ctype = _fetch(url)
            data, ctype = _shrink(raw, edge, prefer_webp)
            _CACHE.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
        logger.debug('espn logo cache miss/fail: %s', exc)
        # Never return HTML/JSON here — PageSpeed treats non-image failures as
        # console errors. Serve a 1x1 transparent PNG so <img> still succeeds.
        resp = Response(_pixel_png(), mimetype='image/png')
        resp.headers['Cache-Control'] = 'public, max-age=300'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        return resp
    resp = Response(data, mimetype=ctype)
    resp.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Vary'] = 'Accept'
    return resp
