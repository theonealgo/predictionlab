import { useEffect, useState } from 'react';
import { getJson } from '../api.js';

// Golf tournaments, with the same content as the classic golf page: the tournament picker, our picks for
// the chosen tournament, and the full field. The data is read from that page on the server.
const GROUPS = ['This week', 'Upcoming', 'Completed'];

export default function GolfPanel() {
  const [eventId, setEventId] = useState(() => new URLSearchParams(window.location.search).get('event') || '');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    getJson(`/api/web/golf${eventId ? `?event=${eventId}` : ''}`)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  if (error) return <p className="pl-empty">Could not load tournaments: {error}</p>;
  if (!data) return <p className="pl-empty">Loading tournaments…</p>;
  if (data.events.length === 0) return <p className="pl-empty">No tournaments are listed right now.</p>;

  const selected = data.events.find((event) => event.id === data.selected) || data.events[0];

  return (
    <>
      {GROUPS.map((group) => {
        const events = data.events.filter((event) => event.group === group);
        if (events.length === 0) return null;
        return (
          <section className="pl-golf-group" key={group}>
            <h2 className="pl-day-title">{group}</h2>
            <div className="pl-conf-row is-bubbles">
              {events.map((event) => (
                <button
                  key={event.id}
                  type="button"
                  className={event.id === selected.id ? 'is-active' : ''}
                  onClick={() => setEventId(event.id)}
                >
                  {event.name}
                </button>
              ))}
            </div>
          </section>
        );
      })}

      <h2 className="pl-golf-title">
        {selected.name}
        <span>{selected.date} · {selected.status}</span>
      </h2>

      <h2 className="pl-day-title">Our predictions</h2>
      {data.picks.length === 0 && <p className="pl-empty">No predictions for this tournament yet.</p>}
      <div className="pl-golf-picks">
        {data.picks.map((pick) => (
          <div className="pl-golf-pick" key={pick.market}>
            <h3>{pick.market}</h3>
            <p>{pick.note}</p>
            <table className="pl-golf-table">
              <tbody>
                {pick.rows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td className="num">{row.chance}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <h2 className="pl-day-title">Field</h2>
      {data.field.length === 0 && <p className="pl-empty">The field is not listed yet.</p>}
      {data.field.length > 0 && (
        <table className="pl-golf-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Player</th>
              <th className="num">Win</th>
              <th className="num">Top 10</th>
              <th className="num">Top 20</th>
            </tr>
          </thead>
          <tbody>
            {data.field.map((row) => (
              <tr key={`${row.rank}-${row.player}`}>
                <td>#{row.rank}</td>
                <td>
                  <span className="pl-golf-name">
                    {row.photo && <img src={row.photo} alt="" loading="lazy" />}
                    {row.player}
                  </span>
                </td>
                <td className="num">{row.win}</td>
                <td className="num">{row.top10}</td>
                <td className="num">{row.top20}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
