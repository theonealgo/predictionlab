import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import ChartFrame from '../components/ChartFrame.jsx';
import { publicPath } from '../data/sportSlugs.js';
import { readCardPreview, readGolfPreview } from '../homePreview.js';

// The sports that have a chart view on their results page.
const CHART_SPORTS = new Set(['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA', 'CFL']);

const FEATURED_CARDS = 6;

async function fetchFeed(path) {
  // Poll while the server builds a missing disk feed (202 building:true).
  for (let tries = 0; tries < 40; tries += 1) {
    const payload = await getJson(path, { maxAge: tries ? 0 : 60_000 });
    if (!payload?.building) return payload;
    await new Promise((resolve) => setTimeout(resolve, Math.min(30, Number(payload.retryAfter) || 2) * 1000));
  }
  return { cards: [], total: 0, building: false };
}

export default function HomePage() {
  const [mode, setMode] = useState('cards');
  const [sports, setSports] = useState(null);
  const [featured, setFeatured] = useState([]);
  const [chartSlug, setChartSlug] = useState('');
  const [noSports, setNoSports] = useState(false);
  const [sportsError, setSportsError] = useState(false);
  const [feeds, setFeeds] = useState({});

  useEffect(() => {
    let cancelled = false;
    getJson('/api/web/sports')
      .then((data) => {
        if (cancelled) return;
        const list = data.sports || [];
        setSports(list);
        if (!list.length) {
          setNoSports(true);
          return;
        }
        const today = list.filter((item) => item.status === 'Today');
        const shown = (today.length ? today : list.filter((item) => item.status !== 'Offseason')).slice(0, FEATURED_CARDS);
        setFeatured(shown);
        setChartSlug((list.find((item) => item.status === 'Today') || list[0]).slug);

        const apply = (item, payload) => {
          if (cancelled) return;
          setFeeds((current) => ({
            ...current,
            [item.slug]: {
              total: payload.total,
              preview: item.key === 'GOLF' ? readGolfPreview(payload) : readCardPreview(payload.cards?.[0]),
            },
          }));
        };
        const fail = (item) => {
          if (!cancelled) {
            setFeeds((current) => ({
              ...current,
              [item.slug]: { error: 'Could not load this sport. Open its page to retry.' },
            }));
          }
        };
        const loadOne = async (item) => {
          if (item.key === 'GOLF') {
            apply(item, { cards: [], total: 0 });
            return;
          }
          const path = `/api/web/sports/${item.slug}/cards?limit=1&cached=1`;
          try {
            const payload = await fetchFeed(path);
            if (cancelled) return;
            apply(item, payload || { cards: [], total: 0 });
          } catch (error) {
            fail(item);
          }
        };
        // Stagger slightly so workers=1 is not hit with 6 SSR builds at once,
        // but do not wait on idle (mobile Safari often never fires it).
        shown.forEach((item, index) => {
          window.setTimeout(() => { if (!cancelled) loadOne(item); }, index * 200);
        });
      })
      .catch(() => {
        if (!cancelled) setSportsError(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);
  const chartSport = sports?.find((item) => item.slug === chartSlug);

  return (
    <>
      <main className="pl-center">
        <div className="pl-home-head">
          <h1 className="pl-title">All sports</h1>
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
        {sportsError && <p className="pl-empty" role="alert">Could not load the sports list. Please reload to retry.</p>}
        {!noSports && !sportsError && !sports && (
          <>
            {Array.from({ length: 3 }, (_, n) => (
              <section className="pl-home-sport is-skeleton" key={n} aria-hidden="true">
                <div className="pl-home-sport-title">&nbsp;</div>
                <div className="pl-home-preview" />
              </section>
            ))}
          </>
        )}

        {mode === 'cards' && featured.map((item) => {
          const feed = feeds[item.slug];
          const preview = feed?.preview;
          const href = publicPath(item.slug);
          return (
            <section className="pl-home-sport" key={item.slug} data-home-sport={item.key}>
              <div className="pl-home-sport-title">
                <span>{item.label}</span>
                <a href={href}>See all{feed?.total ? ` ${feed.total} games` : ''}</a>
              </div>
              {feed?.error && <p className="pl-empty" role="alert">{feed.error}</p>}
              {!feed && <div className="pl-home-preview is-skeleton" aria-hidden="true" />}
              {feed && !feed.error && !preview && <p className="pl-empty">No upcoming {item.label} games in the window.</p>}
              {preview && (
                <a className="pl-home-preview" href={href}>
                  {preview.title && <h3>{preview.title}</h3>}
                  <div className="pl-home-preview-meta">
                    {[preview.date, preview.time, item.label].filter(Boolean).join(' · ')}
                  </div>
                  <div className="pl-home-preview-teams">
                    {preview.teams.map((team, index) => (
                      <div className="pl-home-preview-team" key={`${index}-${team.name}`}>
                        {team.logo ? <img src={team.logo} alt="" width="28" height="28" loading="lazy" decoding="async" onError={(event) => {
                          event.currentTarget.style.visibility = 'hidden';
                        }} /> : null}
                        <span>{team.name}</span>
                        {team.percent != null
                          ? <strong title={team.reason}>{team.percent}%</strong>
                          : preview.confidence && preview.confidence.team === team.name
                            ? <strong title="Published pick confidence">{preview.confidence.percent}%</strong>
                            : <span className="pl-home-preview-na" title={team.reason}>N/A</span>}
                      </div>
                    ))}
                  </div>
                </a>
              )}
            </section>
          );
        })}

        {mode === 'chart' && sports && (
          <>
            <label className="pl-home-chart-pick">
              Sport
              <select value={chartSlug} onChange={(event) => setChartSlug(event.target.value)}>
                {sports.filter((item) => CHART_SPORTS.has(item.key)).map((item) => (
                  <option value={item.slug} key={item.slug}>{item.label}</option>
                ))}
              </select>
            </label>
            {chartSport && CHART_SPORTS.has(chartSport.key) ? (
              <ChartFrame key={chartSport.slug} slug={chartSport.slug} />
            ) : (
              <p className="pl-empty">Chart view is not available for this sport.</p>
            )}
          </>
        )}
      </main>
    </>
  );
}
