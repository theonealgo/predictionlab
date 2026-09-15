"""Unit tests for PageSpeed Insights wiring inside site_checker."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
sys.path.insert(0, str(QA))
sys.path.insert(0, str(ROOT))

from pagespeed_api_checker import (  # noqa: E402
    PagespeedAuditor,
    public_psi_base,
    soft_fail_reasons,
)


class _Report:
    def __init__(self):
        self.checks = []

    def add(self, result):
        self.checks.append(result)


def _CR(**kwargs):
    return SimpleNamespace(**kwargs)


def test_public_psi_base_rejects_local(monkeypatch):
    monkeypatch.delenv("PAGESPEED_BASE_URL", raising=False)
    assert public_psi_base("http://127.0.0.1:5052") is None
    assert public_psi_base("https://predictionlab.io") == "https://predictionlab.io"


def test_public_psi_base_override(monkeypatch):
    monkeypatch.setenv("PAGESPEED_BASE_URL", "https://predictionlab.io")
    assert public_psi_base("http://127.0.0.1:5052") == "https://predictionlab.io"


def test_soft_fail_reasons_floors():
    assert soft_fail_reasons({"categories": {"performance": 40, "accessibility": 95}},
                             perf_floor=50, a11y_floor=90)
    assert not soft_fail_reasons({"categories": {"performance": 70, "accessibility": 95}},
                                 perf_floor=50, a11y_floor=90)


def test_auditor_warns_without_key(monkeypatch):
    monkeypatch.setattr("pagespeed_api_checker.try_load_api_key", lambda: None)
    report = _Report()
    PagespeedAuditor("https://predictionlab.io", report, _CR).run()
    assert report.checks
    assert report.checks[0].status == "WARN"
    assert "api key" in report.checks[0].label


def test_auditor_warns_without_public_base(monkeypatch):
    monkeypatch.setattr("pagespeed_api_checker.try_load_api_key", lambda: "fake-key")
    monkeypatch.delenv("PAGESPEED_BASE_URL", raising=False)
    report = _Report()
    PagespeedAuditor("http://127.0.0.1:5052", report, _CR).run()
    assert report.checks
    assert report.checks[0].status == "WARN"
    assert "public base" in report.checks[0].label


def test_auditor_records_pass(monkeypatch):
    monkeypatch.setattr("pagespeed_api_checker.try_load_api_key", lambda: "fake-key")
    monkeypatch.delenv("PAGESPEED_BASE_URL", raising=False)

    def fake_run(url, *, strategy, api_key, save_raw_path=None, quiet=False, categories=None):
        return {
            "url": url,
            "strategy": strategy,
            "elapsed_s": 1.0,
            "categories": {
                "performance": 80,
                "accessibility": 95,
                "best-practices": 90,
                "seo": 90,
            },
            "metrics": {"largest-contentful-paint": "2.0 s"},
            "failed_audits": [],
        }

    monkeypatch.setattr("pagespeed_api_checker.run_pagespeed", fake_run)
    report = _Report()
    PagespeedAuditor(
        "https://predictionlab.io",
        report,
        _CR,
        paths=["/mlb-picks"],
        strategy="mobile",
        perf_floor=50,
        a11y_floor=90,
    ).run()
    statuses = {c.status for c in report.checks}
    assert "PASS" in statuses
    assert "FAIL" not in statuses


def test_site_checker_lists_pagespeed():
    from audit_config import FULL_MODE_AUDITORS, SHIP_MODE_AUDITORS, PAGESPEED_MODE_AUDITORS
    assert "pagespeed" in FULL_MODE_AUDITORS
    assert "pagespeed" in SHIP_MODE_AUDITORS
    assert PAGESPEED_MODE_AUDITORS == ["pagespeed"]
