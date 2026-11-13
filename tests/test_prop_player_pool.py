import copy
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(os.environ.get("PROP_POOL_ROOT", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("prop_player_pool", ROOT / "prop_player_pool.py")
pool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pool
spec.loader.exec_module(pool)
package = ModuleType("category_props_fixture")
package.__path__ = [str(ROOT / "standalone-player-props/backend/app")]
sys.modules[package.__name__] = package
source = importlib.import_module(package.__name__ + ".data_sources")
eligibility = importlib.import_module(package.__name__ + ".player_eligibility")
lines = importlib.import_module(package.__name__ + ".pl_lines")
engine = importlib.import_module(package.__name__ + ".engine")
AS_OF = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
LEAGUES = ("NBA", "WNBA", "NCAAB", "NCAAW", "NFL", "NCAAF", "CFL", "NHL", "MLB", "SOCCER")


def player(identity, values, role="G"):
    return {"player_id": str(identity), "name": f"Player {identity:04}", "team_id": "1", "team": "Fixture",
            "role": role, "position": role, "history_verified": True,
            "stat_history": {key: [value]*10 for key, value in values.items()},
            "stat_observations": {key: [
                {"event_id": str(10000+day), "date": (AS_OF-timedelta(days=day+1)).isoformat(), "actual": value}
                for day in range(10)] for key, value in values.items()}}


def allowed(league):
    return lambda row, market: eligibility.market_allowed(league, row["role"], market, line_source="espn_props")


@pytest.mark.parametrize("league", LEAGUES)
def test_every_supported_sport_has_independent_top100_per_market(league):
    candidates = []
    for index, market in enumerate(source._LEAGUE_PROP_TYPES[league]):
        role = next(role for role in eligibility.roles_for_league(league)
                    if eligibility.market_allowed(league, role, market, line_source="espn_props"))
        candidates.extend(player(index*1000+i, {market: i}, role) for i in range(120))
    before = copy.deepcopy(candidates)
    selected, audit = pool.select_market_players(candidates, league, source._LEAGUE_PROP_TYPES[league],
                                                 allowed(league), AS_OF)
    for market in source._LEAGUE_PROP_TYPES[league]:
        leaders = [row["market_rankings"][market] for row in selected if market in row["market_rankings"]]
        assert len(leaders) == 100
        assert min(row["average"] for row in leaders) == 20
        assert audit["by_market"][market]["selected"] == 100
        assert sorted(row["rank"] for row in leaders) == list(range(1, 101))
    assert len(selected) == 100 * len(source._LEAGUE_PROP_TYPES[league])
    assert candidates == before


def test_stat_specialists_are_not_selected_by_overall_rank_or_ev():
    rows = [player(i, {"points": i, "rebounds": 119-i, "threes": i % 5}) for i in range(120)]
    for row in rows:
        row.update(consensus_rank=1, pick_ev=10000-float(row["player_id"]))
    selected, _ = pool.select_market_players(rows, "NBA", ["points", "rebounds"], allowed("NBA"), AS_OF)
    by_id = {row["player_id"]: row for row in selected}
    assert "points" in by_id["119"]["market_rankings"]
    assert "rebounds" not in by_id["119"]["market_rankings"]
    assert "rebounds" in by_id["0"]["market_rankings"]
    assert "points" not in by_id["0"]["market_rankings"]


def test_future_missing_duplicates_and_genuine_zeros():
    zero, short = player(1, {"threes": 0}), player(2, {"threes": 99})
    short["stat_observations"]["threes"] = short["stat_observations"]["threes"][:9]
    short["stat_observations"]["threes"] += [{"date": AS_OF.isoformat(), "actual": 999, "event_id": "999999"}]
    duplicate = copy.deepcopy(zero)
    duplicate["stat_observations"]["threes"] *= 2
    selected, audit = pool.select_market_players([zero, duplicate, short], "NBA", ["threes"], allowed("NBA"), AS_OF)
    assert len(selected) == 1
    assert selected[0]["market_rankings"]["threes"]["average"] == 0
    assert audit["by_market"]["threes"]["missing_history"] == 1
    assert audit["raw_players"] == 2


def test_uses_latest_ten_and_stable_ties_and_rejects_naive_cutoff():
    row = player(1, {"points": 5})
    row["stat_observations"]["points"].append({"date": (AS_OF-timedelta(days=90)).isoformat(), "actual": 999, "event_id": "888888"})
    tied = player(2, {"points": 5})
    a, _ = pool.select_market_players([row, tied], "NBA", ["points"], allowed("NBA"), AS_OF, limit=1)
    b, _ = pool.select_market_players([tied, row], "NBA", ["points"], allowed("NBA"), AS_OF, limit=1)
    assert a == b
    assert a[0]["market_rankings"]["points"]["average"] == 5
    with pytest.raises(ValueError):
        pool.select_market_players([row], "NBA", ["points"], allowed("NBA"), AS_OF.replace(tzinfo=None))


def test_outside_market_offers_are_rejected_for_real_and_pl_lines():
    row = {**player(1, {"points": 25}), "market_rankings": {"points": {"rank": 1}}}
    offers = [{"player_id": "1", "prop_type": market, "line": 20.5, "line_source": provider}
              for market in ("points", "rebounds") for provider in ("espn_props", "pl_model")]
    result, audit = eligibility.filter_props_by_eligibility(offers, {"1": row}, league="NBA")
    assert len(result) == 2
    assert all(item["prop_type"] == "points" for item in result)
    assert audit["suppress_reasons"]["outside_category_top100"] == 2


def test_generic_rosters_are_not_capped_by_position_or_league():
    response = Mock()
    response.json.return_value = {"athletes": [
        {"id": str(i+1), "fullName": f"Fixture {i}", "position": {"abbreviation": "C"}}
        for i in range(130)]}
    schedule = [{"home_team_id": "1", "home_team": "Home", "away_team_id": "2", "away_team": "Away",
                 "event_id": "999", "start_time": "2030-11-11T00:00:00Z"}]
    with patch.object(source.requests, "get", return_value=response):
        assert len(source.build_top_players("NCAAB", schedule)) == 130


def test_nba_non_whitelisted_stat_specialists_are_not_discarded():
    response = Mock()
    response.json.return_value = {"athletes": [
        {"id": str(i+1), "fullName": f"Unlisted specialist {i}", "position": {"abbreviation": "C"}}
        for i in range(130)]}
    metrics = {"insufficient_data": False, "avg_minutes": 25, "usage_rate": .2,
               "last_10_games_minutes": [25]*10, "history_verified": True}
    with patch.object(source.requests, "get", return_value=response), \
         patch.object(source, "_fetch_nba_player_metrics", return_value=metrics), \
         patch.object(source, "_append_filter_log"):
        result = source.build_validated_nba_player_pool([{"home_team_id": "1", "home_team": "Home"}])
    assert len(result["players"]) == 130
    assert not result["excluded"]


@pytest.mark.parametrize("league", ("NBA", "NFL", "NHL", "MLB", "SOCCER"))
def test_engine_applies_market_membership_before_forecasting(league):
    market = source._LEAGUE_PROP_TYPES[league][0]
    role = next(role for role in eligibility.roles_for_league(league)
                if eligibility.market_allowed(league, role, market, line_source="espn_props"))
    rows = [player(i, {market: i}, role) for i in range(120)]
    def offers(_league, players, **kwargs):
        return [{"player_id": row["player_id"], "prop_type": market, "line": 20.5, "line_source": "espn_props"}
                for row in players]
    with patch.object(engine, "build_validated_nba_player_pool", return_value={"players": rows, "excluded": []}), \
         patch.object(engine, "build_top_players", return_value=rows), \
         patch.object(engine, "hydrate_market_histories", side_effect=lambda league, players, *args: players), \
         patch.object(engine, "fetch_prop_lines", side_effect=offers), \
         patch.object(engine, "_pl_forecast_row", return_value=None), \
         patch.object(engine, "enrich_mlb_players_with_metrics"), \
         patch("prop_models.train_xgboost", return_value={}):
        payload = engine._build_league_payload(league, schedule_override=[], allow_live_lines=True)
    assert len(payload["props"]) == 100
    assert payload["player_pool_version"] == pool.VERSION
    assert all(row["category_ranking"]["sample"] == 10 for row in payload["props"])


def test_history_loading_is_bounded_and_reuses_verified_histories():
    rows = [player(i, {"points": 5}) for i in range(20)]
    for row in rows:
        row["stat_observations"] = {}
    lock = threading.Lock()
    active = peak = 0
    def history(league, identity, market):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.002)
        with lock:
            active -= 1
        original = player(int(identity), {market: 5})
        return {"observations": original["stat_observations"][market], "recent_form": [5]*10}
    with patch.object(lines, "history_stats", side_effect=history) as fetch:
        loaded = lines.hydrate_market_histories("NBA", rows, ["points"], allowed("NBA"), max_workers=3)
        assert fetch.call_count == 20
        lines.hydrate_market_histories("NBA", loaded, ["points"], allowed("NBA"), max_workers=3)
        assert fetch.call_count == 20
    assert 1 < peak <= 3


def test_history_snapshot_reused_after_memory_cache_clear(tmp_path):
    response = Mock()
    response.json.return_value = {"events": {"1": {"gameResult": "W"}}}
    with patch.object(lines, "_HISTORY_CACHE", {}), \
         patch.object(lines, "_history_snapshot", return_value=tmp_path / "history.json"), \
         patch.object(source.requests, "get", return_value=response) as fetch:
        first = lines._history("NBA", "7")
        lines._HISTORY_CACHE.clear()
        assert lines._history("NBA", "7") == first
        assert fetch.call_count == 1
        for index in range(600):
            lines._remember_history(index, {"ts": time.time(), "body": {}})
        assert len(lines._HISTORY_CACHE) == 512


def test_preview_keeps_category_rank_and_coverage_server_side():
    spec = importlib.util.spec_from_file_location("category_hub_fixture", ROOT / "sports_props_hub.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    today = datetime.now(timezone.utc).astimezone(hub.ET).date().isoformat()
    ranking = {"rank": 1, "average": 20, "sample": 10}
    entry = {"ts": time.time(), "payload": {"requested_date": today, "player_pool_version": pool.VERSION,
             "eligibility_audit": {"pool": {"by_market": {"points": {"selected": 1, "published_players": 1}}}},
             "props": [{"player_name": "Fixture", "prop_type": "points", "line": 20.5,
                        "line_source": "pl_model", "category_ranking": ranking}]}}
    result = hub.preview("NBA", entry, day=today)
    # No matching event date: no invented rows or exposed ranking internals.
    assert result["items"] == []
    assert "category_pools" not in result
    assert "player_pool_version" not in result


def test_hub_preview_retains_each_category_and_complete_list_remains_available():
    spec = importlib.util.spec_from_file_location("category_preview_fixture", ROOT / "sports_props_hub.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    day = datetime.now(hub.ET).date().isoformat()
    rows = [{"player_name": f"Player {index}", "player_id": str(index), "prop_type": market,
             "line": 20.5, "line_source": "pl_model", "event_start": day+"T23:00:00-05:00",
             "category_ranking": {"rank": index+1, "average": 30-index, "sample": 10}}
            for market in ("points", "rebounds", "threes") for index in range(6)]
    entry = {"ts": time.time(), "payload": {"requested_date": day, "player_pool_version": pool.VERSION,
             "props": rows}}
    result = hub.preview("NBA", entry, limit=3, day=day)
    assert len(result["items"]) == 9
    assert result["total"] == 18
    assert {row["prop_type"] for row in result["items"]} == {"points", "rebounds", "threes"}
    assert all("category_ranking" not in row for row in result["items"])
    assert len(hub.preview("NBA", entry, limit=None, day=day)["items"]) == 18
    assert not hub.preview("NBA", entry, limit=0, day=day)["items"]


def test_old_overall_pool_cache_queues_category_refresh(tmp_path):
    spec = importlib.util.spec_from_file_location("category_service_fixture", ROOT / "props_service.py")
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    day = datetime.now(service.ET).date().isoformat()
    entry = {"ts": time.time(), "payload": {"model_registry_version": service.MODEL_VERSION}}
    site = SimpleNamespace(DATABASE=str(tmp_path / "original.sqlite"))
    with patch.object(service, "LEAGUES", ("NBA",)), \
         patch.object(service, "_THREAD", SimpleNamespace(is_alive=lambda: True)), \
         patch.object(service, "entries", return_value={"NBA": entry}), \
         patch.object(service, "_enqueue") as enqueue:
        service.ensure(site, day, "NBA")
        enqueue.assert_called_once_with(site, "NBA", day, 0)
        enqueue.reset_mock()
        entry["payload"]["player_pool_version"] = pool.VERSION
        service.ensure(site, day, "NBA")
        enqueue.assert_not_called()
