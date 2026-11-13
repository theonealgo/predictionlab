import ast
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from queue import Queue
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import weakref

import pytest

ROOT = Path(os.environ.get("PROPS_FIX_ROOT", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("isolated_props_service", ROOT / "props_service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
pool_spec = importlib.util.spec_from_file_location("isolated_player_pool", ROOT / "prop_player_pool.py")
pool = importlib.util.module_from_spec(pool_spec)
pool_spec.loader.exec_module(pool)


def site(tmp_path):
    return SimpleNamespace(DATABASE=str(tmp_path / "original.sqlite"), _PROPS_DISK_DIR=str(tmp_path / "props"),
                           _PROPS_ENGINE_MODULE=None, _props_disk_load=lambda league: None)


def snapshot(value=1):
    return {"ts": 1, "payload": {"props": [{"player_id": "7", "line": value, "input_hash": "saved",
                                          "input_values": {"observations": [1]*100}}],
                               "players": [{"player_id": "7", "stat_observations": {"points": [1]*100}}],
                               "model_validation": {"private": [1]*100}}}


def test_compact_snapshots_preserve_props_but_do_not_hold_training_inputs():
    original = snapshot()
    result = service.compact(original)
    assert result["payload"]["props"][0]["line"] == 1
    assert result["payload"]["props"][0]["input_hash"] == "saved"
    assert "input_values" not in result["payload"]["props"][0]
    assert "stat_observations" not in result["payload"]["players"][0]
    assert "model_validation" not in result["payload"]
    assert original["payload"]["props"][0]["input_values"]


def test_disk_updates_replace_old_memory_and_failed_writes_keep_last_good(tmp_path):
    current = site(tmp_path)
    path = service._path(current, "NBA", "2026-11-12")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(snapshot(1)))
    with patch.dict(service._CACHE, {service._key(current, "NBA", "2026-11-12"): snapshot(99)}, clear=True), \
         patch.dict(service._DISK, {}, clear=True):
        assert service.entries(current, "2026-11-12")["NBA"]["payload"]["props"][0]["line"] == 1
        path.write_text(json.dumps(snapshot(12345)))
        assert service.entries(current, "2026-11-12")["NBA"]["payload"]["props"][0]["line"] == 12345
        path.write_text("incomplete")
        assert service.entries(current, "2026-11-12")["NBA"]["payload"]["props"][0]["line"] == 12345


def test_queue_waits_for_boot_and_never_builds_models_inline(tmp_path):
    current = site(tmp_path)
    queue = Mock()
    queue.get.side_effect = [(0, 0, current, "NBA", "2026-11-12"), StopIteration]
    with patch.object(service, "_QUEUE", queue), patch.object(service, "entries", return_value={}), \
         patch.object(service, "_NOT_BEFORE", service.time.monotonic()+120), \
         patch.object(service.time, "sleep") as sleep, \
         patch.object(service, "run_isolated", return_value=snapshot()) as isolated, \
         patch.object(service, "build_one", side_effect=AssertionError("in-web model run")):
        with pytest.raises(StopIteration):
            service._loop()
    assert sleep.call_args_list[0].args[0] > 119
    isolated.assert_called_once_with(current, "NBA", "2026-11-12")


def test_duplicate_requests_do_not_queue_duplicate_jobs(tmp_path):
    current = site(tmp_path)
    queue = Queue()
    with patch.object(service, "_QUEUE", queue), patch.dict(service._STATE, {}, clear=True):
        for _ in range(20):
            service._enqueue(current, "NBA", "2026-11-12")
        assert queue.qsize() == 1


def test_subprocess_uses_lightweight_worker_and_private_cpu_settings(tmp_path):
    current = site(tmp_path)
    current_path = service._path(current, "NBA", "2026-11-12")
    def launch(command, **kwargs):
        assert command[1] == str(ROOT / "props_worker.py")
        assert "NHL77FINAL" not in " ".join(command)
        assert kwargs["env"]["PL_PROP_MODEL_THREADS"] == "1"
        assert kwargs["env"]["PL_PROPS_HISTORY_WORKERS"] == "8"
        assert kwargs["env"]["OMP_NUM_THREADS"] == "1"
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(json.dumps(snapshot()))
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    with patch.object(service.subprocess, "Popen", side_effect=launch):
        assert service.run_isolated(current, "NBA", "2026-11-12")["payload"]["props"]


def test_failed_subprocess_does_not_replace_the_saved_snapshot(tmp_path):
    current = site(tmp_path)
    path = service._path(current, "NBA", "2026-11-12")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(snapshot()))
    before = path.read_bytes()
    with patch.object(service.subprocess, "Popen", return_value=SimpleNamespace(poll=lambda: 1, returncode=1)):
        with pytest.raises(RuntimeError, match="did not publish"):
            service.run_isolated(current, "NBA", "2026-11-12")
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["timeout", "memory"])
def test_resource_budget_failure_terminates_only_the_publisher(tmp_path, failure):
    process = Mock(pid=999999999, poll=Mock(return_value=None))
    with patch.object(service.subprocess, "Popen", return_value=process), \
         patch.dict(service.os.environ, {"PL_PROPS_JOB_TIMEOUT": "600", "PL_PROPS_JOB_MEMORY_MB": "512"}):
        if failure == "timeout":
            with patch.object(service.time, "monotonic", side_effect=[0, 601]):
                with pytest.raises(RuntimeError, match="time budget"):
                    service.run_isolated(site(tmp_path), "NBA", "2026-11-12")
        else:
            with patch.object(Path, "is_file", return_value=True), \
                 patch.object(Path, "read_text", return_value="VmRSS: 999999999 kB"):
                with pytest.raises(RuntimeError, match="memory budget"):
                    service.run_isolated(site(tmp_path), "NBA", "2026-11-12")
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)


def test_legacy_paths_do_not_call_model_generation():
    tree = ast.parse((ROOT / "NHL77FINAL.py").read_text())
    names = {"_props_preload", "_props_rebuild", "_props_rebuild_in_background", "_props_league_data",
             "player_props_api_players", "player_props_api_diagnostics"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(functions) == len(names)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "get_league_data" for function in functions for node in ast.walk(function))
    preload = next(function for function in functions if function.name == "_props_preload")
    assert "start_daily" in ast.unparse(preload)


def test_streaming_pool_does_not_hold_every_candidates_history():
    class Player(dict):
        pass
    live = weakref.WeakValueDictionary()
    peak = 0
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    def players():
        nonlocal peak
        for index in range(1000):
            row = Player(player_id=str(index), name=f"Player {index}", stat_observations={"points": [
                {"event_id": str(day), "date": (now-timedelta(days=day)).isoformat(), "actual": index}
                for day in range(1, 11)]})
            live[index] = row
            peak = max(peak, len(live))
            yield row
    selected, audit = pool.select_market_players(players(), "NBA", ["points"], lambda *args: True, now)
    assert len(selected) == 100
    assert audit["by_market"]["points"]["eligible_with_history"] == 1000
    assert peak <= 103
    assert min(row["market_rankings"]["points"]["average"] for row in selected) == 900
