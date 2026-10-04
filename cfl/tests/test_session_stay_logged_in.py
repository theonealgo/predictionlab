"""Logged-in customers must stay logged in across tabs and restarts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flask import Flask

import auth_system as auth


class SessionStayLoggedInTests(unittest.TestCase):
    def test_session_protection_off_and_samesite_lax(self):
        app = Flask(__name__)
        app.config["TESTING"] = True
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            auth.init_auth(app, db_path=db_path)
            self.assertIsNone(auth._login_manager.session_protection)
            self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
            self.assertEqual(app.config["REMEMBER_COOKIE_SAMESITE"], "Lax")
            self.assertEqual(app.config["REMEMBER_COOKIE_DURATION"].days, 90)
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_second_request_keeps_login(self):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "stable-test-secret"
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            auth.init_auth(app, db_path=db_path)
            conn = auth._get_db()
            conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                ("stay@example.com", "Stay", "x"),
            )
            conn.commit()
            user = auth._load_user_by_email("stay@example.com")
            conn.close()
            with app.test_request_context("/nfl-results"):
                from flask_login import login_user, current_user

                login_user(user, remember=True)
                self.assertTrue(current_user.is_authenticated)
            client = app.test_client()
            with client.session_transaction() as sess:
                sess["_user_id"] = str(user.id)
                sess["_fresh"] = True
            with app.test_request_context("/nfl-results"):
                # user_loader still resolves the same id after another page
                loaded = auth._load_user_by_id(user.id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.email, "stay@example.com")
        finally:
            Path(db_path).unlink(missing_ok=True)


    def test_user_loader_does_not_logout_on_locked_db(self):
        app = Flask(__name__)
        app.config["TESTING"] = True
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            auth.init_auth(app, db_path=db_path)
            conn = auth._get_db()
            conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
                ("lock@example.com", "Lock", "x"),
            )
            conn.commit()
            user = auth._load_user_by_email("lock@example.com")
            conn.close()
            calls = {"n": 0}
            real_get_db = auth._get_db

            def _get_db_flaky():
                if calls["n"] < 2:
                    calls["n"] += 1
                    raise auth.sqlite3.OperationalError("database is locked")
                return real_get_db()

            auth._get_db = _get_db_flaky
            try:
                loaded = auth._load_user_by_id(user.id)
            finally:
                auth._get_db = real_get_db
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.email, "lock@example.com")
        finally:
            Path(db_path).unlink(missing_ok=True)


class CloakingSafetyTests(unittest.TestCase):
    def test_robots_txt_has_no_adsbot_stanza(self):
        from NHL77FINAL import app

        client = app.test_client()
        body = client.get("/robots.txt").get_data(as_text=True)
        self.assertIn("User-agent: *", body)
        self.assertNotIn("AdsBot-Google", body)
        self.assertNotIn("AdsBot-Google-Mobile", body)

    def test_homepage_head_is_not_empty(self):
        from NHL77FINAL import app

        client = app.test_client()
        get_resp = client.get("/")
        head_resp = client.head("/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(head_resp.status_code, 200)
        get_html = get_resp.get_data(as_text=True)
        self.assertGreater(len(get_html), 2000)
        self.assertIn("Prediction", get_html)
        # Flask HEAD uses the same view; body is stripped but length matches GET.
        self.assertGreater(int(head_resp.headers.get("Content-Length") or 0), 2000)


if __name__ == "__main__":
    unittest.main()
