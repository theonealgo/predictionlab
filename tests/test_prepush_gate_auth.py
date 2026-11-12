from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

import pytest
from qa import prepush_gate as gate


def test_guest_headers_never_include_paid_cookie():
    with patch.object(gate, '_GATE_COOKIE', 'session=test-only-fixture'):
        assert gate._request_headers()['Cookie'] == 'session=test-only-fixture'
        headers = gate._request_headers(gate.FREE_HOST)
        assert headers['Host'] == gate.FREE_HOST
        assert 'Cookie' not in headers


def test_gate_refuses_main_checkout_or_nonlocal_host():
    for work, base in [(gate.ROOT, 'http://127.0.0.1:5199'),
                       (gate.ROOT / '.checkpoints', 'https://predictionlab.io')]:
        with pytest.raises(RuntimeError, match='isolated local worktree'):
            gate._authenticate_worktree(work, base)


def test_account_is_written_only_in_disposable_copy_and_uses_real_login():
    with tempfile.TemporaryDirectory(dir=gate.ROOT) as directory:
        work = Path(directory)
        with sqlite3.connect(work / 'sports_predictions_original.db') as conn:
            conn.execute('CREATE TABLE users(email TEXT,name TEXT,password_hash TEXT,is_premium INTEGER,premium_expires TEXT)')
        with patch('qa.paid_viewer._sign_in', return_value={'session': 'fixture'}) as login:
            assert gate._authenticate_worktree(work, 'http://127.0.0.1:5199') == 'session=fixture'
        assert login.call_count == 1
        with sqlite3.connect(work / 'sports_predictions_original.db') as conn:
            assert conn.execute('SELECT is_premium,premium_expires FROM users').fetchone() == (1, '2099-01-01T00:00:00Z')
