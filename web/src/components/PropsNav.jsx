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

function isOutOfSeason(sport) {
  if (sport.in_season === false || sport.status === 'off_season') return true;
  if (sport.status === 'unsupported') return true;
  return false;
}

export default function PropsNav({ address }) {
  const params = useMemo(() => new URLSearchParams((address || '').split('?')[1] || ''), [address]);
  const league = (params.get('league') || '').toUpperCase();
  const date = params.get('date') || todayET();
  const side = params.get('side') || '';
  const [sports, setSports] = useState([]);

  useEffect(() => {
    let alive = true;
    let timer;
    const load = () => {
      const query = new URLSearchParams({ date });
      getJson(`/api/web/props-navigation?${query}`, { maxAge: 0 })
        .then((data) => {
          if (!alive) return;
          const list = data.sports || [];
          setSports(list);
          const stillLoading = list.some(
            (sport) => sport.status === 'not_loaded' || sport.status === 'building' || sport.note === 'Loading…',
          );
          if (stillLoading || !data.complete) {
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

  const byCode = useMemo(() => Object.fromEntries(sports.map((s) => [s.league, s])), [sports]);
  const active = SPORTS.filter((code) => !isOutOfSeason(byCode[code] || {}));
  const resting = SPORTS.filter((code) => isOutOfSeason(byCode[code] || {}));

  const row = (code) => {
    const sport = byCode[code] || {};
    const note = sport.note || (sport.count > 0 ? `${sport.count} props` : 'Loading…');
    const positive = Number.isInteger(sport.count) && sport.count > 0;
    return (
      <a
        key={code}
        href={propsHref({ league: code, date, side })}
        className={`pl-sport-link${league === code ? ' is-active' : ''}`}
        title={sport.reason || note}
      >
        <span>{nameOf(code)}</span>
        {positive
          ? <span className="pl-nav-count">({sport.count})</span>
          : <span className="pl-nav-status">{note}</span>}
      </a>
    );
  };

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
        {active.map(row)}
      </nav>
      <hr className="pl-nav-divider" />
      <nav className="pl-nav" aria-label="Out of season and predictions">
        {resting.length > 0 && (
          <details className="pl-props-resting" open={resting.includes(league) || undefined}>
            <summary className="pl-props-resting-title">Out of season ({resting.length})</summary>
            {resting.map(row)}
          </details>
        )}
        <a href="/nba-predictions" className="pl-nav-group-link">
          <span>Back to predictions</span>
        </a>
      </nav>
    </aside>
  );
}
