import { useEffect, useMemo, useState } from 'react';
import { getJson } from '../api.js';
import './SportsProps.css';
import { filterPropResults, modelPropCounts, modelCounts } from '../propsView.js';

const SPORTS = ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'CFL', 'SOCCER', 'UFC', 'TENNIS', 'GOLF'];
const nameOf = (code) => code === 'SOCCER' ? 'Soccer' : code;
const labelOf = (type) => type === 'threes' ? '3PT Made' : String(type || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const percentOf = (value) => value == null ? 'N/A' : value > 99.95 ? '>99.9%' : value < 0.05 ? '<0.1%' : `${Number(value).toFixed(1)}%`;

function yesterdayET() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' })
    .formatToParts(new Date());
  const get = (type) => Number(parts.find((part) => part.type === type).value);
  return new Date(Date.UTC(get('year'), get('month') - 1, get('day') - 1)).toISOString().slice(0, 10);
}

function todayET() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}

export default function PropsPage() {
  const requested = (new URLSearchParams(window.location.search).get('league') || '').toUpperCase();
  const league = SPORTS.includes(requested) ? requested : '';
  const selectedGame = new URLSearchParams(window.location.search).get('game') || '';
  const [me, setMe] = useState(null);
  const [identityError, setIdentityError] = useState('');
  const [retry, setRetry] = useState(0);
  const [pane, setPane] = useState('props');
  const [feed, setFeed] = useState(null);
  const [feedError, setFeedError] = useState('');
  const [results, setResults] = useState(null);
  const [resultsError, setResultsError] = useState('');
  const [day, setDay] = useState(() => new URLSearchParams(window.location.search).get('results_date') || yesterdayET());
  const [resultWindow, setResultWindow] = useState(() => new URLSearchParams(window.location.search).get('results_window') || 'night');
  const [propsDay, setPropsDay] = useState(() => new URLSearchParams(window.location.search).get('date') || todayET());
  const [market, setMarket] = useState('all');
  const [model, setModel] = useState('all');
  const [lineType, setLineType] = useState('all');
  const [search, setSearch] = useState(() => new URLSearchParams(window.location.search).get('player') || '');
  const [visibleResults, setVisibleResults] = useState(80);
  useEffect(() => { setVisibleResults(80); }, [day, resultWindow, league, model, market, search, lineType]);

  useEffect(() => {
    let alive = true;
    getJson('/api/web/me', { maxAge: 0 }).then((data) => {
      if (alive) { setMe(data); setIdentityError(''); }
    }).catch(() => { if (alive) setIdentityError('Could not verify your sign-in. Please retry.'); });
    return () => { alive = false; };
  }, [retry]);

  useEffect(() => {
    if (!me?.premium || !me?.loggedIn) return undefined;
    let alive = true;
    let timer;
    const params = new URLSearchParams({ date: propsDay });
    if (league) params.set('league', league);
    const query = `?${params}`;
    const load = () => getJson(`/api/web/props-hub${query}`, { maxAge: 0 }).then((data) => {
      if (!alive) return;
      setFeed(data); setFeedError('');
      if (data.sections?.some((section) => section.building)) timer = setTimeout(load, 5000);
    }).catch(() => { if (alive) setFeedError('Could not load props. Please retry.'); });
    setFeed(null);
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [me, league, propsDay, retry]);

  useEffect(() => {
    if (!me?.premium || !me?.loggedIn) return undefined;
    let alive = true;
    const query = new URLSearchParams({ date: day, window: resultWindow });
    let timer;
    if (league) query.set('league', league);
    setResults(null);
    const load = () => getJson(`/api/web/props-results?${query}`, { maxAge: 0 }).then((data) => {
      if (alive) { setResults(data); setResultsError(''); }
    }).catch(() => { if (alive) setResultsError('Could not load recorded results. Please retry.'); })
      .finally(() => { if (alive) timer = setTimeout(load, 60000); });
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [me, league, day, resultWindow, retry, feed?.captured_forecasts]);

  const sections = feed?.sections || [];
  const types = [...new Set([
    ...sections.flatMap((section) => (section.items || []).map((row) => row.prop_type)),
    ...(results?.items || []).map((row) => row.prop_type),
  ].filter(Boolean))].sort();
  const resultGame = day === propsDay && resultWindow === 'night' ? selectedGame : '';
  const filtered = useMemo(() => filterPropResults(results?.items, { model, market, search, lineType, game: resultGame }),
  [results, market, model, search, lineType, resultGame]);
  const typeSummaries = modelPropCounts(filtered);
  const modelSummaries = modelCounts(filtered, results?.models);
  const firstModelDate = (results?.models || [])
    .filter(item => (model === 'all' ? !item.id.startsWith('pl_') : item.id === model))
    .map(item => item.first_recorded_date).filter(Boolean).sort()[0];
  const sportCounts = Object.fromEntries((feed?.counts || []).map(item => [item.league, item]));

  function changeSport(code, date = propsDay, game = '', resultDate = day) {
    const query = new URLSearchParams({ date, results_date: resultDate, results_window: resultWindow });
    if (code) query.set('league', code);
    if (game) query.set('game', game);
    const link = document.createElement('a');
    link.href = `/sports-props?${query}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  const gate = identityError ? (
    <p className="pl-empty" role="alert">{identityError} <button onClick={() => setRetry((v) => v + 1)}>Retry</button></p>
  ) : !me ? <p className="pl-empty">Loading membership…</p>
    : !me.loggedIn ? <div className="pl-login-prompt">Log in to explore Sports Props. <a href={`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`}>Log in</a> or <a href="/plans">view plans</a>.</div>
      : !me.premium ? <div className="pl-login-prompt">An active premium membership is required for Sports Props. <a href="/plans">View plans</a>.</div> : null;

  return (
    <>
      <div className="pl-mobile-switch" role="tablist" aria-label="Show props or results">
        {['props', 'results'].map((key) => <button key={key} type="button" role="tab" aria-selected={pane === key}
          className={pane === key ? 'active' : ''} onClick={() => setPane(key)}>{key === 'props' ? 'Props' : 'Results'}</button>)}
      </div>
      <main className={`pl-center pp-hub-main${pane !== 'props' ? ' pl-mobile-hidden' : ''}`}>
        <h1 className="pl-title">{league ? `${nameOf(league)} Props` : 'Sports Props'}</h1>
        <div className="pp-hub-filters pp-hub-navigation">
          <label>Sport<select aria-label="Props sport" value={league} onChange={(e) => changeSport(e.target.value)}>
            <option value="">All sports</option>
            {SPORTS.map(code => {
              const count = sportCounts[code];
              const suffix = !me?.premium ? '' : !count ? ' — Loading…' : count.count == null ? ' — Unavailable'
                : ` (${count.count} ${count.kind} props)`;
              return <option key={code} value={code}>{nameOf(code)}{suffix}</option>;
            })}
          </select></label>
          <label>Props date<input aria-label="Props date" type="date" value={propsDay} onChange={(e) => {
            if (e.target.value) changeSport(league, e.target.value, '', e.target.value);
          }} /></label>
        </div>
        {gate || <>
          <div className="pp-hub-filters">
            {league && <label>Game<select aria-label="Prop game" value={selectedGame}
              onChange={e => changeSport(league, propsDay, e.target.value)}>
              <option value="">All games</option>
              {[...new Map(sections.flatMap(section => section.items || []).filter(row => row.game_id)
                .map(row => [row.game_id, row.game_label])).entries()].map(([id, label]) =>
                <option key={id} value={id}>{label}</option>)}
            </select></label>}
            <label>Player<input type="search" placeholder="Search player" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
            <label>Prop type<select value={market} onChange={(e) => setMarket(e.target.value)}>
              <option value="all">All prop types</option>{types.map((type) => <option key={type} value={type}>{labelOf(type)}</option>)}
            </select></label>
            <label>Lines<select value={lineType} onChange={e => setLineType(e.target.value)}>
              <option value="all">All line types</option><option value="market">Market lines</option>
              <option value="pl">PL lines</option>
            </select></label>
          </div>
          {feedError && <p className="pl-empty" role="alert">{feedError} <button onClick={() => setRetry((v) => v + 1)}>Retry</button></p>}
          {!feed && !feedError && <p className="pl-empty">Loading props…</p>}
          {feed?.tracking_available === false && <p className="pl-empty" role="alert">Forecasts loaded, but result tracking is unavailable. Please retry.</p>}
          {feed && <p className="pp-hub-muted">Prop percentages are estimates, not historical win rates.</p>}
          {sections.map((section) => {
            const rows = (section.items || []).filter((row) => (!selectedGame || row.game_id === selectedGame)
              && (market === 'all' || market === row.prop_type)
              && (lineType === 'all' || row.line_type === lineType)
              && (!search || row.player_name.toLowerCase().includes(search.toLowerCase())));
            return <section key={section.league} className="pp-hub-section" data-props-sport={section.league}>
              <div className="pp-hub-section-head"><h2>{nameOf(section.league)}</h2>
                {!league && <a href={`/sports-props?league=${section.league}&date=${propsDay}`}>Open {nameOf(section.league)} props →</a>}</div>
              <p className="pp-hub-muted">{section.total == null ? 'Prop count unavailable' : `${section.total} ${section.count_kind || 'available'} props`} · {propsDay}</p>
              {!league && section.total > (section.items || []).length && <p className="pp-hub-muted">
                Previewing {(section.items || []).length} props. Open {nameOf(section.league)} to view all {section.total}.
              </p>}
              {section.building && <p className="pl-empty">Updating this sport’s props…</p>}
              {section.refresh_notice && <p className="pp-hub-muted">{section.refresh_notice}</p>}
              {!rows.length && !section.building && <p className="pl-empty">{section.reason || 'No props match these filters.'}</p>}
              {!rows.length && section.next_date && <p className="pp-hub-muted">
                <a href={`/sports-props?league=${section.league}&date=${section.next_date}`}>Next scheduled date: {section.next_date}</a>
              </p>}
              {rows.map((row, index) => <article className="pp-hub-card" key={`${row.player_id}-${row.prop_type}-${row.line}-${index}`}>
                <h3>{row.player_name}</h3><p className="pp-hub-muted">{row.team}</p>
                <div className="pp-hub-market"><strong>{labelOf(row.prop_type)}</strong>
                  <span>{row.line_type === 'pl' ? 'PL line' : row.line_type === 'legacy' ? 'Recorded line' : 'Market line'} · Over / Under {row.line}</span></div>
                {row.pick && <div className="pp-hub-selection">
                  <strong>{row.strong_pick ? 'PL pick' : 'PL lean'}: {row.pick} {row.line}</strong>
                  <strong className="pp-hub-probability">{percentOf(row.pick_probability)}</strong>
                </div>}
                {!row.strong_pick && row.pick && <p className="pp-hub-muted">Strong picks require over 61%, at least 10 recent appearances, and model agreement.</p>}
                {row.recorded_side && <p>Recorded selection: {row.recorded_side} {row.line}</p>}
                {(row.model_forecasts || []).length ? row.model_forecasts.map((forecast) =>
                  <p className="pp-hub-forecast" key={forecast.model_id}><span>{forecast.model_name}: {forecast.side || 'PASS'} {forecast.side ? row.line : ''}</span>
                    <strong className="pp-hub-probability">{percentOf(forecast.probability)}</strong></p>)
                  : <p className="pp-hub-muted">Model forecast: N/A — {row.model_reason}</p>}
                {(row.model_unavailable || []).filter(item => item.model_id !== 'efficiency').map(item =>
                  <p className="pp-hub-muted" key={item.model_id}>{item.model_name}: N/A — {item.reason}</p>)}
              </article>)}
            </section>;
          })}
        </>}
      </main>
      <aside className={`pl-right pp-hub-results${pane !== 'results' ? ' pl-mobile-hidden' : ''}`} aria-label="Prop results">
        <h2 className="pl-right-title">{league ? `${nameOf(league)} Prop Results` : 'Prop Results'}</h2>
        {gate || <>
          <div className="pp-hub-filters">
            <label>Results date<input type="date" value={day} onChange={(e) => { if (e.target.value) setDay(e.target.value); }} /></label>
            <label>Period<select aria-label="Prop results period" value={resultWindow} onChange={e => setResultWindow(e.target.value)}>
              <option value="night">Selected night</option><option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option><option value="season">Season</option>
            </select></label>
            <label>Model<select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="all">All models</option>{(results?.models || []).map((item) =>
                <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
          </div>
          {resultsError && <p className="pl-empty" role="alert">{resultsError} <button onClick={() => setRetry((v) => v + 1)}>Retry</button></p>}
          {!results && !resultsError && <p className="pl-empty">Loading results…</p>}
          {results && <>
            {results.window !== 'night' && <p className="pp-hub-muted">{results.start_date} through {results.result_date}, inclusive.</p>}
            <p className="pp-hub-muted">{results.notice}</p>
            {firstModelDate && day < firstModelDate && <p className="pl-empty">
              {model === 'all' ? 'Named-model prop tracking' : `${results.models.find(item => item.id === model)?.name || 'Model'} prop tracking`}
              {' '}began on {firstModelDate}. This date range ends before those records begin; earlier selections cannot be counted as model-specific results.
              {' '}<button type="button" onClick={() => { setDay(firstModelDate); setResultWindow('night'); }}>View {firstModelDate} results</button>
            </p>}
            <div className="pp-hub-table" role="region" aria-label="Results by model" tabIndex={0}><table>
              <thead><tr><th>Model</th><th>Wins</th><th>Losses</th><th>Pushes</th><th>Win rate</th></tr></thead>
              <tbody>{modelSummaries.filter((item) => model === 'all' || model === item.id).map((item) =>
                <tr key={item.id}><th scope="row">{item.name}</th><td>{item.captured ? item.wins : 'N/A'}</td>
                  <td>{item.captured ? item.losses : 'N/A'}</td><td>{item.captured ? item.pushes : 'N/A'}</td>
                  <td className={item.win_rate == null ? '' : 'pp-hub-percentage'} title={item.reason}>{item.win_rate == null ? 'N/A' : `${item.win_rate.toFixed(1)}%`}</td></tr>)}</tbody>
            </table></div>
            <p className="pp-hub-muted">Each model can have its own saved record for the same prop.</p>
            <p className="pp-hub-muted">Efficiency applies to game winners, not player props. Earlier PL Projection and PL Count records remain under their original names.</p>
            <p className="pp-hub-muted">A push means the final stat exactly matched the line. Win rate is wins divided by wins plus losses; pushes are excluded.</p>
            <h3>Model performance by prop type</h3>
            <div className="pp-hub-table" role="region" aria-label="Results by model and prop type" tabIndex={0}><table>
              <thead><tr><th>Model</th><th>Prop type</th><th>Wins</th><th>Losses</th><th>Pushes</th><th>Pending</th><th>Decided</th><th>Win rate</th></tr></thead>
              <tbody>{typeSummaries.length === 0 && <tr><td colSpan={8}>No model-specific records match these filters. Saved selections are listed below.</td></tr>}
                {typeSummaries.map((item) =>
                <tr key={`${item.league}-${item.model_id}-${item.prop_type}`}><th scope="row">{item.model_name}</th>
                  <td>{!league && `${item.league} · `}{labelOf(item.prop_type)}</td><td>{item.wins}</td>
                  <td>{item.losses}</td><td>{item.pushes}</td><td>{item.pending}</td><td>{item.decided}</td>
                  <td className={item.win_rate == null ? '' : 'pp-hub-percentage'}>{item.win_rate == null ? 'N/A' : `${item.win_rate.toFixed(1)}%`}</td></tr>)}</tbody>
            </table></div>
            <h3>Individual recorded props</h3>
            {!filtered.length && <p className="pl-empty">{model !== 'all'
              ? `No separate ${results.models.find(item => item.id === model)?.name || 'model'} predictions were recorded for this date and these filters. Select All models to view any older unassigned records.`
              : 'No recorded props match this date and these filters.'}</p>}
            {filtered.slice(0, visibleResults).map((row) => <article className="pp-hub-card" key={`${row.league}-${row.id}`}>
              <h4>{row.player_name} · {labelOf(row.prop_type)}</h4>
              <p className="pp-hub-muted">{row.league} · {row.pick_date} · {row.team}</p>
              <p><strong>{row.model_name || 'Model not recorded'}</strong>
                {row.model_id && row.probability != null && <span className="pp-hub-probability">{percentOf(row.probability)}</span>}</p>
              {row.line_type && <p className="pp-hub-muted">{row.line_type === 'pl' ? 'PL line' : row.line_type === 'legacy'
                ? 'Recorded line — market source not verified' : 'Market line'}</p>}
              <p>{row.pick} {row.line} · Actual{row.actual_label ? ` (${row.actual_label})` : ''}: {row.actual ?? 'N/A'}</p>
              {row.model_id ? <p>Model result: {row.result}{row.reason ? ` — ${row.reason}` : ''}</p>
                : <>
                  <p>Saved selection result: {row.selection_result || 'N/A'}{row.actual_reason ? ` — ${row.actual_reason}` : ''}</p>
                  <p className="pp-hub-muted">{row.pregame_verified === false && row.selection_result && 'Pregame timing could not be verified. '}
                    Model not recorded; this selection is excluded from model performance.</p>
                </>}
              {row.recorded_result && <p className="pp-hub-muted">Stored grade: {row.recorded_result === 'HIT' ? 'Win' : row.recorded_result === 'MISS' ? 'Loss' : row.recorded_result} (not a verified model-specific result)</p>}
            </article>)}
            {filtered.length > visibleResults && <p className="pp-hub-muted">
              Showing {visibleResults} of {filtered.length} records. Summary tables include all matching records.
              {' '}<button type="button" onClick={() => setVisibleResults(count => count+80)}>Show more results</button>
            </p>}
          </>}
        </>}
      </aside>
    </>
  );
}
