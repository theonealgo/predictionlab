#!/usr/bin/env python3
"""Share-image cache is disk-backed so Gunicorn multi-worker can resolve tokens."""
import os

import NHL77FINAL as m


def test_share_image_disk_roundtrip():
    t = m._register_share_image({
        'type': 'predictions',
        'sport': 'Test',
        'date': '2026-01-01',
        'cards': [],
    })
    assert m._SHARE_TOKEN_RE.match(t)
    try:
        e = m._get_share_cache_entry(t)
        assert e and e.get('payload', {}).get('type') == 'predictions'
    finally:
        p = os.path.join(m._SHARE_IMAGE_CACHE_DIR, f'{t}.json')
        if os.path.isfile(p):
            os.unlink(p)


def test_share_image_rejects_bad_token():
    assert m._get_share_cache_entry('../../../etc/passwd') is None
    assert m._get_share_cache_entry('not-hex') is None
    assert m._get_share_cache_entry('a' * 31) is None


if __name__ == '__main__':
    test_share_image_disk_roundtrip()
    test_share_image_rejects_bad_token()
    print('test_share_image_cache: ok')
