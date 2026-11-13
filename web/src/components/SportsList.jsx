import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { SOCCER_LEAGUES } from '../data/sportNav.js';
import { publicPath } from '../data/sportSlugs.js';
import { propsAvailabilityBadge } from '../propsView.js';

// The left menu: every sport with its status, then a line, then the sports grouped by kind
// (Football, Basketball, ...). A group with more than one entry opens into a list; the group holding
// the open page starts open. A group's count is its sports' games today.
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

export default function SportsList({ activeSlug, address }) {
  const path = window.location.pathname.replace(/\/+$/, '');
  const query = new URLSearchParams(window.location.search);
  const propsMode = ['/sports-props', '/player-props'].includes(path);
  const league = query.get('league');
  const propsDate = query.get('date') || new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const [propsNav, setPropsNav] = useState(null);
  const [sports, setSports] = useState([]);
  const [open, setOpen] = useState({});

  const [tournaments, setTournaments] = useState(null);
  const [leagueCounts, setLeagueCounts] = useState({});
  const [tomorrowCounts, setTomorrowCounts] = useState({});
  const [tennisEvents, setTennisEvents] = useState([]);
  useEffect(() => {
    let alive = true;
    const load = () => getJson(`/api/web/props-navigation?date=${encodeURIComponent(propsDate)}`, { maxAge: 30000 })
      .then(data => { if (alive) setPropsNav(data); })
      .catch(() => { if (alive) setPropsNav(null); });
    load();
    const timer = propsMode ? setInterval(load, 30000) : null;
    return () => { alive = false; clearInterval(timer); };
  }, [propsDate, address, propsMode]);

  useEffect(() => {
    getJson('/api/web/sports')
      .then((data) => setSports(data.sports || []))
      .catch(() => setSports([]));
  }, []);

  // Golf's tournaments load the first time the Golf list is open.
  const golfOpen = open.Golf ?? activeSlug === 'golf-picks';
  useEffect(() => {
    if (!golfOpen || tournaments) return;
    getJson('/api/web/golf')
      .then((data) => setTournaments(data.events || []))
      .catch(() => setTournaments([]));
  }, [golfOpen, tournaments]);

  useEffect(() => {
    getJson('/api/web/soccer-leagues')
      .then((data) => { setLeagueCounts(data.today || {}); setTomorrowCounts(data.tomorrow || {}); })
      .catch(() => setLeagueCounts({}));
    getJson('/api/web/tennis-events')
      .then((data) => setTennisEvents(data.events || []))
      .catch(() => setTennisEvents([]));
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
  const activeKey = propsMode ? league : (sports.find((sport) => sport.slug === activeSlug) || {}).key;
  const propsByKey = Object.fromEntries((propsNav?.sports || []).map(item => [item.league, item]));
  const propsHref = (key, game) => `/sports-props?${new URLSearchParams({
    date: propsDate, ...(key ? { league: key } : {}), ...(game ? { game } : {}),
  })}`;
  const propsBadge = (keys) => {
    const reasons = keys.filter(key => propsByKey[key]?.count == null)
      .map(key => `${key}: ${propsByKey[key]?.reason || 'Availability has not loaded.'}`).join(' ');
    return <span className="pl-nav-count" title={reasons || undefined}>{propsAvailabilityBadge(keys.map(key => propsByKey[key]))}</span>;
  };

  return (
    <aside className={`pl-left${propsMode ? ' is-props-nav' : ''}`}>
      <div className="pl-left-title">Sports</div>
      <nav className="pl-sport-nav">
        {sports.length === 0 && Array.from({ length: 13 }, (_, n) => (
          <span className="pl-sport-link is-placeholder" key={n} aria-hidden="true">&nbsp;</span>
        ))}
        {shown.filter(sport => !propsMode || propsByKey[sport.key]).map((sport) => (
          <a
            key={sport.slug}
            href={propsMode ? propsHref(sport.key) : publicPath(sport.slug)}
            className={`pl-sport-link${(propsMode ? sport.key === activeKey : sport.slug === activeSlug) ? ' is-active' : ''}`}
          >
            <span>{sport.name}</span>
            <span className={`pl-sport-status${sport.status === 'Today' ? ' today' : ''}`}>
              {propsMode ? propsBadge([sport.key]) : sport.statusText}
            </span>
          </a>
        ))}
      </nav>
      <hr className="pl-nav-divider" />
      <nav className="pl-nav">
        <a href={propsHref()} className={`pl-nav-group-link pl-props-link${propsMode ? ' is-active' : ''}`}>
          <span>Sports Props</span>
          {propsBadge((propsNav?.sports || []).map(item => item.league))}
        </a>
        <a href="/all-sports-predictions" className={`pl-nav-group-link${path === '/all-sports-predictions' ? ' is-active' : ''}`}>
          <span>All sports</span>
          <Count value={shown.reduce((sum, sport) => sum + todayCount(sport), 0)} />
        </a>
        {GROUPS.map((group) => {
          const members = group.keys.map((key) => byKey[key]).filter(Boolean);
          if (members.length === 0) return null;
          if (propsMode) {
            const leagues = members.filter(member => propsByKey[member.key]);
            if (!leagues.length) return null;
            const holds = group.keys.includes(activeKey);
            const expanded = open[group.name] ?? holds;
            return <div key={group.name} className={`pl-nav-group${expanded ? ' is-open' : ''}`}>
              <button type="button" className={`pl-nav-group-head${holds ? ' is-active' : ''}`} aria-expanded={expanded}
                onClick={() => setOpen(state => ({ ...state, [group.name]: !expanded }))}>
                <span>{group.name} {propsBadge(leagues.map(item => item.key))}</span>
                <span className="pl-nav-chevron" aria-hidden="true" />
              </button>
              {expanded && <div className="pl-nav-items">{leagues.map(item => {
                const info = propsByKey[item.key];
                const gamesOpen = open[`props-${item.key}`] ?? item.key === activeKey;
                return <div key={item.key}>
                  <div className="pl-props-league">
                    <a href={propsHref(item.key)} className={`pl-nav-item${item.key === activeKey ? ' is-active' : ''}`}>
                      <span>{item.name}</span>{propsBadge([item.key])}
                    </a>
                    <button type="button" className="pl-props-games-toggle" aria-label={`${item.name} prop games`}
                      aria-expanded={gamesOpen} onClick={() => setOpen(state => ({ ...state, [`props-${item.key}`]: !gamesOpen }))}>
                      <span className="pl-nav-chevron" aria-hidden="true" />
                    </button>
                  </div>
                  {gamesOpen && <div className="pl-props-games">
                    {info.games.map(game => <a key={game.id} href={propsHref(item.key, game.id)}
                      className={`pl-nav-item${query.get('game') === game.id ? ' is-active' : ''}`}>
                      <span>{game.name}</span><span className="pl-nav-count">({game.count})</span>
                    </a>)}
                    {!info.games.length && <span className="pl-nav-status">Game availability not loaded for this date.</span>}
                  </div>}
                </div>;
              })}</div>}
            </div>;
          }
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
            ? [
              { label: 'All tournaments', href: publicPath(members[0].slug), active: holdsActive && !league },
              ...tennisEvents.map((item) => ({
                label: item.name,
                href: `${publicPath(members[0].slug)}?league=${encodeURIComponent(item.name)}`,
                status: item.today ? `Today · ${item.today}` : String(item.total),
                today: item.today > 0,
                active: holdsActive && league === item.name,
              })),
            ]
            : group.leagues
            ? [
              { label: 'All leagues', href: publicPath(members[0].slug), active: holdsActive && !league },
              ...SOCCER_LEAGUES.filter((name) => name !== 'All').map((name) => ({
                label: name,
                href: `${publicPath(members[0].slug)}?league=${encodeURIComponent(name)}`,
                status: [leagueCounts[name] ? `Today · ${leagueCounts[name]}` : '',
                  tomorrowCounts[name] ? `Tomorrow · ${tomorrowCounts[name]}` : ''].filter(Boolean).join(' / '),
                today: Boolean(leagueCounts[name] || tomorrowCounts[name]),
                active: holdsActive && league === name,
              })),
            ]
            : members.map((sport) => ({
              label: sport.name,
              href: publicPath(sport.slug),
              status: sport.statusText,
              today: sport.status === 'Today',
              active: sport.key === activeKey,
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
                  {group.tournaments && !tournaments && <span className="pl-nav-status">Loading tournaments…</span>}
                  {items.map((item) => (
                    <a key={item.label} href={item.href} className={`pl-nav-item${item.active ? ' is-active' : ''}`}>
                      <span>{item.label}</span>
                      {item.status && <span className={`pl-nav-status${item.today ? ' today' : ''}`}>{item.status}</span>}
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
