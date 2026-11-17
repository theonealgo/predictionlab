import ast
from datetime import date
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "site_checker/claudeplchecker.py"
if not SOURCE.exists():
    pytest.skip("External owner checker is not present in this checkout", allow_module_level=True)
spec = importlib.util.spec_from_file_location("owner_claude_checker_fixture", SOURCE)
checker_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker_module
spec.loader.exec_module(checker_module)


def checker(monkeypatch, payload=None):
    c = checker_module.Checker(checker_module.Config(
        base="http://127.0.0.1:5122", groups=("values", "paywall", "links"), delay=0,
    ))
    data = {
        "/api/web/sports": {"sports": [{"slug": "nba-picks", "key": "NBA", "name": "NBA", "status": "Today"}]},
        "/api/web/sports/nba-picks/cards": payload if payload is not None else
            {"cards": [{"html": "<div>Card</div>", "date": "2099-01-01"}]},
    }
    monkeypatch.setattr(c, "api_json", lambda path: data.get(path, {}))
    for name in ("sports_problems", "live_problems", "season_problems", "json_problems", "status_problems"):
        monkeypatch.setattr(checker_module.rules, name, lambda *args, **kwargs: [])
    monkeypatch.setattr(checker_module.rules, "page_value_problems",
                        lambda *args, **kwargs: ([("WARN", "Game A: missing forecast"),
                                                 ("WARN", "Game A: missing forecast")], 0, 1))
    return c


def test_original_checker_bug_is_reproduced_and_fixed(monkeypatch):
    c = checker(monkeypatch)
    before = ROOT / ".checkpoints/navigation-audit/claudeplchecker.before.py"
    if before.exists():
        tree = ast.parse(before.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Checker")
        fn = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "check_values")
        namespace = {"date": date, "rules": checker_module.rules}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "original-checker-regression", "exec"), namespace)
        with pytest.raises(TypeError, match="not iterable"):
            namespace["check_values"](c)
        c.run = checker_module.Run(c.cfg)
    c.check_values()
    assert c.cards["nba-picks"]["upcoming"] == 1
    assert any("missing forecast" in finding.message for finding in c.run.findings)
    assert c.run.metrics["values"].startswith("1 cards")


@pytest.mark.parametrize("payload", [{"cards": 2}, {"cards": 0}, {"cards": None}, []])
def test_invalid_card_schema_produces_finding_not_traceback(monkeypatch, payload):
    c = checker(monkeypatch, payload)
    c.check_values()
    assert any(f.level == "FAIL" and "cards API" in f.message for f in c.run.findings)


def test_nonobject_cards_are_reported_and_valid_cards_still_checked(monkeypatch):
    c = checker(monkeypatch, {"cards": [None, {"html": "card", "date": "2099-01-01"}]})
    c.check_values()
    assert c.cards["nba-picks"]["upcoming"] == 1
    assert any("non-object card" in f.message for f in c.run.findings)


def test_localhost_does_not_suppress_a_premium_leak(monkeypatch):
    c = checker(monkeypatch)
    monkeypatch.setattr(c, "api_json", lambda path: {"premium": True, "loggedIn": False})
    c.check_paywall()
    assert any(f.group == "paywall" and f.level == "FAIL" for f in c.run.findings)
    assert "every visitor counts as premium" not in c.cfg.view


def test_link_stage_does_not_probe_account_or_payment_actions(monkeypatch):
    c = checker(monkeypatch)
    c.scans["/"] = SimpleNamespace(hrefs=["/checkout", "/logout", "/billing", "/admin/rebuild", "/blog"])
    c.responses["/"] = SimpleNamespace(final_url=c.origin + "/")
    seen = []
    def probes(urls):
        seen.extend(urls)
        return {url: checker_module.Response(url=url, final_url=url, status=200) for url in urls}
    monkeypatch.setattr(c, "probe_all", probes)
    c.check_links()
    assert seen == [c.origin + "/blog"]
