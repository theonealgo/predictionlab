import { useEffect, useMemo, useState } from 'react';
import { getJson } from '../api.js';
import './SportsProps.css';
import { filterPropResults, modelPropCounts, modelCounts, matchesPropSide } from '../propsView.js';

const SPORTS = ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'CFL', 'SOCCER', 'UFC', 'TENNIS', 'GOLF'];
const nameOf = (code) => code === 'SOCCER' ? 'Soccer' : code;
const labelOf = (type) => type === 'threes' ? '3PT Made' : String(type || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const percentOf = (value) => value == null ? 'N/A' : value > 99.95 ? '>99.9%' : value < 0.05 ? '<0.1%' : `${Number(value).toFixed(1)}%`;

function todayET() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}

function shiftET(days) {
  const [year, month, day] = todayET().split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export default function PropsPage() {
  const opening = new URLSearchParams(window.location.search);
  const requested = (opening.get('league') || '').toUpperCase();
  const [league, setLeague] = useState(SPORTS.includes(requested) ? requested : '');
  const [selectedGame, setSelectedGame] = useState(opening.get('game') || '');
  const [me, setMe] = useState(null);
  const [identityError, setIdentityError] = useState('');
  const [retry, setRetry] = useState(0);
  const [pane, setPane] = useState('props');
  const [feed, setFeed] = useState(null);
  const [feedError, setFeedError] = useState('');
  const [results, setResults] = useState(null);
  const [resultView, setResultView] = useState('results');
  const [streaks, setStreaks] = useState(null);
  const [resultsError, setResultsError] = useState('');
  // Empty until the API names the latest night with records — calendar yesterday is often all N/A.
  const [day, setDay] = useState(() => new URLSearchParams(window.location.search).get('results_date') || '');
  const [resultWindow, setResultWindow] = useState(() => new URLSearchParams(window.location.search).get('results_window') || 'night');
  const [propsDay, setPropsDay] = useState(() => opening.get('date') || todayET());
  const [market, setMarket] = useState('all');
  const [model, setModel] = useState('all');
  const [lineType, setLineType] = useState('all');
  const [side, setSide] = useState(() => {
    const requestedSide = (new URLSearchParams(window.location.search).get('side') || '').toUpperCase();
    return ['OVER', 'UNDER'].includes(requestedSide) ? requestedSide : 'all';
  });
  const [resultSide, setResultSide] = useState('all');
  const [search, setSearch] = useState(() => new URLSearchParams(window.location.search).get('player') || '');
  const [visibleResults, setVisibleResults] = useState(3);
  const [itemsReady, setItemsReady] = useState(false);
  const [propsQuery, setPropsQuery] = useState(null);
  const [resultsQuery, setResultsQuery] = useState(null);
  useEffect(() => {
    setVisibleResults(3);
    setItemsReady(false);
  }, [day, resultWindow, league, model, resultSide]);

  // Choosing a sport loads the latest night with records (API picks the day when
  // results_date was not set). An explicit date always wins.
  useEffect(() => {
    if (!league) return undefined;
    setResultsError('');
    setResultsQuery({ league, day: day || null, window: resultWindow, n: Date.now() });
    return undefined;
  }, [league, day, resultWindow]);

  useEffect(() => {
    let alive = true;
    const loadMe = (attempt = 0) => getJson('/api/web/me', { maxAge: 0 }).then((data) => {
      if (!alive) return;
      if (typeof data?.loggedIn !== 'boolean' || typeof data?.premium !== 'boolean') {
        throw new Error('bad me payload');
      }
      setMe(data);
      setIdentityError('');
    }).catch(() => {
      if (!alive) return;
      // One quiet retry — a single blip must not block a signed-in member.
      if (attempt < 1) {
        setTimeout(() => { if (alive) loadMe(attempt + 1); }, 400);
        return;
      }
      setIdentityError('Could not verify your sign-in. Please retry.');
    });
    loadMe();
    return () => { alive = false; };
  }, [retry]);

  useEffect(() => {
    if (!propsQuery || !me?.premium || !me?.loggedIn) return undefined;
    let alive = true;
    let timer;
    let tries = 0;
    const params = new URLSearchParams({
      date: propsQuery.date,
      limit: String(propsQuery.limit),
      league: propsQuery.league,
    });
    const load = (startBuild) => {
      const query = new URLSearchParams(params);
      if (startBuild) query.set('load', '1');
      return getJson(`/api/web/props-hub?${query}`, { maxAge: 0 }).then((data) => {
        if (!alive) return;
        setFeed(data);
        setFeedError('');
        const building = data.sections?.some((section) => section.building);
        if (building && tries < 24) {
          tries += 1;
          timer = setTimeout(() => load(false), 5000);
        }
      }).catch(() => { if (alive) setFeedError('Could not load props. Please retry.'); });
    };
    setFeed(null);
    load(true);
    return () => { alive = false; clearTimeout(timer); };
  }, [me, propsQuery, retry]);

  // Prop results for the sport and date chosen above. One request, saved rows included.
  useEffect(() => {
    if (!resultsQuery || !me?.premium || !me?.loggedIn) return undefined;
    const desktop = typeof window !== 'undefined'
      && window.matchMedia('(min-width: 761px)').matches;
    const needResults = pane === 'results' || desktop;
    if (!needResults) return undefined;
    let alive = true;
    const query = new URLSearchParams({ window: resultsQuery.window, items: '1' });
    if (resultsQuery.day) query.set('date', resultsQuery.day);
    if (resultsQuery.league) query.set('league', resultsQuery.league);
    setResults(null);
    setItemsReady(false);
    getJson(`/api/web/props-results?${query}`, { maxAge: 0 }).then((data) => {
      if (!alive) return;
      setResults(data);
      setItemsReady(true);
      setResultsError('');
      // Fill the date picker from the newest recorded night when we did not ask
      // for a date. Prefer dates[] so a stale server default (calendar yesterday)
      // cannot strand Results on an empty pre-tracking night.
      if (!resultsQuery.day) {
        const newest = (data?.dates || []).find(Boolean) || data?.result_date;
        if (newest) setDay(newest);
      }
    }).catch(() => { if (alive) setResultsError('Could not load recorded results. Please retry.'); });
    return () => { alive = false; };
  }, [me, resultsQuery, retry, pane]);

  useEffect(() => {
    if (resultView !== 'streaks' || !resultsQuery || !me?.premium || !me?.loggedIn) return undefined;
    let alive = true;
    const query = league ? `?league=${league}` : '';
    getJson(`/api/web/props-streaks${query}`, { maxAge: 60_000 })
      .then((data) => { if (alive) setStreaks(data.items || []); })
      .catch(() => { if (alive) setStreaks([]); });
    return () => { alive = false; };
  }, [me, league, resultView]);
  const propsCurrent = propsQuery && propsQuery.league === league && propsQuery.date === propsDay;
  const visibleFeed = propsCurrent ? feed : null;
  const sections = visibleFeed?.sections || [];
  const types = [...new Set([
    ...sections.flatMap((section) => (section.items || []).map((row) => row.prop_type)),
    ...(results?.items || []).map((row) => row.prop_type),
  ].filter(Boolean))].sort();
  const filtered = useMemo(() => filterPropResults(results?.items, { model, side: resultSide }),
  [results, model, resultSide]);
  const typeSummaries = modelPropCounts(filtered);
  const modelSummaries = modelCounts(filtered, results?.models);
  const firstModelDate = (results?.models || [])
    .filter(item => (model === 'all' ? !item.id.startsWith('pl_') : item.id === model))
    .map(item => item.first_recorded_date).filter(Boolean).sort()[0];
  const sportCounts = Object.fromEntries((visibleFeed?.counts || []).map(item => [item.league, item]));
  const loadedResultDay = resultsQuery?.day || results?.result_date || '';
  const resultsCurrent = Boolean(resultsQuery)
    && resultsQuery.league === league
    && resultsQuery.window === resultWindow
    && (!day || !loadedResultDay || day === loadedResultDay);

  function pressLoadProps() {
    if (!league) return;
    setFeedError('');
    setSelectedGame('');
    setPropsQuery({ league, date: propsDay, limit: 'all', n: Date.now() });
  }

  function pressLoadResults() {
    setResultsError('');
    setResultsQuery({ league, day: day || null, window: resultWindow, n: Date.now() });
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
          <label>Sport<select aria-label="Props sport" value={league} onChange={(e) => { setLeague(e.target.value); setSelectedGame(''); }}>
            <option value="">Choose a sport</option>
            {SPORTS.map(code => {
              const count = sportCounts[code];
              // Only annotate sports with a positive ready count (hide 0 / blank).
              const suffix = count && Number.isInteger(count.count) && count.count > 0
                ? ` (${count.count} ${count.kind} props)` : '';
              return <option key={code} value={code}>{nameOf(code)}{suffix}</option>;
            })}
          </select></label>
          <label>Props date<input aria-label="Props date" type="date" value={propsDay} onChange={(e) => {
            if (e.target.value) setPropsDay(e.target.value);
          }} /></label>
        </div>
        {gate || <>
          <div className="pp-hub-filters">
            {league && <label>Game<select aria-label="Prop game" value={selectedGame}
              onChange={e => setSelectedGame(e.target.value)}>
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
            <label>Over / Under<select aria-label="Prop prediction side" value={side} onChange={e => setSide(e.target.value)}>
              <option value="all">Both</option><option value="OVER">Over</option><option value="UNDER">Under</option>
            </select></label>
            <button type="button" className="pp-hub-load" disabled={!league} onClick={pressLoadProps}>Load props</button>
          </div>
          {feedError && <p className="pl-empty" role="alert">{feedError} <button onClick={() => setRetry((v) => v + 1)}>Retry</button></p>}
          {!league && <p className="pl-empty">Pick a sport, then press Load props.</p>}
          {league && !propsCurrent && !feedError && <p className="pl-empty">Press Load props to load {nameOf(league)} for {propsDay}.</p>}
          {propsCurrent && !visibleFeed && !feedError && <p className="pl-empty">Loading props…</p>}
          {propsCurrent && visibleFeed?.tracking_available === false && <p className="pl-empty" role="alert">Forecasts loaded, but result tracking is unavailable. Please retry.</p>}
          {propsCurrent && visibleFeed && <p className="pp-hub-muted">Prop percentages are estimates, not historical win rates.</p>}
          {sections.map((section) => {
            const rows = (section.items || []).filter((row) => (!selectedGame || row.game_id === selectedGame)
              && (market === 'all' || market === row.prop_type)
              && (lineType === 'all' || row.line_type === lineType)
              && matchesPropSide(row, side)
              && (!search || row.player_name.toLowerCase().includes(search.toLowerCase())));
            return <section key={section.league} className="pp-hub-section" data-props-sport={section.league}>
              <div className="pp-hub-section-head"><h2>{nameOf(section.league)}</h2></div>
              {side !== 'all' && <p className="pp-hub-muted">Showing {rows.length} {side === 'OVER' ? 'Over' : 'Under'} selections matching your filters. Individual models may favor the other side.</p>}
              {section.total > (section.items || []).length && <p className="pp-hub-muted">
                Showing {(section.items || []).length} of {section.total} props.
                {' '}<button type="button" onClick={() => setPropsQuery((current) => current ? { ...current, limit: 'all' } : current)}>Show more props</button>
              </p>}
              {section.building && <p className="pl-empty">Loading this sport’s props…</p>}
              {section.refresh_notice && <p className="pp-hub-muted">{section.refresh_notice}</p>}
              {!rows.length && !section.building && <p className="pl-empty">{section.reason || 'No props match these filters.'}</p>}
              {!rows.length && section.next_date && <p className="pp-hub-muted">
                <button type="button" onClick={() => setPropsDay(section.next_date)}>Next scheduled date: {section.next_date}. Press Load props.</button>
              </p>}
              {rows.map((row, index) => <article className="pp-hub-card" key={`${row.player_id}-${row.prop_type}-${row.line}-${index}`}>
                <h3>{row.player_name}</h3><p className="pp-hub-muted">{row.team}</p>
                <div className="pp-hub-market"><strong>{labelOf(row.prop_type)}</strong>
                  <span>{row.line_type === 'pl' ? 'PL line' : row.line_type === 'legacy' ? 'Recorded line' : 'Market line'} · Over / Under {row.line}</span></div>
                {row.pick && <div className="pp-hub-selection">
                  <strong>{row.strong_pick ? 'PL prediction' : 'PL lean'}: {row.pick} {row.line}</strong>
                  <strong className="pp-hub-probability">{percentOf(row.pick_probability)}</strong>
                </div>}
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
      <div className={`pl-right-stack${pane !== 'results' ? ' pl-mobile-hidden' : ''}`}>
      <aside className="pl-right pp-hub-results" aria-label="Prop results">
        <h2 className="pl-right-title">{league ? `${nameOf(league)} Prop Results` : 'Prop Results'}</h2>
        {gate || <>
          <div className="pp-hub-filters">
            <label>Date<input aria-label="Prop results date" type="date" value={day} onChange={(e) => {
              if (e.target.value) setDay(e.target.value);
            }} /></label>
            <label>Range<select aria-label="Prop results period" value={resultWindow} onChange={e => setResultWindow(e.target.value)}>
              <option value="night">This date only</option><option value="7">7 days ending this date</option>
              <option value="30">30 days ending this date</option><option value="season">Season through this date</option>
            </select></label>
            <label>Model<select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="all">Saved picks</option>{(results?.models || []).filter((item) => item.decided || item.first_recorded_date).map((item) =>
                <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <label>Over / Under<select aria-label="Prop results prediction side" value={resultSide} onChange={e => setResultSide(e.target.value)}>
              <option value="all">Both</option><option value="OVER">Over</option><option value="UNDER">Under</option>
            </select></label>
            <button type="button" className="pp-hub-load" onClick={pressLoadResults}>Load results</button>
          </div>
          {resultsError && <p className="pl-empty" role="alert">{resultsError} <button onClick={() => setRetry((v) => v + 1)}>Retry</button></p>}
          {!resultsCurrent && !resultsError && <p className="pl-empty">Press Load results for the sport, date, and range above.</p>}
          {resultsCurrent && !results && !resultsError && <p className="pl-empty">Loading results…</p>}
          {resultsCurrent && results && <>
            <p className="pp-hub-muted">{results.window === 'night'
              ? `Showing ${results.result_date} only.`
              : `Showing ${results.start_date} through ${results.result_date}.`}</p>
            <p className="pp-hub-muted">{results.notice}</p>
            <div className="pp-hub-filters" role="tablist" aria-label="Results or streaks">
              <button type="button" className={resultView === 'results' ? 'is-active' : ''} onClick={() => setResultView('results')}>Results</button>
              <button type="button" className={resultView === 'streaks' ? 'is-active' : ''} onClick={() => setResultView('streaks')}>Streaks</button>
            </div>
            {resultView === 'streaks' && <>
              {!streaks && <p className="pp-hub-muted">Loading streaks…</p>}
              {streaks && !streaks.length && <p className="pl-empty">No graded streaks yet.</p>}
              {streaks && streaks.length > 0 && (
                <div className="pp-hub-table" role="region" aria-label="Players on streaks" tabIndex={0}><table>
                  <thead><tr><th>Player</th><th>Prop</th><th>Streak</th><th>Record</th><th>Hit %</th></tr></thead>
                  <tbody>{streaks.map((item) => (
                    <tr key={`${item.league}-${item.player_name}-${item.prop_type}`}>
                      <th scope="row">{item.player_name}{!league && ` · ${item.league}`}</th>
                      <td>{labelOf(item.prop_type)}</td>
                      <td>{item.streak_type === 'HIT' ? `W${item.streak_len}` : `L${item.streak_len}`}</td>
                      <td>{item.hits}-{item.misses}</td>
                      <td>{item.hit_rate}%</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </>}
            {resultView === 'results' && <>
            {firstModelDate && day < firstModelDate && <p className="pl-empty">
              {model === 'all' ? 'Named-model prop tracking' : `${results.models.find(item => item.id === model)?.name || 'Model'} prop tracking`}
              {' '}began on {firstModelDate}. This date range ends before those records begin; earlier selections cannot be counted as model-specific results.
              {' '}<button type="button" onClick={() => { setDay(firstModelDate); setResultWindow('night'); setResultsQuery({ league, day: firstModelDate, window: 'night', n: Date.now() }); }}>View {firstModelDate} results</button>
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
            {!itemsReady && (results.items_deferred || !(results.items || []).length) && (
              <p className="pp-hub-muted">Loading individual recorded props in the background…</p>
            )}
            {itemsReady && !filtered.length && <p className="pl-empty">{model !== 'all'
              ? `No separate ${results.models.find(item => item.id === model)?.name || 'model'} predictions were recorded for this date and these filters. Select All models to view any older unassigned records.`
              : 'No recorded props match this date and these filters.'}</p>}
            {filtered.slice(0, visibleResults).map((row) => <article className="pp-hub-card" key={`${row.league}-${row.id}`}>
              <h4>{row.player_name} · {labelOf(row.prop_type)}</h4>
              <p className="pp-hub-muted">{row.league} · {row.pick_date} · {row.team}</p>
              {row.model_id && (
                <p><strong>{row.model_name}</strong>
                  {row.probability != null && <span className="pp-hub-probability">{percentOf(row.probability)}</span>}
                </p>
              )}
              <p>{row.pick} {row.line} · Actual: {row.actual ?? 'not in yet'} · {row.model_id ? `Model ${row.result}` : (row.selection_result || 'not graded')}</p>
              {row.model_id && row.reason ? <p className="pp-hub-muted">{row.reason}</p> : null}
            </article>)}
            {filtered.length > visibleResults && <p className="pp-hub-muted">
              Showing {visibleResults} of {filtered.length} records. Summary tables include all matching records.
              {' '}<button type="button" onClick={() => setVisibleResults(count => count + 40)}>Show more results</button>
            </p>}
            </>}
          </>}
        </>}
      </aside>
      </div>
    </>
  );
}
