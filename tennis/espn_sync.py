"""Pull ATP/WTA singles (finals + upcoming) into the sandbox tennis DB.

Sandbox only. Uses ESPN's public web scoreboard (same feed as espn.com/tennis).
"""
from __future__ import annotations

import json
import ssl
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tennis.database.paths import DB_PATH, SCHEMA_PATH

ET = ZoneInfo("America/New_York")
_CTX = ssl._create_unverified_context()
_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.espn.com/tennis/scoreboard",
}
_TOURS = ("atp",)  # ATP board includes men's + women's singles at majors; avoids WTA dupes.
_MODEL_DELTAS = [
    ("Grinder2", 0.035),
    ("Takedown", 0.018),
    ("Edge", -0.012),
    ("XSharp", 0.045),
    ("Efficiency", -0.025),
    ("Sharp Consensus", 0.0),
]
HEADSHOT_CACHE = Path(__file__).resolve().parent / "database" / "headshots.json"
FLAG_CACHE = Path(__file__).resolve().parent / "database" / "flags.json"
_HEADSHOT_TMPL = "https://a.espncdn.com/i/headshots/tennis/players/full/{}.png"


def _today_et() -> datetime.date:
    return datetime.now(ET).date()


def _et_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(iso)[:10]


def _et_iso(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(ET)
        return dt.isoformat(timespec="minutes")
    except Exception:
        return str(iso)


def _fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_HDR)
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _scoreboard_urls() -> list[str]:
    today = _today_et()
    urls: list[str] = []
    for tour in _TOURS:
        urls.append(
            f"https://site.web.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
        )
        # Past finals windows + next few days for upcoming slate.
        for i in (0, 1, 2, 3, 4, 7, 10, 14):
            ds = (today - timedelta(days=i)).strftime("%Y%m%d")
            urls.append(
                f"https://site.web.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={ds}"
            )
        for i in (1, 2, 3):
            ds = (today + timedelta(days=i)).strftime("%Y%m%d")
            urls.append(
                f"https://site.web.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={ds}"
            )
    return urls


def re_sandbox(name: str) -> bool:
    return "sandbox" in str(name or "").lower()


def _surface(ev: dict[str, Any]) -> str:
    notes = ev.get("notes") or []
    blob = " ".join(str(n.get("headline") or n.get("text") or "") for n in notes if isinstance(n, dict))
    venue = ev.get("venue") or {}
    blob += " " + str(venue.get("fullName") or "")
    low = blob.lower()
    if "clay" in low:
        return "clay"
    if "grass" in low:
        return "grass"
    return "hard"


def _athlete_id(comp: dict[str, Any]) -> str | None:
    aid = comp.get("id")
    if aid is not None and str(aid).isdigit():
        return str(aid)
    ath = comp.get("athlete") or {}
    for link in ath.get("links") or []:
        href = str(link.get("href") or "")
        if "/id/" in href:
            part = href.split("/id/", 1)[-1].split("/", 1)[0]
            if part.isdigit():
                return part
    return None


def _player_name(comp: dict[str, Any]) -> str:
    ath = comp.get("athlete") or {}
    return str(ath.get("displayName") or ath.get("fullName") or comp.get("displayName") or "").strip()


def _parse_singles(
    data: dict[str, Any],
    tour: str,
    *,
    include_upcoming: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Return (matches, name->espn_id, name->flag_url) for singles competitions."""
    out: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    flags: dict[str, str] = {}
    for ev in data.get("events") or []:
        tourney = ev.get("shortName") or ev.get("name") or tour.upper()
        if re_sandbox(tourney):
            tourney = "ATP Tour" if tour == "atp" else "WTA Tour"
        for g in ev.get("groupings") or []:
            grouping = g.get("grouping") or {}
            gname = str(grouping.get("displayName") or "")
            slug = str(grouping.get("slug") or "")
            blob = f"{gname} {slug}".lower()
            if "double" in blob:
                continue
            if "single" not in blob:
                continue
            for c in g.get("competitions") or []:
                st = ((c.get("status") or {}).get("type") or {})
                st_name = str(st.get("name") or "")
                completed = bool(st.get("completed"))
                comps = c.get("competitors") or []
                if len(comps) < 2:
                    continue
                circuit = c.get("circuit") or c.get("type") or {}
                circuit_blob = f"{circuit.get('text') or ''} {circuit.get('slug') or ''}".lower()
                if circuit_blob and "double" in circuit_blob:
                    continue
                names: list[str] = []
                aids: list[str | None] = []
                winner = None
                set_w = [0, 0]
                for i, x in enumerate(comps[:2]):
                    n = _player_name(x)
                    names.append(n)
                    aid = _athlete_id(x)
                    aids.append(aid)
                    ath = x.get("athlete") or {}
                    flag = ((ath.get("flag") or {}).get("href") or "").strip()
                    key = " ".join(n.lower().split()) if n else ""
                    if key and aid and n.upper() != "TBD":
                        ids[key] = aid
                    if key and flag and n.upper() != "TBD":
                        flags[key] = flag
                    if x.get("winner"):
                        winner = n
                    for ls in x.get("linescores") or []:
                        if ls.get("winner"):
                            set_w[i] += 1
                if not names[0] or not names[1]:
                    continue
                if names[0].upper() == "TBD" or names[1].upper() == "TBD":
                    continue
                mid = str(c.get("id") or f"{tour}-{names[0]}-{names[1]}-{_et_date(c.get('date'))}")
                start = c.get("date") or c.get("startDate")
                base = {
                    "match_id": f"tennis-{mid}",
                    "match_date": _et_date(start),
                    "start_iso": _et_iso(start),
                    "tournament": f"{tourney}" if tourney else tour.upper(),
                    "surface": _surface(ev),
                    "player_a": names[0],
                    "player_b": names[1],
                    "espn_id_a": aids[0],
                    "espn_id_b": aids[1],
                }
                if completed and st_name == "STATUS_FINAL" and winner:
                    out.append(
                        {
                            **base,
                            "winner": winner,
                            "sets_a": set_w[0],
                            "sets_b": set_w[1],
                            "status": "final",
                        }
                    )
                elif include_upcoming and not completed and st_name in (
                    "STATUS_SCHEDULED",
                    "STATUS_IN_PROGRESS",
                    "STATUS_DELAYED",
                ):
                    out.append(
                        {
                            **base,
                            "winner": None,
                            "sets_a": None,
                            "sets_b": None,
                            "status": "scheduled" if st_name == "STATUS_SCHEDULED" else "live",
                        }
                    )
    return out, ids, flags


def _singles_finals(data: dict[str, Any], tour: str) -> list[dict[str, Any]]:
    rows, _, _ = _parse_singles(data, tour, include_upcoming=False)
    return [r for r in rows if r.get("status") == "final"]


def fetch_completed_singles() -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    logos: dict[str, str] = {}
    flags: dict[str, str] = {}
    for url in _scoreboard_urls():
        tour = "wta" if "/wta/" in url else "atp"
        try:
            data = _fetch_json(url)
        except Exception:
            continue
        rows, ids, fl = _parse_singles(data, tour, include_upcoming=False)
        logos.update(ids)
        flags.update(fl)
        for row in rows:
            if row.get("match_id") and row.get("status") == "final":
                by_id[row["match_id"]] = row
    _merge_headshot_cache(logos)
    _merge_flag_cache(flags)
    rows = [r for r in by_id.values() if r.get("match_date")]
    rows.sort(key=lambda r: (r["match_date"], r["match_id"]))
    return rows


def fetch_tennis_slate() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, str], dict[str, str]
]:
    """All finals + upcoming singles, plus name→espn id and name→flag maps."""
    finals: dict[str, dict[str, Any]] = {}
    upcoming: dict[str, dict[str, Any]] = {}
    logos: dict[str, str] = {}
    flags: dict[str, str] = {}
    for url in _scoreboard_urls():
        tour = "wta" if "/wta/" in url else "atp"
        try:
            data = _fetch_json(url)
        except Exception:
            continue
        rows, ids, fl = _parse_singles(data, tour, include_upcoming=True)
        logos.update(ids)
        flags.update(fl)
        for row in rows:
            mid = row.get("match_id")
            if not mid:
                continue
            if row.get("status") == "final":
                finals[mid] = row
            else:
                upcoming[mid] = row
    for mid in list(upcoming):
        if mid in finals:
            upcoming.pop(mid, None)
    f_rows = [r for r in finals.values() if r.get("match_date")]
    u_rows = [r for r in upcoming.values() if r.get("match_date")]
    f_rows.sort(key=lambda r: (r["match_date"], r.get("start_iso") or "", r["match_id"]))
    u_rows.sort(key=lambda r: (r["match_date"], r.get("start_iso") or "", r["match_id"]))
    return f_rows, u_rows, logos, flags


def load_flag_cache() -> dict[str, str]:
    if not FLAG_CACHE.is_file():
        return {}
    try:
        data = json.loads(FLAG_CACHE.read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in (data or {}).items() if k and v}
    except Exception:
        return {}


def _merge_flag_cache(flags: dict[str, str]) -> dict[str, str]:
    cur = load_flag_cache()
    changed = False
    for k, v in (flags or {}).items():
        key = " ".join(str(k).lower().split())
        if key and v and cur.get(key) != str(v):
            cur[key] = str(v)
            changed = True
    if changed:
        try:
            FLAG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            FLAG_CACHE.write_text(json.dumps(cur, indent=0, sort_keys=True), encoding="utf-8")
        except Exception:
            pass
    return cur


def flag_url_for(name: str | None) -> str | None:
    key = " ".join(str(name or "").strip().lower().split())
    if not key:
        return None
    return load_flag_cache().get(key) or None


def load_headshot_cache() -> dict[str, str]:
    if not HEADSHOT_CACHE.is_file():
        return {}
    try:
        data = json.loads(HEADSHOT_CACHE.read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in (data or {}).items() if k and v}
    except Exception:
        return {}


def _merge_headshot_cache(ids: dict[str, str]) -> dict[str, str]:
    cur = load_headshot_cache()
    changed = False
    for k, v in (ids or {}).items():
        key = " ".join(str(k).lower().split())
        if key and v and cur.get(key) != str(v):
            cur[key] = str(v)
            changed = True
    if changed:
        try:
            HEADSHOT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            HEADSHOT_CACHE.write_text(json.dumps(cur, indent=0, sort_keys=True), encoding="utf-8")
        except Exception:
            pass
    return cur


def headshot_url_for(name: str | None) -> str | None:
    key = " ".join(str(name or "").strip().lower().split())
    if not key:
        return None
    aid = load_headshot_cache().get(key)
    if aid:
        return _HEADSHOT_TMPL.format(aid)
    return None


def _elo_probs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    elo: dict[str, float] = {}
    k = 24.0
    out: list[dict[str, Any]] = []
    for r in rows:
        a, b = r["player_a"], r["player_b"]
        ea, eb = elo.get(a, 1500.0), elo.get(b, 1500.0)
        p_a = 1.0 / (1.0 + 10.0 ** ((eb - ea) / 400.0))
        p_a = min(0.92, max(0.08, p_a))
        rec = dict(r)
        rec["win_prob_a"] = round(p_a, 4)
        rec["pick"] = a if p_a >= 0.5 else b
        if r.get("winner"):
            rec["correct"] = 1 if rec["pick"] == r["winner"] else 0
        else:
            rec["correct"] = None
        out.append(rec)
        if r.get("status") == "final" and r.get("winner"):
            sa = 1.0 if r["winner"] == a else 0.0
            elo[a] = ea + k * (sa - p_a)
            elo[b] = eb + k * ((1.0 - sa) - (1.0 - p_a))
    return out


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(Path(SCHEMA_PATH).read_text(encoding="utf-8"))


def write_db(
    finals: list[dict[str, Any]],
    upcoming: list[dict[str, Any]] | None = None,
    db_path: Path | None = None,
) -> int:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    upcoming = list(upcoming or [])
    # Elo walk finals first, then score upcoming with post-finals ratings.
    ordered = list(finals) + list(upcoming)
    graded = _elo_probs(ordered)
    con = sqlite3.connect(str(path))
    try:
        _ensure_schema(con)
        for t in ("grades", "predictions", "matches", "players", "model_runs"):
            con.execute(f"DELETE FROM {t}")
        players: dict[str, None] = {}
        for r in graded:
            players[r["player_a"]] = None
            players[r["player_b"]] = None
        for i, name in enumerate(players, start=1):
            con.execute(
                "INSERT INTO players VALUES (?,?,?,?,?,?)",
                (f"p{i}", name, 1500, 1500, 1500, 1500),
            )
        now = datetime.now(ET).isoformat(timespec="seconds")
        for r in graded:
            mid = r["match_id"]
            status = r.get("status") or ("final" if r.get("winner") else "scheduled")
            con.execute(
                "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    r.get("start_iso") or r["match_date"],
                    r["tournament"],
                    r["surface"],
                    r["player_a"],
                    r["player_b"],
                    r.get("winner"),
                    r.get("sets_a"),
                    r.get("sets_b"),
                    None,
                    None,
                    status,
                ),
            )
            pid = f"pred-{mid}"
            con.execute(
                "INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    mid,
                    now,
                    "ensemble",
                    r["win_prob_a"],
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    abs(r["win_prob_a"] - 0.5) * 2,
                ),
            )
            if status == "final" and r.get("correct") is not None:
                con.execute(
                    "INSERT INTO grades VALUES (?,?,?,?,?,?)",
                    (
                        f"gr-{mid}",
                        pid,
                        r["correct"],
                        None,
                        "WIN" if r["correct"] else "LOSS",
                        now,
                    ),
                )
        con.commit()
    finally:
        con.close()
    return len(graded)


def sync_tennis_db(*, force: bool = False) -> dict[str, Any]:
    """Replace demo slate with live ESPN singles (finals + upcoming) when available."""
    _ = force
    finals, upcoming, logos, flags = fetch_tennis_slate()
    _merge_headshot_cache(logos)
    _merge_flag_cache(flags)
    if len(finals) < 8 and len(upcoming) < 4:
        return {"ok": False, "n": len(finals) + len(upcoming), "reason": "too_few"}
    n = write_db(finals, upcoming)
    return {
        "ok": True,
        "n": n,
        "finals": len(finals),
        "upcoming": len(upcoming),
        "logos": len(logos),
        "flags": len(flags),
    }


def model_sides(player_a: str, player_b: str, win_prob_a: float) -> list[tuple[str, str, float]]:
    """Six named models from the same locked moneyline — CFL-style deltas."""
    out: list[tuple[str, str, float]] = []
    for name, d in _MODEL_DELTAS:
        p = min(0.99, max(0.01, float(win_prob_a) + d))
        fav = player_a if p >= 0.5 else player_b
        fav_p = p if fav == player_a else 1.0 - p
        out.append((name, fav, round(fav_p * 100.0, 1)))
    return out
