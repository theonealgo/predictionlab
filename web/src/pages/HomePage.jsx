import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import ChartFrame from '../components/ChartFrame.jsx';
import { publicPath } from '../data/sportSlugs.js';
import { readCardPreview, readGolfPreview } from '../homePreview.js';

// The sports that have a chart view on their results page.
const CHART_SPORTS = new Set(['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA', 'CFL']);

const FEATURED_CARDS = 6;

function sportLabel(sport) {
  return String(sport?.name || sport?.key || '').trim();
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
        const shown = (today.length ? today : list.filter((item) => item.status === 'In season')).slice(0, FEATURED_CARDS);
        if (!shown.length) {
          setNoSports(true);
          return;
        }
        setFeatured(shown);
        setChartSlug((list.find((item) => item.status === 'Today') || list[0]).slug);

        const apply = (item, payload) => {
          if (cancelled) return;
          const preview = item.key === 'GOLF'
            ? readGolfPreview(payload)
            : readCardPreview(payload?.cards?.[0]);
          setFeeds((current) => ({
            ...current,
            [item.slug]: {
              total: payload?.total || 0,
              preview: preview || null,
            },
          }));
        };
        const fail = (item) => {
          if (!cancelled) {
            setFeeds((current) => ({
              ...current,
              [item.slug]: { total: 0, preview: null },
            }));
          }
        };
        const loadOne = async (item) => {
          if (item.key === 'GOLF') {
            apply(item, { cards: [], total: 0 });
            return;
          }
          try {
            const payload = await getJson(
              `/api/web/sports/${item.slug}/cards?limit=1&cached=1`,
              { maxAge: 60_000 },
            );
            if (cancelled) return;
            apply(item, payload || { cards: [], total: 0 });
          } catch (error) {
            fail(item);
          }
        };
        shown.forEach((item) => { loadOne(item); });
      })
      .catch(() => {
        if (!cancelled) setSportsError(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const chartSport = sports?.find((item) => item.slug === chartSlug);
  // Only sports with a real preview card — never empty "no upcoming" shells.
  const visible = featured.filter((sport) => Boolean(feeds[sport.slug]?.preview));
  const stillLoading = featured.some((sport) => !feeds[sport.slug]);

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
        {!noSports && !sportsError && (!sports || stillLoading) && !visible.length && (
          <p className="pl-empty">Loading games…</p>
        )}
        {mode === 'cards' && sports && !stillLoading && !visible.length && (
          <p className="pl-empty">No upcoming cards to show right now.</p>
        )}

        {mode === 'cards' && visible.map((sport) => {
          const feed = feeds[sport.slug];
          const preview = feed.preview;
          const name = sportLabel(sport);
          const href = publicPath(sport.slug);
          return (
            <section className="pl-home-sport" key={sport.slug} data-home-sport={sport.key}>
              <div className="pl-home-sport-label">{name}</div>
              <h2 className="pl-home-sport-title">
                <span className="pl-home-sport-name">{name}</span>
                <a href={href}>
                  {feed.total ? `See all ${feed.total} ${name} games` : `Open ${name}`}
                </a>
              </h2>
              <a
                className="pl-home-preview"
                href={`${href}?${new URLSearchParams(preview.query || { game: preview.game })}`}
              >
                <h3>{preview.title || preview.teams.slice(0, 2).map((team) => team.name).join(' vs ')}</h3>
                <div className="pl-home-preview-meta">
                  {[preview.date, preview.time].filter(Boolean).join(' · ')}
                </div>
                <div className="pl-home-preview-teams">
                  {preview.teams.map((team, index) => (
                    <div className="pl-home-preview-team" key={`${index}-${team.name}`}>
                      {team.logo ? (
                        <img
                          src={team.logo}
                          alt=""
                          width="28"
                          height="28"
                          loading="lazy"
                          decoding="async"
                          onError={(event) => {
                            const image = event.currentTarget;
                            if (!image.dataset.fallback) {
                              image.dataset.fallback = '1';
                              image.src = '/static/pl-logo.svg';
                            }
                          }}
                        />
                      ) : (
                        <span className="pl-home-preview-logo" />
                      )}
                      <div className="pl-home-preview-name">
                        <span>{team.name}</span>
                        {team.percent !== null && (
                          <span className="pl-home-preview-track" aria-hidden="true">
                            <span style={{ width: `${team.percent}%` }} />
                          </span>
                        )}
                      </div>
                      {(team.percent !== null || !preview.confidence) && (
                        <span
                          className="pl-home-preview-percent"
                          title={team.percent === null ? team.reason : undefined}
                        >
                          {team.percent === null ? 'N/A' : `${team.percent}%`}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                {preview.confidence && (
                  <div className="pl-home-preview-confidence">
                    <span>Forecast confidence · {preview.confidence.team}</span>
                    <strong className="pl-home-preview-percent">{preview.confidence.percent}%</strong>
                  </div>
                )}
              </a>
            </section>
          );
        })}

        {mode === 'chart' && sports && (
          <section className="pl-home-sport">
            <h2 className="pl-home-sport-title">
              <select aria-label="Chart sport" value={chartSlug} onChange={(event) => setChartSlug(event.target.value)}>
                {sports.filter((item) => CHART_SPORTS.has(item.key)).map((item) => (
                  <option value={item.slug} key={item.slug}>{sportLabel(item)}</option>
                ))}
              </select>
              {chartSport && <a href={publicPath(chartSport.slug)}>Open {sportLabel(chartSport)}</a>}
            </h2>
            {chartSport && CHART_SPORTS.has(chartSport.key) ? (
              <ChartFrame
                key={chartSport.slug}
                title={`${sportLabel(chartSport)} chart`}
                src={`/${chartSport.slug.replace('-picks', '-results')}?view=chart`}
              />
            ) : (
              <p className="pl-empty">Chart view is not available for this sport.</p>
            )}
          </section>
        )}
      </main>
    </>
  );
}
