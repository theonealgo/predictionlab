import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import ChartFrame from '../components/ChartFrame.jsx';
import SeasonRecord from '../components/SeasonRecord.jsx';
import { publicPath } from '../data/sportSlugs.js';
import { readCardPreview, readGolfPreview } from '../homePreview.js';

// The sports that have a chart view on their results page.
const CHART_SPORTS = new Set(['NHL', 'NBA', 'NCAAB', 'NCAAW', 'MLB', 'SOCCER', 'NFL', 'NCAAF', 'WNBA', 'CFL']);

const FEATURED_CARDS = 6;

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
            const payload = await getJson(path, { maxAge: 0 });
            if (cancelled) return;
            apply(item, payload?.building ? { cards: [], total: 0 } : payload);
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
        {!noSports && !sportsError && !sports && <p className="pl-empty">Loading games…</p>}

        {mode === 'cards' && featured.map((sport) => {
          const feed = feeds[sport.slug];
          const preview = feed?.preview;
          return <section className="pl-home-sport" key={sport.slug} data-home-sport={sport.key}>
            <h2 className="pl-home-sport-title">
              <span>{sport.name}</span>
              <a href={publicPath(sport.slug)}>{feed?.total ? `See all ${feed.total} games` : 'Open sport'}</a>
            </h2>
            {!feed && <p className="pl-empty">Loading games…</p>}
            {feed?.error && <p className="pl-empty" role="alert">{feed.error}</p>}
            {feed && !feed.error && !preview && <p className="pl-empty">{sport.status === 'Offseason' ? 'Off-season — no upcoming card available.' : 'No card is available right now. Open the sport for its current schedule.'}</p>}
            {preview && (
              <a className="pl-home-preview" href={`${publicPath(sport.slug)}?${new URLSearchParams(preview.query || { game: preview.game })}`}>
                <h3>{preview.title || preview.teams.slice(0, 2).map((team) => team.name).join(' vs ')}</h3>
                <div className="pl-home-preview-meta">
                  {[preview.date, preview.time, sport.name].filter(Boolean).join(' · ')}
                </div>
                <div className="pl-home-preview-teams">
                  {preview.teams.map((team, index) => (
                    <div className="pl-home-preview-team" key={`${index}-${team.name}`}>
                      {team.logo ? <img src={team.logo} alt="" width="28" height="28" loading="lazy" onError={(event) => {
                        const image = event.currentTarget;
                        if (!image.dataset.fallback) { image.dataset.fallback = '1'; image.src = '/static/pl-logo.svg'; }
                      }} /> : <span className="pl-home-preview-logo" />}
                      <div className="pl-home-preview-name">
                        <span>{team.name}</span>
                        {team.percent !== null && <span className="pl-home-preview-track" aria-hidden="true"><span style={{ width: `${team.percent}%` }} /></span>}
                      </div>
                      {(team.percent !== null || !preview.confidence) && (
                        <span className="pl-home-preview-percent" title={team.percent === null ? team.reason : undefined}>
                          {team.percent === null ? 'N/A' : `${team.percent}%`}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                {preview.confidence && <div className="pl-home-preview-confidence">
                  <span>Forecast confidence · {preview.confidence.team}</span>
                  <strong className="pl-home-preview-percent">{preview.confidence.percent}%</strong>
                </div>}
              </a>
            )}
          </section>;
        })}

        {chartSport && mode === 'chart' && (
          <section className="pl-home-sport">
            <h2 className="pl-home-sport-title">
              <select aria-label="Chart sport" value={chartSlug} onChange={(event) => setChartSlug(event.target.value)}>
                {sports.map((sport) => <option key={sport.slug} value={sport.slug}>{sport.name}</option>)}
              </select>
              <a href={publicPath(chartSport.slug)}>See all games</a>
            </h2>
            {CHART_SPORTS.has(chartSport.key) ? (
              <ChartFrame src={`/${chartSport.slug.replace('-picks', '-results')}?view=chart`} title={`${chartSport.name} chart`} />
            ) : (
              <p className="pl-empty">No chart view for this sport yet.</p>
            )}
          </section>
        )}
      </main>
      <aside className="pl-right">
        <SeasonRecord />
      </aside>
    </>
  );
}
