import { useEffect, useMemo, useState } from 'react';
import { getJson } from '../api.js';
import { useDragScroll } from '../hooks/useDragScroll.js';

// All Sports Picks: every open pick across the sports, from the same board the classic page shows
// (members only, same rule). Picks by day, a sport filter, today's Ladder pick, and the graded results.
const TABS = ['Predictions', 'Results', 'Prediction types'];

function rateOf(record) {
  return record && typeof record.rate === 'number' ? Math.round(record.rate * 1000) / 10 : null;
}

function RecordBar({ record }) {
  const rate = rateOf(record);
  if (rate === null) return <span className="pl-asp-muted">No record yet</span>;
  return (
    <span className="pl-asp-record" title={`Prediction type record: ${record.text}`}>
      <span className="pl-asp-bar"><span style={{ width: `${Math.min(100, rate)}%` }} /></span>
      <span>{record.text}</span>
    </span>
  );
}

function PickRow({ pick }) {
  return (
    <div className={`pl-asp-pick${pick.ladder ? ' is-ladder' : ''}`}>
      <div className="pl-asp-pick-main">
        <span className="pl-asp-market">{pick.market}</span>
        <span className="pl-asp-model">{pick.model}</span>
        <strong className="pl-asp-label">{pick.label}</strong>
        {pick.ladder && <span className="pl-asp-ladder-tag" title="This prediction type qualifies for The Ladder">Ladder</span>}
      </div>
      <div className="pl-asp-pick-side">
        {pick.win_pct && <span className="pl-asp-pct">{pick.win_pct}</span>}
        {pick.odds && <span className="pl-asp-price">{pick.odds}</span>}
        <RecordBar record={pick.record} />
      </div>
    </div>
  );
}

function GameCard({ game }) {
  const [away, home] = game.event.split(' @ ');
  return (
    <article className="pl-asp-game">
      <header className="pl-asp-game-head">
        <span className="pl-asp-sport">{game.sport_name}</span>
        {game.league && game.league !== game.sport && game.league !== game.sport_name && (
          <span className="pl-asp-league">{game.league}</span>
        )}
      </header>
      <div className="pl-asp-teams">
        <span>{away}</span>
        {home && <span className="pl-asp-at">@</span>}
        {home && <span>{home}</span>}
      </div>
      <div className="pl-asp-picks">
        {game.picks.map((pick, index) => <PickRow key={`${pick.market}-${pick.model}-${index}`} pick={pick} />)}
      </div>
    </article>
  );
}

function SummaryTable({ rows, label }) {
  if (!rows || rows.length === 0) return <p className="pl-asp-muted">No graded predictions.</p>;
  return (
    <table className="pl-asp-table">
      <thead><tr><th>Sport</th><th>Market</th><th>{label}</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.sport}-${row.market}`}>
            <td>{row.sport}</td><td>{row.market}</td><td>{row.record} <span className="pl-asp-muted">({row.rate})</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AllSportsPage() {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState('Predictions');
  const [sport, setSport] = useState('All');
  const [copied, setCopied] = useState(false);
  const strip = useDragScroll();

  useEffect(() => {
    let alive = true;
    let timer = null;
    const load = () => {
      getJson('/api/web/all-sports')
        .then((data) => {
          if (!alive) return;
          setPayload(data);
          // The board builds in the background on a cold start; ask again shortly.
          if (!data.locked && !data.board) timer = setTimeout(load, 8000);
        })
        .catch((err) => alive && setError(err.message));
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  const board = payload && payload.board;
  const games = board ? board.games || [] : [];

  const sports = useMemo(() => {
    const counts = new Map();
    games.forEach((g) => counts.set(g.sport_name, (counts.get(g.sport_name) || 0) + 1));
    return [...counts.entries()];
  }, [games]);

  const days = useMemo(() => {
    const map = new Map();
    games
      .filter((g) => sport === 'All' || g.sport_name === sport)
      .forEach((g) => {
        const key = g.date_label || g.date;
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(g);
      });
    return [...map.entries()];
  }, [games, sport]);

  const results = board ? (board.results || []).filter((g) => sport === 'All' || g.sport_name === sport) : [];

  const copyAll = () => {
    if (!board || !board.copy_text) return;
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(board.copy_text).then(done).catch(() => {});
    } else {
      const area = document.createElement('textarea');
      area.value = board.copy_text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand('copy');
        done();
      } catch (err) {
        // Nothing copied.
      }
      area.remove();
    }
  };

  let body = null;
  if (error) {
    body = <p className="pl-empty">Could not load predictions: {error}</p>;
  } else if (!payload) {
    body = <p className="pl-empty">Loading predictions…</p>;
  } else if (payload.locked) {
    body = (
      <div className="pl-asp-lock">
        <h2>All Sports Predictions is for members</h2>
        <p>Every sport&apos;s predictions on one page, each prediction type&apos;s recent record, and last night&apos;s results.</p>
        <a className="pl-asp-btn primary" href="/plans">Join Premium</a>
        {!payload.loggedIn && <a className="pl-asp-btn ghost" href="/login?next=/all-sports-predictions">Log in</a>}
      </div>
    );
  } else if (!board) {
    body = <p className="pl-empty">Today&apos;s predictions are loading…</p>;
  } else {
    body = (
      <>
        <div className="pl-tabs" role="tablist">
          {TABS.map((name) => (
            <button key={name} type="button" className={tab === name ? 'is-active' : ''} onClick={() => setTab(name)}>
              {name}
            </button>
          ))}
        </div>

        {tab !== 'Prediction types' && sports.length > 1 && (
          <div className="pl-team-strip pl-league-strip" ref={strip.ref} {...strip.handlers}>
            <div className="pl-team-rows">
              <div className="pl-team-row">
                {[['All', games.length], ...sports].map(([name, count]) => (
                  <button
                    key={name}
                    type="button"
                    className={`pl-league-pill${sport === name ? ' is-active' : ''}`}
                    onClick={() => setSport(name)}
                  >
                    {name} <span className="pl-asp-count">{count}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === 'Predictions' && (
          <>
            {board.ladder_today && (sport === 'All' || board.ladder_today.sport === sport) && (
              <section className="pl-asp-hero">
                <div className="pl-asp-hero-tag">Today&apos;s Ladder prediction</div>
                <div className="pl-asp-hero-pick">{board.ladder_today.pick}</div>
                <div className="pl-asp-hero-meta">
                  <span>{board.ladder_today.sport} · {board.ladder_today.event}</span>
                  {board.ladder_today.odds && <span>Price {board.ladder_today.odds}</span>}
                  {board.ladder_today.record && <span>Prediction type record {board.ladder_today.record}</span>}
                </div>
                <a className="pl-asp-hero-link" href="/ladder-picks">See The Ladder</a>
              </section>
            )}
            {days.length === 0 && <p className="pl-empty">No open predictions right now. New predictions post as each sport&apos;s slate is set.</p>}
            {days.map(([day, list]) => (
              <section key={day} className="pl-day">
                <h2 className="pl-day-title">{day}</h2>
                <div className="pl-asp-grid">
                  {list.map((game, index) => <GameCard key={`${game.sport}-${game.event}-${index}`} game={game} />)}
                </div>
              </section>
            ))}
            <p className="pl-asp-note">
              <span className="pl-asp-ladder-tag">Ladder</span> marks a pick type with {board.rules.games}+ graded predictions whose
              record clears the rate its price needs. Past records do not guarantee future results.
            </p>
          </>
        )}

        {tab === 'Results' && (
          <section className="pl-day">
            <h2 className="pl-day-title">Results · {board.results_label}</h2>
            {results.length === 0 && <p className="pl-empty">No graded predictions for this date.</p>}
            <div className="pl-asp-grid">
              {results.map((game, index) => (
                <article className="pl-asp-game" key={`${game.sport}-${game.event}-${index}`}>
                  <header className="pl-asp-game-head">
                    <span className="pl-asp-sport">{game.sport_name}</span>
                    {game.score && <span className="pl-asp-league">Final {game.score}</span>}
                  </header>
                  <div className="pl-asp-teams"><span>{game.event}</span></div>
                  <div className="pl-asp-picks">
                    {game.picks.map((pick, i) => (
                      <div className="pl-asp-pick" key={`${pick.market}-${pick.model}-${i}`}>
                        <div className="pl-asp-pick-main">
                          <span className="pl-asp-market">{pick.market}</span>
                          <span className="pl-asp-model">{pick.model}</span>
                          <strong className="pl-asp-label">{pick.label}</strong>
                        </div>
                        <span className={pick.win ? 'pl-asp-win' : 'pl-asp-loss'}>{pick.win ? 'Win' : 'Loss'}</span>
                      </div>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}

        {tab === 'Prediction types' && (
          <section className="pl-day">
            <h2 className="pl-day-title">Prediction type records</h2>
            <table className="pl-asp-table">
              <thead><tr><th>Prediction type</th><th>Gap</th><th>Record</th><th>Return</th><th /></tr></thead>
              <tbody>
                {(board.types || []).map((row) => (
                  <tr key={`${row.type}-${row.gap}`}>
                    <td>{row.type}</td><td>{row.gap}</td><td><RecordBar record={row.record} /></td><td>{row.roi}</td>
                    <td>{row.qualifies && <span className="pl-asp-ladder-tag">Ladder</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </>
    );
  }

  return (
    <>
      <main className="pl-center">
        <div className="pl-asp-head">
          <div>
            <h1 className="pl-title">All Sports Predictions</h1>
            <p className="pl-asp-sub">
              Every prediction our models make across all sports{board ? ` · Updated ${board.built_at}` : ''}
            </p>
          </div>
          {board && (
            <button type="button" className="pl-asp-btn primary" onClick={copyAll}>
              {copied ? 'Copied' : 'Copy all'}
            </button>
          )}
        </div>
        {body}
      </main>
      <aside className="pl-right">
        <div className="pl-right-title">Last night · {board ? board.results_label : ''}</div>
        {board ? <SummaryTable rows={board.summary_last_night} label="Record" /> : <p className="pl-asp-muted">—</p>}
        <div className="pl-right-title pl-asp-gap">Last 7 days</div>
        {board ? <SummaryTable rows={board.summary_last7} label="Record" /> : <p className="pl-asp-muted">—</p>}
      </aside>
    </>
  );
}
