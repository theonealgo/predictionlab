import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_navigation_crawl", ROOT / "qa/navigation_crawl.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("href", [
    "/logout", "/checkout", "/billing", "/admin/rebuild", "/api/refresh",
    "/stripe/cancel", "/create-portal-session", "/set-password?token=x",
    "/auth/google/callback?code=x", "/somewhere?action=delete", "/somewhere?force=1",
])
def test_account_or_mutation_links_are_not_visited(href):
    target, reason = audit.local_path(href, "/")
    assert target is None and reason


@pytest.mark.parametrize("href", [
    "https://buy.stripe.com/example", "https://www.facebook.com/predictionlab",
    "http://127.0.0.1:5133/", "http://localhost:5000/anything",
])
def test_external_or_unrelated_local_service_is_not_contacted(href):
    assert audit.local_path(href, "/")[0] is None


def test_production_internal_urls_are_checked_locally():
    assert audit.local_path("https://predictionlab.io/nba-results?view=chart", "/blog") == (
        "/nba-results?view=chart", None,
    )
    assert audit.local_path(" /blog#news ", "/") == ("/blog", None)


class Response:
    def __init__(self, code=200, location=None, body="<html>Working page</html>"):
        self.status_code = code
        self.headers = {"Content-Type": "text/html"}
        if location:
            self.headers["Location"] = location
        self.text = body


def test_redirect_to_billing_is_recorded_not_followed():
    with patch.object(audit.requests, "get", return_value=Response(302, "https://buy.stripe.com/example")) as get:
        row = audit.fetch("/plans")
    assert row["kind"] == "handoff"
    assert get.call_count == 1


def test_redirect_loop_is_reported():
    with patch.object(audit.requests, "get", return_value=Response(301, "/same")) as get:
        row = audit.fetch("/same")
    assert row["kind"] == "broken" and row["reason"] == "redirect-loop"
    assert get.call_count == 7


def test_expected_paywall_is_not_reported_as_a_missing_page():
    with patch.object(audit.requests, "get", return_value=Response(403)):
        assert audit.fetch("/performance")["kind"] == "protected"


def test_broken_response_and_destination_links_are_detected():
    with patch.object(audit.requests, "get", return_value=Response(
            body='<html><a href="/blog">Blog</a>Internal Server Error</html>')):
        row = audit.fetch("/daily-report")
    assert row["kind"] == "broken" and row["links"] == ["/blog"]
