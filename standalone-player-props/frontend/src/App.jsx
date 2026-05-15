import React, { useEffect, useMemo, useState } from "react";
import { fetchProps, fetchResults } from "./api";

const LEAGUES = ["NBA", "NHL", "NFL", "MLB", "SOCCER", "NCAAB", "WNBA", "NCAAF", "NCAAW"];

const PROP_LABELS = {
  points: "Points",
  rebounds: "Rebounds",
  assists: "Assists",
  threes: "3PT Made",
  steals: "Steals",
  shots_on_goal: "Shots on Goal",
  goals: "Goals",
  hits: "Hits",
  strikeouts: "Strikeouts",
  runs: "Runs",
  rbis: "RBI",
  home_runs: "HR",
  passing_yards: "Pass Yds",
  rushing_yards: "Rush Yds",
  receiving_yards: "Rec Yds",
  receptions: "Rec",
  shots: "Shots",
  shots_on_target: "On Target",
};

const MODEL_LABELS = [
  ["glicko2", "Grinder2"],
  ["trueskill", "Takedown"],
  ["xgboost", "Edge"],
  ["xsharp", "XSharp"],
  ["sharp_consensus", "Consensus"],
];

function fmt(v) {
  if (v == null) return "—";
  return PROP_LABELS[v] || v.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function groupByPlayer(rows) {
  const map = new Map();
  for (const row of rows) {
    const key = row.player_id;
    if (!map.has(key)) {
      map.set(key, {
        player_id: row.player_id,
        player_name: row.player_name,
        team: row.team,
        props: [],
      });
    }
    map.get(key).props.push(row);
  }
  return [...map.values()];
}

function groupResultsByPlayer(rows) {
  const map = new Map();
  for (const row of rows) {
    const key = row.player_id || row.player_name;
    if (!map.has(key)) {
      map.set(key, {
        player_id: key,
        player_name: row.player_name,
        team: row.team,
        results: [],
        wins: 0,
        losses: 0,
        pushes: 0,
      });
    }
    const g = map.get(key);
    g.results.push(row);
    if (row.result === "WIN") g.wins++;
    else if (row.result === "LOSS") g.losses++;
    else if (row.result === "PUSH") g.pushes++;
  }
  return [...map.values()];
}

export default function App() {
  const [propsRows, setPropsRows] = useState([]);
  const [selectedLeague, setSelectedLeague] = useState("");
  const [appliedLeague, setAppliedLeague] = useState("");
  const [view, setView] = useState("props");
  const [resultsRows, setResultsRows] = useState([]);
  const [resultsSummary, setResultsSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState(null);

  useEffect(() => {
    if (!appliedLeague) {
      setPropsRows([]);
      setResultsRows([]);
      setResultsSummary(null);
      return;
    }
    async function run() {
      setLoading(true);
      setApiError(null);
      try {
        if (view === "props") {
          const r = await fetchProps({ league: appliedLeague });
          setPropsRows(r.items || []);
        } else {
          const rr = await fetchResults(appliedLeague);
          setResultsRows(rr.items || []);
          setResultsSummary(rr.summary || null);
        }
      } catch (e) {
        const msg =
          e instanceof TypeError && e.message === "Failed to fetch"
            ? "Props service is offline. Please try again shortly."
            : e instanceof Error
              ? e.message
              : String(e);
        setApiError(msg);
        setPropsRows([]);
        setResultsRows([]);
        setResultsSummary(null);
      } finally {
        setLoading(false);
      }
    }
    run();
  }, [appliedLeague, view]);

  const playerGroups = useMemo(() => groupByPlayer(propsRows), [propsRows]);
  const resultGroups = useMemo(() => groupResultsByPlayer(resultsRows), [resultsRows]);

  const overallRecord = useMemo(() => {
    if (resultsSummary?.overall) return resultsSummary.overall;
    const wins = resultGroups.reduce((s, g) => s + g.wins, 0);
    const losses = resultGroups.reduce((s, g) => s + g.losses, 0);
    return { wins, losses };
  }, [resultsSummary, resultGroups]);

  function run() {
    if (!selectedLeague) return;
    if (selectedLeague === appliedLeague) {
      // force re-fetch by toggling
      setAppliedLeague("");
      setTimeout(() => setAppliedLeague(selectedLeague), 0);
    } else {
      setAppliedLeague(selectedLeague);
    }
  }

  return (
    <div className="app">
      {/* ── Controls bar ── */}
      <div className="controls-bar">
        <div className="controls-left">
          <div className="tab-group">
            <button
              className={`tab-btn${view === "props" ? " active" : ""}`}
              onClick={() => setView("props")}
            >
              Props
            </button>
            <button
              className={`tab-btn${view === "results" ? " active" : ""}`}
              onClick={() => setView("results")}
            >
              Results
            </button>
          </div>
          <select
            className="league-select"
            value={selectedLeague}
            onChange={(e) => setSelectedLeague(e.target.value)}
          >
            <option value="">Select sport</option>
            {LEAGUES.map((lg) => (
              <option key={lg} value={lg}>{lg}</option>
            ))}
          </select>
        </div>
        <button
          className="run-btn"
          onClick={run}
          disabled={loading || !selectedLeague}
        >
          {loading ? "Loading…" : "Load Props"}
        </button>
      </div>

      {/* ── Error ── */}
      {apiError && (
        <div className="api-error" role="alert">
          <strong>Error:</strong> {apiError}
        </div>
      )}

      {/* ── Props view ── */}
      {view === "props" && !loading && (
        <>
          {playerGroups.length === 0 && appliedLeague && (
            <div className="empty-state">No props available for {appliedLeague}. Try again shortly.</div>
          )}
          {!appliedLeague && (
            <div className="empty-state">Select a sport and click <strong>Load Props</strong> to see tonight's projections.</div>
          )}
          <div className="player-grid">
            {playerGroups.map((player) => (
              <PlayerCard key={player.player_id} player={player} />
            ))}
          </div>
        </>
      )}

      {/* ── Results view ── */}
      {view === "results" && !loading && (
        <>
          {appliedLeague && resultGroups.length > 0 && (
            <div className="results-header">
              <span className="results-league">{appliedLeague} Props Results</span>
              <span className="record-badge">
                <span className="rec-w">{overallRecord.wins}W</span>
                {" – "}
                <span className="rec-l">{overallRecord.losses}L</span>
              </span>
            </div>
          )}
          {resultGroups.length === 0 && appliedLeague && (
            <div className="empty-state">No graded results yet for {appliedLeague}.</div>
          )}
          {!appliedLeague && (
            <div className="empty-state">Select a sport and click <strong>Load Props</strong> to see results.</div>
          )}
          <div className="player-grid">
            {resultGroups.map((player) => (
              <ResultCard key={player.player_id} player={player} />
            ))}
          </div>
        </>
      )}

      {loading && <div className="loading-spinner">Loading…</div>}
    </div>
  );
}

/* ── Player Props Card ── */
function PlayerCard({ player }) {
  const { player_name, team, props } = player;
  const initials = player_name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="player-card">
      <div className="player-card-header">
        <div className="player-avatar">{initials}</div>
        <div className="player-info">
          <div className="player-name">{player_name}</div>
          <div className="player-team">{team}</div>
        </div>
      </div>

      <div className="prop-row-grid">
        {props.map((p) => (
          <PropCell key={`${p.prop_type}-${p.line}`} prop={p} />
        ))}
      </div>

      {/* Model confidence mini-bar */}
      <div className="model-conf-row">
        {MODEL_LABELS.map(([key, label]) => {
          const val = props[0]?.model_confidence?.[key];
          return (
            <div key={key} className="model-conf-cell">
              <span className="mc-label">{label}</span>
              <span className="mc-val">{val != null ? `${val}%` : "—"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PropCell({ prop }) {
  const isOver = prop.picked_side === "OVER";
  const isUnder = prop.picked_side === "UNDER";
  return (
    <div className={`prop-cell${isOver ? " over" : isUnder ? " under" : ""}`}>
      <div className="prop-cell-type">{fmt(prop.prop_type)}</div>
      <div className="prop-cell-proj">{prop.projection ?? "—"}</div>
      <div className="prop-cell-line">Line: {prop.line ?? "—"}</div>
      <div className={`prop-pick-badge${isOver ? " over" : isUnder ? " under" : ""}`}>
        {prop.picked_side || "—"}
      </div>
    </div>
  );
}

/* ── Results Card ── */
function ResultCard({ player }) {
  const { player_name, team, results, wins, losses, pushes } = player;
  const initials = player_name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="player-card result-card">
      <div className="player-card-header">
        <div className="player-avatar">{initials}</div>
        <div className="player-info">
          <div className="player-name">{player_name}</div>
          <div className="player-team">{team}</div>
        </div>
        <div className="player-record">
          <span className="rec-w">{wins}W</span>
          {" – "}
          <span className="rec-l">{losses}L</span>
          {pushes > 0 && <span className="rec-p"> – {pushes}P</span>}
        </div>
      </div>

      <div className="result-rows">
        {results.map((r, i) => (
          <ResultRow key={`${r.prop_type}-${r.line}-${i}`} r={r} />
        ))}
      </div>
    </div>
  );
}

function ResultRow({ r }) {
  const isWin = r.result === "WIN";
  const isLoss = r.result === "LOSS";
  const isPush = r.result === "PUSH";
  const isOver = r.pick === "OVER";

  return (
    <div className="result-row">
      <div className="rr-prop">{fmt(r.prop_type)}</div>
      <div className={`rr-pick${isOver ? " over" : " under"}`}>{r.pick}</div>
      <div className="rr-line">
        <span className="rr-lbl">Line</span> {r.line}
      </div>
      <div className="rr-actual">
        <span className="rr-lbl">Actual</span> {r.actual ?? "—"}
      </div>
      <div className={`rr-result${isWin ? " win" : isLoss ? " loss" : isPush ? " push" : ""}`}>
        {r.result || "—"}
      </div>
    </div>
  );
}
