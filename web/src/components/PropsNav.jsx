import { useEffect, useMemo, useState } from 'react';
import { getJson } from '../api.js';

/** Left rail for /player-props only — not mixed with the sports picks list. */
const SPORTS = ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'CFL', 'SOCCER', 'UFC', 'TENNIS', 'GOLF'];
const nameOf = (code) => (code === 'SOCCER' ? 'Soccer' : code);

function propsHref({ league = '', date = '', game = '', side = '' } = {}) {
  const query = new URLSearchParams();
  if (date) query.set('date', date);
  if (league) query.set('league', league);
  if (game) query.set('game', game);
  if (side) query.set('side', side);
  const qs = query.toString();
  return qs ? `/player-props?${qs}` : '/player-props';
}

function todayET() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

export default function PropsNav({ address }) {
  const params = useMemo(() => new URLSearchParams((address || '').split('?')[1] || ''), [address]);
  const league = (params.get('league') || '').toUpperCase();
  const date = params.get('date') || todayET();
  const side = params.get('side') || '';
  const [counts, setCounts] = useState({});

  useEffect(() => {
    let alive = true;
    let timer;
    const load = () => {
      const query = new URLSearchParams({ date });
      getJson(`/api/web/props-navigation?${query}`, { maxAge: 0 })
        .then((data) => {
          if (!alive) return;
          const next = {};
          (data.sports || []).forEach((sport) => {
            // Only show positive ready counts — hide null (not loaded) and 0 (no verified props).
            if (Number.isInteger(sport.count) && sport.count > 0) {
              next[sport.league] = sport.count;
            }
          });
          setCounts(next);
          // Keep refreshing while a big slate (NBA) is still missing a count.
          const missingReady = (data.sports || []).some(
            (sport) => sport.status === 'ready' && !(Number.isInteger(sport.count) && sport.count > 0),
          );
          const stillLoading = (data.sports || []).some(
            (sport) => sport.status === 'not_loaded' || sport.status === 'building',
          );
          if (missingReady || stillLoading || !data.complete) {
            timer = setTimeout(load, 8000);
          }
        })
        .catch(() => {
          if (!alive) return;
          timer = setTimeout(load, 12000);
        });
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [date]);

  return (
    <aside className="pl-left is-props-nav" aria-label="Sports Props">
      <div className="pl-left-title">Sports Props</div>
      <nav className="pl-sport-nav">
        <a
          href={propsHref({ date, side })}
          className={`pl-sport-link${!league ? ' is-active' : ''}`}
        >
          <span>All sports</span>
        </a>
        {SPORTS.map((code) => (
          <a
            key={code}
            href={propsHref({ league: code, date, side })}
            className={`pl-sport-link${league === code ? ' is-active' : ''}`}
          >
            <span>{nameOf(code)}</span>
            {counts[code] != null && <span className="pl-nav-count">({counts[code]})</span>}
          </a>
        ))}
      </nav>
      <hr className="pl-nav-divider" />
      <nav className="pl-nav">
        <a href="/nba-predictions" className="pl-nav-group-link">
          <span>Back to predictions</span>
        </a>
      </nav>
    </aside>
  );
}
