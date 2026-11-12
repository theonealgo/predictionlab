"""Stat-parser regression tests without importing the app or writing ledgers."""
import ast
import copy
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "standalone-player-props/backend/app/data_sources.py"


def functions(body):
    names = {"_parse_makes", "_parse_attempts", "_num", "_clamp", "_fetch_nba_player_metrics"}
    nodes = [node for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"Dict": Dict, "Optional": Optional, "math": math,
                 "time": SimpleNamespace(time=lambda: 1_000_000),
                 "_PLAYER_METRICS_CACHE": {}, "_PLAYER_METRICS_TTL": 600,
                 "requests": SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(
                     raise_for_status=lambda: None, json=lambda: copy.deepcopy(body)))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def payload(shooting="2-8"):
    return {"labels": ["MIN", "FG", "FT", "TO", "PTS", "REB", "AST", "3PT"],
            "seasonTypes": [{"displayName": "Regular Season", "categories": [{"events": [
                {"stats": ["30", "7-15", "4-5", "1", "20", "5", "6", shooting]}
                for _ in range(6)]}]}]}


class MadeThreesTests(unittest.TestCase):
    def test_projects_makes_not_attempts(self):
        result = functions(payload())["_fetch_nba_player_metrics"]("fixture")
        self.assertFalse(result["insufficient_data"])
        for key in ("stats_last5", "stats_last10", "stats_weighted"):
            self.assertEqual(result[key]["threes"], 2)
        self.assertEqual(result["stats_last5"]["points"], 20)

    def test_verified_zero_is_valid(self):
        result = functions(payload("0-0"))["_fetch_nba_player_metrics"]("fixture")
        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["stats_weighted"]["threes"], 0)

    def test_bad_counts_are_not_invented_zero(self):
        for value in ("", "DNP", "4-2", "-1-5", "nan-8", "1.5-8", None):
            with self.subTest(value=value):
                self.assertTrue(functions(payload(value))["_fetch_nba_player_metrics"]("fixture")["insufficient_data"])

    def test_attempts_helper_remains_unchanged_for_usage(self):
        namespace = functions(payload())
        self.assertEqual(namespace["_parse_attempts"]("7-15"), 15)
        self.assertEqual(namespace["_parse_makes"]("7-15"), 7)
