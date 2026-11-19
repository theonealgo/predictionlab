import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { SOCCER_LEAGUES } from '../data/sportNav.js';
import { publicPath } from '../data/sportSlugs.js';

// Sports picks left rail only — Sports Props lives on /player-props (PropsNav).
const GROUPS = [
  { name: 'Football', keys: ['NFL', 'NCAAF', 'CFL'] },
  { name: 'Basketball', keys: ['NBA', 'NCAAB', 'NCAAW', 'WNBA'] },
  { name: 'Hockey', keys: ['NHL'] },
  { name: 'Baseball', keys: ['MLB'] },
  { name: 'Soccer', keys: ['SOCCER'], leagues: true },
  { name: 'Tennis', keys: ['TENNIS'], events: true },
  { name: 'UFC', keys: ['UFC'] },
  { name: 'Golf', keys: ['GOLF'], tournaments: true },
];

function todayCount(sport) {
  const match = /·\s*(\d+)/.exec(sport.statusText || '');
  return match ? Number(match[1]) : 0;
}

function Count({ value }) {
  return value > 0 ? <span className="pl-nav-count">({value})</span> : null;
}

export default function SportsList({ activeSlug }) {
  const [sports, setSports] = useState([]);
  const [open, setOpen] = useState({});
  const [tournaments, setTournaments] = useState(null);
  const [leagueCounts, setLeagueCounts] = useState({});
  const [tomorrowCounts, setTomorrowCounts] = useState({});
  const [tennisEvents, setTennisEvents] = useState([]);

  useEffect(() => {
    getJson('/api/web/sports')
      .then((data) => setSports(data.sports || []))
      .catch(() => setSports([]));
  }, []);

  const golfOpen = open.Golf ?? activeSlug === 'golf-picks';
  useEffect(() => {
    if (!golfOpen || tournaments) return;
    getJson('/api/web/golf')
      .then((data) => setTournaments(data.events || []))
      .catch(() => setTournaments([]));
  }, [golfOpen, tournaments]);

  useEffect(() => {
    let alive = true;
    getJson('/api/web/soccer-leagues')
      .then((data) => {
        if (!alive) return;
        setLeagueCounts(data.today || {});
        setTomorrowCounts(data.tomorrow || {});
      })
      .catch(() => { if (alive) setLeagueCounts({}); });
    getJson('/api/web/tennis-events')
      .then((data) => { if (alive) setTennisEvents(data.events || []); })
      .catch(() => { if (alive) setTennisEvents([]); });
    return () => { alive = false; };
  }, []);

  const soccerToday = Object.values(leagueCounts).reduce((sum, n) => sum + n, 0);
  const soccerTomorrow = Object.values(tomorrowCounts).reduce((sum, n) => sum + n, 0);
  const tennisToday = tennisEvents.reduce((sum, item) => sum + (item.today || 0), 0);
  const todayCounts = { SOCCER: soccerToday, TENNIS: tennisToday };
  const shown = sports.map((sport) => (todayCounts[sport.key]
    ? { ...sport, status: 'Today', statusText: `Today · ${todayCounts[sport.key]}` }
    : sport.key === 'SOCCER' && soccerTomorrow
      ? { ...sport, status: 'Tomorrow', statusText: `Tomorrow · ${soccerTomorrow}` }
      : sport));
  const byKey = Object.fromEntries(shown.map((sport) => [sport.key, sport]));
  const activeKey = (sports.find((sport) => sport.slug === activeSlug) || {}).key;
  const path = window.location.pathname.replace(/\/+$/, '');
  const featured = shown.filter((sport) => sport.status === 'Today' || todayCount(sport) > 0);
  const other = shown.filter((sport) => !(sport.status === 'Today' || todayCount(sport) > 0));
  const todayTotal = shown.reduce((sum, sport) => sum + todayCount(sport), 0);

  return (
    <aside className="pl-left">
      <nav className="pl-sport-nav">
        {sports.length === 0 && Array.from({ length: 10 }, (_, n) => (
          <span className="pl-sport-link is-placeholder" key={n} aria-hidden="true">&nbsp;</span>
        ))}
        {sports.length > 0 && (
          <>
            <div className="pl-left-title">Featured</div>
            {featured.length === 0 && (
              <span className="pl-nav-status pl-nav-empty">No games today</span>
            )}
            {featured.map((sport) => (
              <a
                key={sport.slug}
                href={publicPath(sport.slug)}
                className={`pl-sport-link${sport.slug === activeSlug ? ' is-active' : ''}`}
              >
                <span>{sport.name}</span>
                <span className={`pl-sport-status${sport.status === 'Today' ? ' today' : ''}`}>
                  {sport.statusText}
                </span>
              </a>
            ))}
            {other.length > 0 && (
              <>
                <div className="pl-left-title pl-left-title-sub">Other sports</div>
                {other.map((sport) => (
                  <a
                    key={sport.slug}
                    href={publicPath(sport.slug)}
                    className={`pl-sport-link${sport.slug === activeSlug ? ' is-active' : ''}`}
                  >
                    <span>{sport.name}</span>
                    <span className={`pl-sport-status${sport.status === 'Today' ? ' today' : ''}`}>
                      {sport.statusText}
                    </span>
                  </a>
                ))}
              </>
            )}
          </>
        )}
      </nav>
      <hr className="pl-nav-divider" />
      <nav className="pl-nav">
        <a href="/all-sports-predictions" className={`pl-nav-group-link${path === '/all-sports-predictions' || path === '/all-sports-picks' || path === '/all-sports-projections' ? ' is-active' : ''}`}>
          <span>All Sports</span>
          <Count value={todayTotal} />
        </a>
        {sports.length === 0 && GROUPS.map((group) => (
          <span className="pl-nav-group-link is-placeholder" key={`sk-${group.name}`} aria-hidden="true">
            <span>{group.name}</span>
            <span className="pl-nav-status">&nbsp;</span>
          </span>
        ))}
        {GROUPS.map((group) => {
          const members = group.keys.map((key) => byKey[key]).filter(Boolean);
          if (members.length === 0) return null;
          const total = members.reduce((sum, sport) => sum + todayCount(sport), 0);
          const holdsActive = group.keys.includes(activeKey);
          const single = members.length === 1 && !group.leagues && !group.tournaments && !(group.events && tennisEvents.length);

          if (single) {
            const sport = members[0];
            return (
              <a
                key={group.name}
                href={publicPath(sport.slug)}
                className={`pl-nav-group-link${holdsActive ? ' is-active' : ''}`}
              >
                <span>{group.name}</span>
                <span className={`pl-nav-status${sport.status === 'Today' ? ' today' : ''}`}>{sport.statusText}</span>
              </a>
            );
          }

          const isOpen = open[group.name] ?? holdsActive;
          const event = new URLSearchParams(window.location.search).get('event');
          const items = group.tournaments
            ? (tournaments || []).map((item) => ({
              label: item.name,
              href: `${publicPath(members[0].slug)}?event=${encodeURIComponent(item.id)}`,
              status: item.group === 'Completed' ? 'Final' : item.group,
              today: item.group === 'This week',
              active: holdsActive && event === item.id,
            }))
            : group.events
              ? tennisEvents.map((item) => ({
                label: item.name,
                href: `${publicPath(members[0].slug)}?league=${encodeURIComponent(item.name)}`,
                status: item.today ? `Today · ${item.today}` : (item.upcoming ? `Upcoming · ${item.upcoming}` : ''),
                today: Boolean(item.today),
                active: holdsActive && new URLSearchParams(window.location.search).get('league') === item.name,
              }))
              : group.leagues
                ? SOCCER_LEAGUES.filter((item) => item && item !== 'All').map((item) => {
                  const name = typeof item === 'string' ? item : (item.label || item.slug || '');
                  return {
                    label: name,
                    href: `${publicPath(members[0].slug)}?league=${encodeURIComponent(name)}`,
                    status: leagueCounts[name]
                      ? `Today · ${leagueCounts[name]}`
                      : (tomorrowCounts[name] ? `Tomorrow · ${tomorrowCounts[name]}` : ''),
                    today: Boolean(leagueCounts[name]),
                    active: holdsActive && new URLSearchParams(window.location.search).get('league') === name,
                  };
                })
                : members.map((sport) => ({
                  label: sport.name,
                  href: publicPath(sport.slug),
                  status: sport.statusText,
                  today: sport.status === 'Today',
                  active: sport.slug === activeSlug,
                }));

          return (
            <div key={group.name} className={`pl-nav-group${isOpen ? ' is-open' : ''}`}>
              <button
                type="button"
                className={`pl-nav-group-head${holdsActive ? ' is-active' : ''}`}
                aria-expanded={isOpen}
                onClick={() => setOpen((state) => ({ ...state, [group.name]: !isOpen }))}
              >
                <span>{group.name} <Count value={total} /></span>
                <span className="pl-nav-chevron" aria-hidden="true" />
              </button>
              {isOpen && (
                <div className="pl-nav-items">
                  {group.tournaments && tournaments === null && (
                    <span className="pl-nav-status">Loading…</span>
                  )}
                  {items.map((item) => (
                    <a key={item.href + item.label} href={item.href} className={`pl-nav-item${item.active ? ' is-active' : ''}`}>
                      <span>{item.label}</span>
                      {item.status ? (
                        <span className={`pl-nav-status${item.today ? ' today' : ''}`}>{item.status}</span>
                      ) : null}
                    </a>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
