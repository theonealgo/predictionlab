import React, { useEffect, useMemo, useState } from "react";
import { fetchProps, fetchResults } from "./api";

const LEAGUES = ["NBA", "NHL", "NFL", "MLB", "SOCCER", "NCAAB", "WNBA", "NCAAF", "NCAAW"];
const MODEL_LABELS = [
  ["glicko2", "Grinder2"],
  ["trueskill", "Takedown"],
  ["xgboost", "Edge"],
  ["xsharp", "XSharp"],
  ["sharp_consensus", "Sharp Consensus"],
];

const PROP_TYPE_LABELS = {
  points: "Points",
  rebounds: "Rebounds",
  assists: "Assists",
  threes: "3PT Made",
  shots_on_goal: "Shots on Goal",
  goals: "Goals",
  hits: "Hits",
  strikeouts: "Strikeouts",
  runs: "Runs",
  rbis: "RBI",
  home_runs: "HR",
  passing_yards: "Pass Yards",
  rushing_yards: "Rush Yards",
  receiving_yards: "Receiving Yards",
  receptions: "Receptions",
  shots: "Shots",
  shots_on_target: "Shots on Target",
};

function formatPropType(value) {
  if (!value) return "";
  return PROP_TYPE_LABELS[value] || value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function App() {
  const [propsRows, setPropsRows] = useState([]);
  const [selectedLeague, setSelectedLeague] = useState("");
  const [propType, setPropType] = useState("");
  const [side, setSide] = useState("");
  const [appliedFilters, setAppliedFilters] = useState({
    league: "",
    propType: "",
    side: "",
  });
  const [view, setView] = useState("props");
  const [resultsRows, setResultsRows] = useState([]);
  const [resultsSummary, setResultsSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState(null);
  const [shareStatus, setShareStatus] = useState("");

  useEffect(() => {
    if (!appliedFilters.league) {
      setPropsRows([]);
      setResultsRows([]);
      setResultsSummary(null);
      setLoading(false);
      return;
    }
    async function run() {
      setLoading(true);
      setApiError(null);
      try {
        if (view === "props") {
          const r = await fetchProps({
            league: appliedFilters.league,
            propType: appliedFilters.propType,
            side: appliedFilters.side,
          });
          setPropsRows(r.items || []);
        } else {
          const rr = await fetchResults(appliedFilters.league);
          setResultsRows(rr.items || []);
          setResultsSummary(rr.summary || null);
        }
      } catch (e) {
        const msg =
          e instanceof TypeError && e.message === "Failed to fetch"
            ? "Cannot load props right now. Please try again."
            : e instanceof Error
              ? e.message
              : String(e);
        setApiError(msg);
        if (view === "props") {
          setPropsRows([]);
        } else {
          setResultsRows([]);
          setResultsSummary(null);
        }
      } finally {
        setLoading(false);
      }
    }
    run();
  }, [appliedFilters, view]);

  const propTypes = useMemo(() => {
    const s = new Set(propsRows.map((x) => x.prop_type));
    return [...s].sort();
  }, [propsRows]);

  const topProps = useMemo(() => propsRows.slice(0, 30), [propsRows]);
  const shareProps = useMemo(() => topProps.slice(0, 3), [topProps]);
  const shareDate = useMemo(
    () =>
      new Date().toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      }),
    []
  );

  function applyFilters() {
    setAppliedFilters({
      league: selectedLeague,
      propType,
      side,
    });
  }

  async function buildShareCanvas() {
    const card = document.getElementById("propsShareCard");
    if (!card) throw new Error("Share card not found.");
    const html2canvas = (await import("html2canvas")).default;
    return html2canvas(card, { backgroundColor: "#ffffff", scale: 2, useCORS: true });
  }

  async function saveShareImage() {
    if (shareProps.length === 0) {
      setShareStatus("No props available to export.");
      return;
    }
    setShareStatus("Generating image...");
    try {
      const canvas = await buildShareCanvas();
      canvas.toBlob((blob) => {
        if (!blob) {
          setShareStatus("Could not generate image.");
          return;
        }
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `player-props-${appliedFilters.league.toLowerCase()}-${shareDate.replace(/\s+/g, "-")}.png`;
        a.click();
        setShareStatus("Image saved.");
      }, "image/png");
    } catch (e) {
      setShareStatus("Could not generate image.");
    }
  }

  async function shareImage() {
    if (shareProps.length === 0) {
      setShareStatus("No props available to share.");
      return;
    }
    setShareStatus("Preparing share image...");
    try {
      const canvas = await buildShareCanvas();
      canvas.toBlob(async (blob) => {
        if (!blob) {
          setShareStatus("Could not generate image.");
          return;
        }
        const file = new File([blob], "player-props-share.png", { type: "image/png" });
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          try {
            await navigator.share({ files: [file], title: "Top Props" });
            setShareStatus("Shared.");
            return;
          } catch (_) {}
        }
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "player-props-share.png";
        a.click();
        setShareStatus("Image saved for sharing.");
      }, "image/png");
    } catch (e) {
      setShareStatus("Could not generate image.");
    }
  }

  return (
    <div className="app">
      <header className="hero">
        <h1>Top Props Tonight</h1>
        {apiError ? (
          <div className="api-error" role="alert">
            <strong>API error.</strong> {apiError}
          </div>
        ) : null}
      </header>

      <section className="filters">
        <label>
          View
          <select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="props">Props</option>
            <option value="results">Results</option>
          </select>
        </label>
        <label>
          Sport
          <select value={selectedLeague} onChange={(e) => setSelectedLeague(e.target.value)}>
            <option value="">Select a sport</option>
            {LEAGUES.map((lg) => (
              <option key={lg} value={lg}>
                {lg}
              </option>
            ))}
          </select>
        </label>
        <label>
          Prop Type
          <select value={propType} onChange={(e) => setPropType(e.target.value)}>
            <option value="">All</option>
            {propTypes.map((t) => (
              <option key={t} value={t}>
                {formatPropType(t)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Side
          <select value={side} onChange={(e) => setSide(e.target.value)}>
            <option value="">All</option>
            <option value="OVER">Over</option>
            <option value="UNDER">Under</option>
          </select>
        </label>
        <button type="button" className="run-btn" onClick={applyFilters} disabled={loading || !selectedLeague}>
          {loading ? "Loading..." : "Run"}
        </button>
      </section>

      <section className="panel">
        <h2>{appliedFilters.league} {view === "props" ? `Top Props Tonight (${topProps.length})` : `Results (${resultsRows.length})`}</h2>
        {view === "results" && resultsSummary ? (
          <p className="results-summary">
            Overall: {resultsSummary.overall?.wins ?? 0}-{resultsSummary.overall?.losses ?? 0}
            {" | "}
            {Object.entries(resultsSummary.by_prop_type || {})
              .map(([k, v]) => `${formatPropType(k)}: ${v.wins}-${v.losses}`)
              .join(" | ")}
          </p>
        ) : null}
        {loading ? (
          <p>Loading...</p>
        ) : (
          <div className="table-wrap">
            <table>
              {view === "props" ? (
                <>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Team</th>
                      <th>Prop</th>
                      <th>Line</th>
                      <th>Projection</th>
                      <th>Pick</th>
                      {MODEL_LABELS.map(([, label]) => (
                        <th key={label}>{label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {topProps.map((r) => (
                      <tr key={`${r.player_id}-${r.prop_type}-${r.line}`}>
                        <td>{r.player_name}</td>
                        <td>{r.team}</td>
                        <td>{formatPropType(r.prop_type)}</td>
                        <td>{r.line ?? "-"}</td>
                        <td>{r.projection}</td>
                        <td className={r.picked_side === 'OVER' ? 'pick-over' : r.picked_side === 'UNDER' ? 'pick-under' : ''}>{r.picked_side}</td>
                        {MODEL_LABELS.map(([key]) => (
                          <td key={`${r.player_id}-${r.prop_type}-${key}`}>
                            {r.model_confidence?.[key] != null ? `${r.model_confidence[key]}%` : "-"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </>
              ) : (
                <>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Team</th>
                      <th>Prop</th>
                      <th>Pick</th>
                      <th>Line</th>
                      <th>Actual</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resultsRows.map((r) => (
                      <tr key={`${r.player_id}-${r.prop_type}-${r.line}-${r.actual}`}>
                        <td>{r.player_name}</td>
                        <td>{r.team}</td>
                        <td>{formatPropType(r.prop_type)}</td>
                        <td className={r.pick === 'OVER' ? 'pick-over' : r.pick === 'UNDER' ? 'pick-under' : ''}>{r.pick}</td>
                        <td>{r.line}</td>
                        <td>{r.actual}</td>
                        <td><span className={r.result === 'WIN' ? 'result-win' : r.result === 'LOSS' ? 'result-loss' : r.result === 'PUSH' ? 'result-push' : ''}>{r.result}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </>
              )}
            </table>
          </div>
        )}
      </section>

      {view === "props" ? (
        <section className="panel share-panel">
          <h2>Share Top 3 Props Image</h2>
          <p className="results-summary">Save or share an image of the top 3 props. Date is shown at the bottom.</p>
          <div className="share-actions">
            <button type="button" className="run-btn" onClick={saveShareImage}>Save Image</button>
            <button type="button" className="run-btn secondary-btn" onClick={shareImage}>Share Image</button>
          </div>
          {shareStatus ? <p className="results-summary">{shareStatus}</p> : null}
          <div id="propsShareCard" className="props-share-card">
            <div className="props-share-head">{appliedFilters.league} Top Props</div>
            {shareProps.length === 0 ? (
              <div className="props-share-empty">No props available.</div>
            ) : (
              shareProps.map((r, idx) => (
                <div className="props-share-row" key={`${r.player_id}-${r.prop_type}-${idx}`}>
                  <div className="ps-player">{r.player_name}</div>
                  <div className="ps-prop">{formatPropType(r.prop_type)}</div>
                  <div className="ps-pick">{r.picked_side} {r.line ?? "-"}</div>
                </div>
              ))
            )}
            <div className="props-share-date">{shareDate}</div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
