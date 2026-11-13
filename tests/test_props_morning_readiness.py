from datetime import datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("morning_props_service", ROOT / "props_service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.mark.parametrize("times", [
    [datetime(2026, 11, 13, 7, tzinfo=ZoneInfo("America/New_York"))],
    [datetime(2026, 11, 13, 23, 59, tzinfo=ZoneInfo("America/New_York")),
     datetime(2026, 11, 14, 0, 0, tzinfo=ZoneInfo("America/New_York"))],
])
def test_new_day_is_queued_before_nine_am(times, tmp_path):
    site = SimpleNamespace(DATABASE=str(tmp_path / "original.sqlite"))
    with patch.object(service.threading, "Thread") as thread:
        service.start_daily(site)
        target = thread.call_args.kwargs["target"]
    with patch.object(service, "datetime") as clock, patch.object(service, "ensure") as ensure, \
         patch.object(service.time, "sleep", side_effect=[None]*len(times)+[StopIteration]):
        clock.now.side_effect = times
        with pytest.raises(StopIteration):
            target()
    assert [call.args[1] for call in ensure.call_args_list] == [stamp.date().isoformat() for stamp in times]


def test_queued_slates_report_updating_not_unavailable(tmp_path):
    site = SimpleNamespace(DATABASE=str(tmp_path / "original.sqlite"), _PROPS_ENGINE_MODULE=None,
                           _props_disk_load=lambda league: None)
    key = service._key(site, "NBA", "2026-11-13")
    with patch.dict(service._STATE, {key: {"status": "queued"}}, clear=True):
        row = service.entries(site, "2026-11-13")["NBA"]["payload"]
    assert row["availability"] == "building"
    assert row["availability_reason"] == "Updating props for this date."
