import { useEffect, useMemo, useState } from 'react';
import { getJson } from '../api.js';
import { modelPropCounts } from '../propsView.js';

// Player props for one league, in the site's look. The data and the access rules come from the
// /player-props-api routes (the same ones the old props page used). Locked rows come back from the
// server, so nothing is hidden only by the page.
const TABS = [
  ['props', 'Props'],
  ['streaks', 'Streaks'],
  ['results', 'Results'],
  ['diagnostics', 'Diagnostics'],
];

const MODELS = [
  ['glicko2', 'Grinder2'],
  ['trueskill', 'Takedown'],
  ['elo', 'Edge'],
  ['xgboost', 'XSharp'],
  ['ensemble', 'Sharp Consensus'],
];

const PROP_LABELS = {
  points: 'Points', rebounds: 'Rebounds', assists: 'Assists', threes: '3PT Made', steals: 'Steals',
  shots_on_goal: 'SOG', goals: 'Goals', hits: 'Hits', strikeouts: 'K', runs: 'Runs', rbis: 'RBI',
  home_runs: 'HR', passing_yards: 'Pass Yds', rushing_yards: 'Rush Yds', receiving_yards: 'Rec Yds',
  receptions: 'Rec', shots: 'Shots', shots_on_target: 'On Tgt',
};

function propLabel(propType) {
  return PROP_LABELS[propType] || String(propType || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function pct(wins, losses) {
  const total = wins + losses;
  return total > 0 ? Math.round((wins / total) * 100) : null;
}

function rateClass(rate) {
  return rate == null ? '' : rate >= 60 ? 'great' : rate >= 50 ? 'ok' : 'poor';
}

// The expected value of the side the model picked (falls back to the over or under number).
function evOf(row) {
  if (row.pick_ev != null) return row.pick_ev;
  return String(row.picked_side).toUpperCase() === 'OVER' ? row.ev_over_percent : row.ev_under_percent;
}

// Local calendar day (YYYY-MM-DD) for an offset from today.
function dayIso(offset) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const value = type => Number(parts.find(part => part.type === type).value);
  return new Date(Date.UTC(value('year'), value('month')-1, value('day')+offset)).toISOString().slice(0, 10);
}

function dayLabel(iso) {
  if (!iso) return '';
  return new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function sortValue(row, key) {
  if (key === 'player') return String(row.player_name || '').toLowerCase();
  if (key === 'prop') return propLabel(row.prop_type).toLowerCase();
  if (key === 'line') return Number(row.line) || 0;
  if (key === 'projection') return Number(row.projection) || 0;
  if (key === 'pick') return String(row.picked_side || '').toLowerCase();
  if (key === 'ev') return evOf(row) ?? -Infinity;
  return (row.model_forecasts || []).find(model => model.model_id === key)?.probability ?? -Infinity;
}

const PROP_HEAD = [
  ['player', 'Player'],
  ['prop', 'Prop'],
  ['line', 'Line'],
  ['projection', 'Projection'],
  ['pick', 'Prediction'],
  ['ev', 'EV'],
  ...MODELS,
];

export default function PlayerProps({ league }) {
  const [tab, setTab] = useState('props');
  const [day, setDay] = useState(() => dayIso(-1));
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [market, setMarket] = useState('all');
  const [side, setSide] = useState('all');
  const [minEv, setMinEv] = useState('');
  const [search, setSearch] = useState(() => new URLSearchParams(window.location.search).get('player') || '');
  const [role, setRole] = useState('all');
  const [sort, setSort] = useState({ key: null, dir: 'desc' });

  // Only the results tab depends on the day; the others always load the current slate.
  const requestDay = tab === 'results' ? day : '';

  useEffect(() => {
    let alive = true;
    let retry = null;
    setData(null);
    setError(null);
    const query = new URLSearchParams({ league });
    if (requestDay) query.set('date', requestDay);
    const load = () => {
      getJson(`${tab === 'results' ? '/api/web/props-results' : `/player-props-api/${tab}`}?${query.toString()}`)
        .then((body) => {
          if (!alive) return;
          setData(body);
          // The server is building this league's first slate; ask again shortly.
          if (body && body.building) retry = setTimeout(load, 4000);
        })
        .catch((err) => {
          if (alive) setError(err.message);
        });
    };
    load();
    return () => {
      alive = false;
      clearTimeout(retry);
    };
  }, [tab, league, requestDay]);

  const items = useMemo(() => (data && Array.isArray(data.items) ? data.items : []), [data]);
  const markets = useMemo(
    () => [...new Set(items.filter((r) => !r.locked).map((r) => r.prop_type).filter(Boolean))].sort(),
    [items],
  );
  const roles = useMemo(() => [...new Set(items.map((r) => r.position).filter(Boolean))].sort(), [items]);
  const locked = Boolean(data && data.locked && data.is_premium === false);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    let out = items.filter((r) => {
      if (market !== 'all' && r.prop_type !== market) return false;
      if (side !== 'all' && String(r.picked_side || '').toUpperCase() !== side) return false;
      if (minEv !== '' && !(evOf(r) >= Number(minEv))) return false;
      if (role !== 'all' && r.position !== role) return false;
      if (q && !String(r.player_name || '').toLowerCase().includes(q)) return false;
      return true;
    });
    if (sort.key) {
      const dir = sort.dir === 'asc' ? 1 : -1;
      out = [...out].sort((a, b) => {
        const va = sortValue(a, sort.key);
        const vb = sortValue(b, sort.key);
        if (va < vb) return -dir;
        if (va > vb) return dir;
        return 0;
      });
    }
    return out;
  }, [items, market, side, minEv, search, role, sort]);

  function toggleSort(key) {
    const textFirst = key === 'player' || key === 'prop' || key === 'pick';
    setSort((current) => (current.key === key
      ? { key, dir: current.dir === 'desc' ? 'asc' : 'desc' }
      : { key, dir: textFirst ? 'asc' : 'desc' }));
  }

  return (
    <div className="pp">
      <div className="pp-tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`pp-tab${tab === key ? ' is-active' : ''}`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'results' && (
        <div className="pp-chips">
          {[-6, -5, -4, -3, -2, -1, 0].map((offset) => {
            const iso = dayIso(offset);
            return (
              <button
                key={iso}
                type="button"
                className={`pp-chip${day === iso ? ' is-active' : ''}`}
                onClick={() => setDay(iso)}
              >
                {offset === 0 ? 'Today' : dayLabel(iso)}
              </button>
            );
          })}
        </div>
      )}

      {error && <div className="pp-banner warn">{error}</div>}
      {!error && data === null && <p className="pl-empty">Loading {league} {tab}…</p>}

      {!error && data && data.off_season && <div className="pp-banner upgrade">{data.detail || `${league} is in the off-season.`}</div>}

      {!error && data && !data.off_season && locked && (
        <div className="pp-banner upgrade">
          Your plan unlocks this section. <a href="/plans">See plans</a>.
        </div>
      )}

      {!error && data && data.building && <p className="pl-empty">Updating {league} props… this page fills in by itself.</p>}

      {!error && data && !data.building && !data.off_season && !locked && tab === 'props' && (
        <>
          {data.is_premium === false && (
            <div className="pp-banner upgrade">
              You&apos;re viewing 1 free prediction. <a href="/plans">Upgrade</a> to unlock every prop, filters, streaks and results.
            </div>
          )}
          {data.lines_real === false && (
            <div className="pp-banner warn">
              Market lines are unavailable right now. PL lines are shown separately from market lines.
            </div>
          )}
          {data.eligibility_audit && (
            <p className="pp-meta">
              Pool {data.eligibility_audit.eligible_players || 0} eligible players · {data.eligibility_audit.valid_props || items.length} props
            </p>
          )}
          {items.length === 0 ? (
            <p className="pl-empty">No props available for {league} right now.</p>
          ) : (
            <>
              <div className="pp-chips">
                <button type="button" className={`pp-chip${market === 'all' ? ' is-active' : ''}`} onClick={() => setMarket('all')}>All</button>
                {markets.map((pt) => (
                  <button
                    key={pt}
                    type="button"
                    className={`pp-chip${market === pt ? ' is-active' : ''}`}
                    onClick={() => setMarket(pt)}
                  >
                    {propLabel(pt)}
                  </button>
                ))}
              </div>
              <div className="pp-toolbar">
                <input
                  className="pp-input"
                  type="search"
                  placeholder="Search player"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {roles.length > 1 && (
                  <select className="pp-select" value={role} onChange={(e) => setRole(e.target.value)}>
                    <option value="all">All roles</option>
                    {roles.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                )}
                <select className="pp-select" value={side} onChange={(e) => setSide(e.target.value)}>
                  <option value="all">Side: All</option>
                  <option value="OVER">Over</option>
                  <option value="UNDER">Under</option>
                </select>
                <input
                  className="pp-input pp-input-small"
                  type="number"
                  step="0.5"
                  placeholder="Min EV %"
                  value={minEv}
                  onChange={(e) => setMinEv(e.target.value)}
                />
              </div>
              <PropsTable rows={rows} league={league} sort={sort} onSort={toggleSort} />
            </>
          )}
        </>
      )}

      {!error && data && !data.off_season && !locked && tab === 'streaks' && <StreaksView data={data} league={league} />}
      {!error && data && !data.off_season && !locked && tab === 'results' && <ResultsView data={data} league={league} />}
      {!error && data && !data.off_season && !locked && tab === 'diagnostics' && <DiagnosticsView data={data} league={league} />}
    </div>
  );
}

function PropsTable({ rows, league, sort, onSort }) {
  return (
    <div className="pp-table-wrap">
      <table className="pp-table">
        <thead>
          <tr>
            {PROP_HEAD.map(([key, label]) => (
              <th key={key} onClick={() => onSort(key)} aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                {key === 'line' && league === 'MLB' ? 'Book' : label}
                {sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, index) => (r.locked ? (
            <tr key={`${r.player_id}-${r.prop_type}-${index}`} className="pp-row-locked">
              <td>
                <span className="pp-player">{r.player_name}</span>
                <span className="pp-team">{r.team}</span>
              </td>
              <td>{propLabel(r.prop_type)}</td>
              <td colSpan={MODELS.length + 4}>
                <span className="pp-lock-note">Locked. Upgrade to see the line, the prediction and the model numbers.</span>
              </td>
            </tr>
          ) : (
            <PropRow key={`${r.player_id}-${r.prop_type}-${r.line}-${index}`} r={r} />
          )))}
        </tbody>
      </table>
    </div>
  );
}

function PropRow({ r }) {
  const ev = evOf(r);
  const projection = r.display_mode === 'projection';
  const pickClass = projection || String(r.picked_side).toUpperCase() === 'OVER'
    ? 'pp-pick-over'
    : String(r.picked_side).toUpperCase() === 'UNDER' ? 'pp-pick-under' : '';
  return (
    <tr>
      <td>
        <span className="pp-player">{r.player_name}</span>
        <span className="pp-team">{r.team}</span>
      </td>
      <td>{propLabel(r.prop_type)}</td>
      <td>{projection ? (r.line != null ? `O/U ${r.line}` : '—') : (r.line ?? '—')}</td>
      <td>{r.projection ?? '—'}</td>
      <td className={pickClass}>{projection ? `Proj ${r.pick_display ?? r.projection}` : String(r.picked_side || '—').toUpperCase()}</td>
      <td className={ev == null ? '' : ev >= 0 ? 'pp-ev-pos' : 'pp-ev-neg'}>
        {ev == null ? '—' : `${ev > 0 ? '+' : ''}${Number(ev).toFixed(1)}%`}
      </td>
      {MODELS.map(([key]) => (
        <td key={key}>{(() => {
          const forecast = (r.model_forecasts || []).find(model => model.model_id === key);
          const reason = (r.model_unavailable || []).find(model => model.model_id === key)?.reason;
          return forecast?.probability != null
            ? <><span>{forecast.side || 'PASS'} </span><strong className="pp-hub-percentage">{Number(forecast.probability).toFixed(1)}%</strong></>
            : <span title={reason || 'A separate player-stat forecast is unavailable.'}>N/A</span>;
        })()}</td>
      ))}
    </tr>
  );
}

function StreaksView({ data, league }) {
  const [market, setMarket] = useState('all');
  const [search, setSearch] = useState('');
  const items = data.items || [];
  if (items.length === 0) {
    return (
      <p className="pl-empty">
        No graded streak history for {league} yet. Streaks appear once real sportsbook props are graded from completed games.
      </p>
    );
  }
  const markets = [...new Set(items.map((r) => r.prop_type))].sort();
  const q = search.trim().toLowerCase();
  const rows = items.filter((r) => (market === 'all' || r.prop_type === market)
    && (!q || String(r.player_name || '').toLowerCase().includes(q)));
  return (
    <>
      <div className="pp-chips">
        <button type="button" className={`pp-chip${market === 'all' ? ' is-active' : ''}`} onClick={() => setMarket('all')}>All</button>
        {markets.map((pt) => (
          <button key={pt} type="button" className={`pp-chip${market === pt ? ' is-active' : ''}`} onClick={() => setMarket(pt)}>
            {propLabel(pt)}
          </button>
        ))}
      </div>
      <div className="pp-toolbar">
        <input className="pp-input" type="search" placeholder="Search player" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <div className="pp-table-wrap">
        <table className="pp-table">
          <thead>
            <tr>
              <th>Player</th>
              <th>Prop</th>
              <th>Streak</th>
              <th>Last 5</th>
              <th>Hit %</th>
              <th>Record</th>
              <th>Games</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7} className="pp-muted">No streaks match these filters.</td></tr>
            ) : rows.map((r, index) => {
              const hot = r.streak_type === 'HIT';
              return (
                <tr key={`${r.player_id || r.player_name}-${r.prop_type}-${index}`}>
                  <td>
                    <span className="pp-player">{r.player_name || '—'}</span>
                    <span className="pp-team">{r.team || ''}</span>
                  </td>
                  <td>{propLabel(r.prop_type)}</td>
                  <td><span className={`pp-streak ${hot ? 'hot' : 'cold'}`}>{hot ? '🔥 W' : '❄️ L'}{r.streak_len}</span></td>
                  <td>
                    {(r.recent || []).map((x, i) => (
                      <span key={i} className={`pp-pill ${x === 'HIT' ? 'hit' : 'miss'}`}>{x === 'HIT' ? 'H' : 'M'}</span>
                    ))}
                  </td>
                  <td><span className={`pp-rate ${rateClass(r.hit_rate)}`}>{r.hit_rate}%</span></td>
                  <td>{r.hits}–{r.misses}</td>
                  <td className="pp-muted">{r.games}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ResultsView({ data, league }) {
  const items = data.items || [];
  const forecastRows = items.filter(row => row.model_id);
  const overall = data.summary?.overall || {
    wins: forecastRows.filter(row => row.result === 'Win').length,
    losses: forecastRows.filter(row => row.result === 'Loss').length,
  };
  const byModelType = modelPropCounts(items);
  const week = data.week_summary || {};
  const season = data.season_summary || {};
  const seasonByProp = Object.entries(data.season_by_prop || {});
  const sinceLabel = data.tracking_since
    ? `Since ${new Date(`${data.tracking_since}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`
    : 'All tracked';
  const tallies = [
    { label: data.result_date === dayIso(0) ? 'Today' : dayLabel(data.result_date), w: overall.wins || 0, l: overall.losses || 0 },
    ...(data.week_summary ? [{ label: 'Last 7 days', w: week.wins || 0, l: week.losses || 0 }] : []),
    ...(data.season_summary ? [{ label: sinceLabel, w: season.wins || 0, l: season.losses || 0 }] : []),
  ];
  const graded = tallies.reduce((sum, t) => sum + t.w + t.l, 0);
  if (graded === 0 && items.length === 0) {
    return <p className="pl-empty">No graded {league} props for this date yet. Results appear once the games finish.</p>;
  }
  return (
    <>
      <div className="pp-stat-row">
        {tallies.map((t) => {
          const rate = pct(t.w, t.l);
          return (
            <div key={t.label} className="pp-stat">
              <div className="pp-stat-label">{t.label}</div>
              <div className={`pp-stat-pct ${rateClass(rate)}`}>{rate == null ? 'N/A' : `${rate}%`}</div>
              <div className="pp-muted">{t.w}–{t.l} · {t.w + t.l} graded</div>
            </div>
          );
        })}
      </div>
      <p className="pp-muted">Model records use saved pregame forecasts. Older unassigned selections do not count toward a model’s record.
        {' '}<a href={`/sports-props?league=${league}&results_date=${data.result_date || ''}`}>View model records and longer periods</a>.</p>
      {byModelType.length > 0 && <div className="pp-table-wrap"><table className="pp-table">
        <thead><tr><th>Model</th><th>Prop</th><th>Wins</th><th>Losses</th><th>Pushes</th><th>Decided</th><th>Win rate</th></tr></thead>
        <tbody>{byModelType.map(row => <tr key={`${row.model_id}-${row.prop_type}-${row.league}`}>
          <td>{row.model_name}</td><td>{propLabel(row.prop_type)}</td><td>{row.wins}</td><td>{row.losses}</td>
          <td>{row.pushes}</td><td>{row.decided}</td><td className="pp-hub-percentage">{row.win_rate == null ? 'N/A' : `${row.win_rate.toFixed(1)}%`}</td>
        </tr>)}</tbody>
      </table></div>}
      {seasonByProp.length > 0 && (
        <div className="pp-table-wrap">
          <table className="pp-table">
            <thead>
              <tr><th>Prop</th><th>Hit %</th><th>Record</th><th>Graded</th></tr>
            </thead>
            <tbody>
              {seasonByProp.map(([pt, s]) => (
                <tr key={pt}>
                  <td className="pp-player">{propLabel(pt)}</td>
                  <td><span className={`pp-rate ${rateClass(pct(s.wins, s.losses))}`}>{pct(s.wins, s.losses) == null ? 'N/A' : `${pct(s.wins, s.losses)}%`}</span></td>
                  <td>{s.wins}–{s.losses}</td>
                  <td className="pp-muted">{s.wins + s.losses}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {items.length > 0 && (
        <div className="pp-table-wrap">
          <table className="pp-table">
            <thead>
              <tr><th>Player</th><th>Prop</th><th>Model</th><th>Prediction</th><th>Line</th><th>Actual</th><th>Result</th></tr>
            </thead>
            <tbody>
              {items.map((r, index) => (
                <tr key={`${r.player_id || r.player_name}-${r.prop_type}-${index}`}>
                  <td>
                    <span className="pp-player">{r.player_name}</span>
                    <span className="pp-team">{r.team}</span>
                  </td>
                  <td>{propLabel(r.prop_type)}</td>
                  <td>{r.model_id ? r.model_name : 'Not recorded'}</td>
                  <td className={r.pick === 'OVER' ? 'pp-pick-over' : r.pick === 'UNDER' ? 'pp-pick-under' : ''}>{r.pick}</td>
                  <td>{r.line}</td>
                  <td>{r.actual ?? 'N/A'}</td>
                  <td><span className={`pp-result ${String(r.result || '').toLowerCase()}`}>{r.model_id ? r.result : r.selection_result || 'N/A'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function DiagnosticsView({ data, league }) {
  const overall = data.overall || {};
  const stability = data.distribution_stability || {};
  const byType = Object.entries(data.by_prop_type || {});
  return (
    <>
      <div className="pp-stat-row">
        <div className="pp-stat"><div className="pp-stat-label">Total props</div><div className="pp-stat-value">{data.total_props ?? '—'}</div></div>
        <div className="pp-stat"><div className="pp-stat-label">Avg confidence</div><div className="pp-stat-value">{overall.avg_confidence ?? '—'}%</div></div>
        <div className="pp-stat"><div className="pp-stat-label">Avg EV</div><div className="pp-stat-value">{overall.avg_ev ?? '—'}%</div></div>
        <div className="pp-stat"><div className="pp-stat-label">Distribution</div><div className="pp-stat-value">{stability.label ?? '—'}</div></div>
      </div>
      <p className="pp-meta">{league} model diagnostics. Positive EV props: {overall.positive_ev_props ?? '—'}, negative EV props: {overall.negative_ev_props ?? '—'}.</p>
      {byType.length > 0 && (
        <div className="pp-table-wrap">
          <table className="pp-table">
            <thead>
              <tr><th>Type</th><th>Count</th><th>Avg EV</th><th>Avg conf</th><th>+EV %</th></tr>
            </thead>
            <tbody>
              {byType.map(([pt, s]) => (
                <tr key={pt}>
                  <td className="pp-player">{propLabel(pt)}</td>
                  <td>{s.count}</td>
                  <td className={s.avg_ev > 0 ? 'pp-ev-pos' : s.avg_ev < 0 ? 'pp-ev-neg' : ''}>{s.avg_ev > 0 ? '+' : ''}{s.avg_ev}%</td>
                  <td>{s.avg_conf}%</td>
                  <td>{s.positive_ev_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
