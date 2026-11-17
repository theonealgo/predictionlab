import { useEffect, useState } from 'react';

const MARKETS = [
  { value: 'moneyline', label: 'Straight up' },
  { value: 'spread', label: 'Spread' },
  { value: 'total', label: 'Over/Under' },
];

/** Light season W-L / units list. Market comes from the control; rows are sport-only. */
export default function SeasonRecord() {
  const [market, setMarket] = useState('moneyline');
  const [items, setItems] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setError(false);
    const url = `/api/web/season?market=${encodeURIComponent(market)}&_=${Date.now()}`;
    fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`season ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        // Refuse a stale/wrong payload so Spread / OU never paint Straight-up numbers.
        const got = String(data?.market || '').toLowerCase();
        const want = market === 'moneyline' ? 'moneyline' : market;
        if (got && got !== want) {
          setError(true);
          setItems([]);
          return;
        }
        const rows = (data.items || []).map((item) => ({
          ...item,
          sport: item.sport || String(item.label || '').replace(
            /\s+(Moneyline|Straight up|Spread|Over\/Under)$/i,
            '',
          ).trim(),
        }));
        setItems(rows);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => { cancelled = true; };
  }, [market]);

  const marketLabel = MARKETS.find((m) => m.value === market)?.label || 'Straight up';

  return (
    <section className="pl-season-record" aria-label={`Season ${marketLabel} record`}>
      <div className="pl-home-results-title">
        <h2>Season record</h2>
        <a href="/all-sports-results">Full results</a>
      </div>
      <div className="pl-season-market" role="group" aria-label="Season record market">
        {MARKETS.map((m) => (
          <button
            key={m.value}
            type="button"
            className={`pl-season-market-btn${market === m.value ? ' is-active' : ''}`}
            aria-pressed={market === m.value}
            onClick={() => setMarket(m.value)}
          >
            {m.label}
          </button>
        ))}
      </div>
      {error && <p className="pl-empty" role="alert">Could not load season records.</p>}
      {!error && !items && <p className="pl-empty">Loading…</p>}
      {!error && items && !items.length && <p className="pl-empty">No graded season games yet.</p>}
      {items?.length > 0 && (
        <ul className="pl-season-list" key={market}>
          {items.map((item) => (
            <li key={`${market}-${item.sport}`} className={item.positive === false ? 'is-neg' : 'is-pos'}>
              <span className="pl-season-label">{item.sport}</span>
              <b className="pl-season-record-wl">{item.record}</b>
              <em className="pl-season-units">{item.units}</em>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
