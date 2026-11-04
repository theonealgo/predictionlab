import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { applyCardStyles } from '../cardStyles.js';
import ChartFrame from '../components/ChartFrame.jsx';
import { publicPath } from '../data/sportSlugs.js';

// Home page: one sport, the first one with games today. The sports list on the left is how people pick any
// other sport. The Chart button shows this sport's chart view, so people can flip between the two.
const CARDS_SHOWN = 6;
// The sports that have a chart view on their results page.
const CHART_SPORTS = new Set(['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA', 'CFL']);

export default function HomePage() {
  const [mode, setMode] = useState('cards');
  const [sport, setSport] = useState(null);
  const [noSports, setNoSports] = useState(false);
  const [cards, setCards] = useState(null);
  const [total, setTotal] = useState(0);
  const [season, setSeason] = useState([]);

  useEffect(() => {
    let cancelled = false;
    getJson('/api/web/sports')
      .then((data) => {
        if (cancelled) return null;
        const live = (data.sports || []).filter((item) => item.status !== 'Offseason');
        const first = live.find((item) => item.status === 'Today') || live[0] || null;
        if (!first) {
          setNoSports(true);
          return null;
        }
        setSport(first);
        return getJson(`/api/web/sports/${first.slug}/cards?limit=${CARDS_SHOWN}`).then((payload) => {
          if (cancelled) return;
          applyCardStyles(payload.css);
          setCards(payload.cards);
          setTotal(payload.total);
        });
      })
      .catch(() => {
        if (!cancelled) setNoSports(true);
      });

    getJson('/api/web/season')
      .then((data) => {
        if (!cancelled) setSeason(data.items || []);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <main className="pl-center">
        <div className="pl-home-head">
          <h1 className="pl-title">{sport ? sport.name : 'All sports'}</h1>
          <div className="pl-results-views" role="tablist">
            <button type="button" role="tab" aria-selected={mode === 'cards'} className={mode === 'cards' ? 'is-active' : ''} onClick={() => setMode('cards')}>
              Cards
            </button>
            <button type="button" role="tab" aria-selected={mode === 'chart'} className={mode === 'chart' ? 'is-active' : ''} onClick={() => setMode('chart')}>
              Chart
            </button>
          </div>
        </div>

        {noSports && <p className="pl-empty">No games are scheduled right now.</p>}
        {!noSports && !sport && <p className="pl-empty">Loading games…</p>}

        {sport && mode === 'cards' && (
          <section className="pl-home-sport">
            <h2 className="pl-home-sport-title">
              <span>{sport.name}</span>
              <a href={publicPath(sport.slug)}>{cards === null ? 'Loading…' : `See all ${total} games`}</a>
            </h2>
            {cards === null && <p className="pl-empty">Loading games…</p>}
            {cards !== null && cards.length === 0 && <p className="pl-empty">No games scheduled right now.</p>}
            {cards !== null && cards.map((card, index) => (
              <div
                key={`${sport.slug}-${index}`}
                className="pl-card-row"
                dangerouslySetInnerHTML={{ __html: card.html }}
              />
            ))}
          </section>
        )}

        {sport && mode === 'chart' && (
          <section className="pl-home-sport">
            <h2 className="pl-home-sport-title">
              <span>{sport.name}</span>
              <a href={publicPath(sport.slug)}>See all games</a>
            </h2>
            {CHART_SPORTS.has(sport.key) ? (
              <ChartFrame src={`/${sport.slug.replace('-picks', '-results')}?view=chart`} title={`${sport.name} chart`} />
            ) : (
              <p className="pl-empty">No chart view for this sport yet.</p>
            )}
          </section>
        )}
      </main>
      <aside className="pl-right">
        <div className="pl-right-title">Season record</div>
        {season.map((item) => (
          <div className="pl-season-row" key={item.label}>
            <span>{item.label}</span>
            <b>{item.record}</b>
            <span>{item.units}</span>
          </div>
        ))}
      </aside>
    </>
  );
}
