from copy import deepcopy
import threading
import time

from live_scores import ScoreCache, parse_scoreboard

DAY = "2026-11-11"
ITEM = {"sport": "NHL", "away": "Tampa Bay Lightning", "home": "Buffalo Sabres",
        "awayPct": 47.8, "homePct": 52.2, "live": True}


def payload(state="in", scores=("2", "0"), event_id="123", date="2026-11-12T00:00Z"):
    return {"events": [{"id": event_id, "competitions": [{
        "id": event_id, "date": date,
        "status": {"period": 1, "displayClock": "0:00",
                   "type": {"state": state, "name": "STATUS_FINAL" if state == "post" else "",
                            "shortDetail": "End of 1st"}},
        "competitors": [
            {"team": {"displayName": "Tampa Bay Lightning"}, "score": scores[0]},
            {"team": {"displayName": "Buffalo Sabres"}, "score": scores[1]},
        ],
    }]}]}


def cached(data=None, age=0):
    cache = ScoreCache(clock=lambda: 1000)
    cache.entries[("hockey/nhl", DAY)] = (1000-age, parse_scoreboard(data or payload(), DAY))
    return cache


def test_live_zero_and_unchanged_predictions():
    original = deepcopy(ITEM)
    row = cached().enrich([ITEM], DAY, refresh=False)[0]
    assert row["live"] and row["awayScore"] == "2" and row["homeScore"] == "0"
    assert row["statusDetail"] == "End of 1st" and row["clock"] == "0:00"
    assert row["homePct"] == ITEM["homePct"] and row["awayPct"] == ITEM["awayPct"]
    assert ITEM == original


def test_scheduled_zeros_are_not_scores_or_live():
    row = cached(payload("pre", ("0", "0"))).enrich([ITEM], DAY, refresh=False)[0]
    assert not row["live"] and row["scoreState"] == "pre"
    assert row["homeScore"] is None and row["awayScore"] is None


def test_final_is_removed():
    assert cached(payload("post")).enrich([ITEM], DAY, refresh=False) == []


def test_postponed_is_removed_and_delay_is_not_live():
    data = payload("pre")
    data["events"][0]["competitions"][0]["status"]["type"]["name"] = "STATUS_POSTPONED"
    assert cached(data).enrich([ITEM], DAY, False) == []
    data["events"][0]["competitions"][0]["status"]["type"]["name"] = "STATUS_DELAYED"
    row = cached(data).enrich([ITEM], DAY, False)[0]
    assert not row["live"] and row["homeScore"] is None


def test_nested_tennis_matches_and_unicode_names():
    data = payload()
    competition = data["events"][0]["competitions"][0]
    competition["competitors"] = [
        {"athlete": {"displayName": "Carlos Alcaraz"}, "score": "0"},
        {"athlete": {"displayName": "Félix Auger-Aliassime"}, "score": "1"},
    ]
    data = {"events": [{"groupings": [{"competitions": [competition]}]}]}
    records = parse_scoreboard(data, DAY)
    cache = ScoreCache(clock=lambda: 1000)
    cache.entries[("tennis/atp", DAY)] = (1000, records)
    item = dict(ITEM, sport="Tennis", away="Felix Auger Aliassime", home="Carlos Alcaraz")
    row = cache.enrich([item], DAY, False)[0]
    assert row["awayScore"] == "1" and row["homeScore"] == "0" and row["scoreLabel"] == "Sets"


def test_reverse_orientation_keeps_actual_teams_scores():
    item = dict(ITEM, away=ITEM["home"], home=ITEM["away"])
    row = cached().enrich([item], DAY, refresh=False)[0]
    assert row["awayScore"] == "0" and row["homeScore"] == "2"


def test_reject_wrong_team_date_and_sport():
    assert cached().enrich([dict(ITEM, away="Buffalo Bulls")], DAY, False)[0]["awayScore"] is None
    assert not parse_scoreboard(payload(date="2026-11-11T00:00Z"), DAY)
    assert cached().enrich([dict(ITEM, sport="NBA")], DAY, False)[0]["homeScore"] is None


def test_doubleheader_requires_exact_id():
    data = payload()
    data["events"] += payload(event_id="456")["events"]
    cache = cached(data)
    row = cache.enrich([ITEM], DAY, False)[0]
    assert row["awayScore"] is None and "Multiple" in row["scoreReason"]
    assert cache.enrich([dict(ITEM, eventId="456")], DAY, False)[0]["awayScore"] == "2"


def test_missing_score_not_zero_and_stale_updates_are_labeled():
    row = cached(payload(scores=(None, "0"))).enrich([ITEM], DAY, False)[0]
    assert row["awayScore"] is None and row["homeScore"] == "0" and row["scoreReason"]
    row = cached(age=121).enrich([ITEM], DAY, False)[0]
    assert row["scoreState"] == "delayed" and not row["live"] and row["awayScore"] == "2"
    assert cached(age=601).enrich([ITEM], DAY, False)[0]["awayScore"] is None


def test_nonblocking_single_background_fetch_and_failure_preserves_cache():
    release, started = threading.Event(), threading.Event()
    calls = []

    def fetch(path, day):
        calls.append((path, day))
        started.set()
        release.wait(2)
        return payload()

    cache = ScoreCache(fetch=fetch)
    begin = time.monotonic()
    cache.enrich([ITEM], DAY)
    assert time.monotonic() - begin < .1
    assert started.wait(1)
    cache.enrich([ITEM], DAY)
    release.set()
    deadline = time.monotonic() + 3
    while cache.running and time.monotonic() < deadline:
        time.sleep(.01)
    assert len(calls) == 1
    assert cache.enrich([ITEM], DAY, False)[0]["homeScore"] == "0"
    cache.fetch = lambda *args: (_ for _ in ()).throw(RuntimeError("offline"))
    cache._refresh(("hockey/nhl", DAY))
    assert cache.enrich([ITEM], DAY, False)[0]["awayScore"] == "2"
