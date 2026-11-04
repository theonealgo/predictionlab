import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { SOCCER_LEAGUES } from '../data/sportNav.js';
import { publicPath } from '../data/sportSlugs.js';

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

export default function SportsList({ activeSlug }) {
  const [sports, setSports] = useState([]);
  const [open, setOpen] = useState({});

  const [tournaments, setTournaments] = useState(null);
  const [leagueCounts, setLeagueCounts] = useState({});
  const [tennisEvents, setTennisEvents] = useState([]);

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
      .then((data) => setLeagueCounts(data.today || {}))
      .catch(() => setLeagueCounts({}));
    getJson('/api/web/tennis-events')
      .then((data) => setTennisEvents(data.events || []))
      .catch(() => setTennisEvents([]));
  }, []);

  const soccerToday = Object.values(leagueCounts).reduce((sum, n) => sum + n, 0);
  const shown = soccerToday
    ? sports.map((sport) => (sport.key === 'SOCCER' ? { ...sport, status: 'Today', statusText: `Today · ${soccerToday}` } : sport))
    : sports;
  const byKey = Object.fromEntries(shown.map((sport) => [sport.key, sport]));
  const activeKey = (sports.find((sport) => sport.slug === activeSlug) || {}).key;
  const path = window.location.pathname.replace(/\/+$/, '');
  const league = new URLSearchParams(window.location.search).get('league');

  return (
    <aside className="pl-left">
      <div className="pl-left-title">Sports</div>
      <nav className="pl-sport-nav">
        {sports.length === 0 && Array.from({ length: 13 }, (_, n) => (
          <span className="pl-sport-link is-placeholder" key={n} aria-hidden="true">&nbsp;</span>
        ))}
        {shown.map((sport) => (
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
      </nav>
      <hr className="pl-nav-divider" />
      <nav className="pl-nav">
        <a href="/all-sports-predictions" className={`pl-nav-group-link${path === '/all-sports-predictions' ? ' is-active' : ''}`}>
          <span>All sports</span>
          <Count value={shown.reduce((sum, sport) => sum + todayCount(sport), 0)} />
        </a>
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
                status: leagueCounts[name] ? `Today · ${leagueCounts[name]}` : '',
                today: Boolean(leagueCounts[name]),
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
