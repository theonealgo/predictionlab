import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import PlayerProps from './PlayerProps.jsx';

// PLAYERS tab: the player props for the sport on this page. The left-hand list already chose the
// sport, so the props load straight away. Visitors who are not logged in see a login prompt.
export default function PlayersPanel({ sport }) {
  const [me, setMe] = useState(null);
  const [leagues, setLeagues] = useState(null);

  useEffect(() => {
    getJson('/api/web/me')
      .then(setMe)
      .catch(() => setMe({ loggedIn: false, premium: false }));
  }, []);

  useEffect(() => {
    if (!me || !me.loggedIn) return;
    getJson('/player-props-api/leagues')
      .then((body) => setLeagues(body.leagues || []))
      .catch(() => setLeagues([]));
  }, [me]);

  if (!me) {
    return <p className="pl-empty">Loading…</p>;
  }

  if (!me.loggedIn) {
    return (
      <div className="pl-login-prompt">
        Player props are for members. <a href="/login">Log in</a> to see them, or <a href="/plans">see plans</a>.
      </div>
    );
  }

  if (leagues === null) {
    return <p className="pl-empty">Loading {sport} props…</p>;
  }

  if (!leagues.includes(sport)) {
    return <p className="pl-empty">Player props are not available for {sport} yet.</p>;
  }

  return <PlayerProps league={sport} />;
}
