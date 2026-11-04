import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import PlayerProps from '../components/PlayerProps.jsx';

// The player props page on its own (/player-props). Members pick a sport from the chips and the
// props load straight away.
export default function PropsPage() {
  const [me, setMe] = useState(null);
  const [leagues, setLeagues] = useState(null);
  const [league, setLeague] = useState('');

  useEffect(() => {
    getJson('/api/web/me')
      .then(setMe)
      .catch(() => setMe({ loggedIn: false, premium: false }));
  }, []);

  useEffect(() => {
    if (!me || !me.loggedIn) return;
    getJson('/player-props-api/leagues')
      .then((body) => {
        const list = body.leagues || [];
        setLeagues(list);
        setLeague((current) => current || list[0] || '');
      })
      .catch(() => setLeagues([]));
  }, [me]);

  if (!me) {
    return <main className="pp-page"><p className="pl-empty">Loading…</p></main>;
  }

  if (!me.loggedIn) {
    return (
      <main className="pp-page">
        <h1 className="pl-title">Player Props</h1>
        <div className="pl-login-prompt">
          Player props are for members. <a href="/login">Log in</a> to see them, or <a href="/plans">see plans</a>.
        </div>
      </main>
    );
  }

  return (
    <main className="pp-page">
      <h1 className="pl-title">Player Props</h1>
      {leagues === null ? (
        <p className="pl-empty">Loading…</p>
      ) : (
        <>
          <div className="pp-chips">
            {leagues.map((lg) => (
              <button
                key={lg}
                type="button"
                className={`pp-chip${lg === league ? ' is-active' : ''}`}
                onClick={() => setLeague(lg)}
              >
                {lg}
              </button>
            ))}
          </div>
          {league && <PlayerProps league={league} />}
        </>
      )}
    </main>
  );
}
