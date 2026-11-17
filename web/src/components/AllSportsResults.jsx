import { useEffect, useMemo, useRef, useState } from 'react';
import useVisibleOnce from '../hooks/useVisibleOnce.js';
import '../../../static/css/all-sports-results.css';

const BOARDS = [
  { id: 'ml', label: 'Season · Straight up', blurb: 'Season model records from each sport’s results board.' },
  { id: 'spread', label: 'Spread · Past 30 days', blurb: 'Past 30 days on the sport results page — recent, not season.' },
  { id: 'ou', label: 'Over / Under · Past 30 days', blurb: 'Past 30 days totals on the sport results page.' },
];

const MARKET_MODELS = [
  { id: 'xgboost', label: 'XSharp' },
  { id: 'pl', label: 'Prediction Lab' },
];

function Cell({ cell }) {
  if (!cell || cell.status === 'unavailable' || cell.status === 'not_applicable' || cell.pct == null) {
    return (
      <div className="pl-asr-cell is-empty">
        <span className="asr-status">N/A</span>
        {cell?.reason ? <small>{cell.reason}</small> : null}
      </div>
    );
  }
  return (
    <div className="pl-asr-cell">
      <span className="asr-pct">{cell.pct}%</span>
      <span className="asr-rec">{cell.record}</span>
    </div>
  );
}

export default function AllSportsResults({ enabled = true, preferSport = '' }) {
  const section = useRef(null);
  const visible = useVisibleOnce(section, enabled);
  const [sports, setSports] = useState([]);
  const [models, setModels] = useState([]);
  const [sport, setSport] = useState('');
  const [board, setBoard] = useState('ml');
  const [row, setRow] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);
  const cache = useRef(new Map());

  // Instant catalog — no board HTML, no warm-all.
  useEffect(() => {
    if (!visible) return undefined;
    const controller = new AbortController();
    fetch('/api/web/results-summary', {
      signal: controller.signal, credentials: 'same-origin', cache: 'default',
    })
      .then((res) => {
        if (!res.ok) throw new Error('catalog');
        return res.json();
      })
      .then((data) => {
        if (controller.signal.aborted) return;
        const list = data.sports || [];
        setSports(list);
        setModels(data.models || []);
        const preferred = (preferSport || '').toUpperCase();
        const pick = list.find((item) => item.key === preferred)
          || list.find((item) => item.key === 'NBA')
          || list[0];
        if (pick) setSport((current) => current || pick.key);
      })
      .catch((err) => {
        if (err.name !== 'AbortError') setError(true);
      });
    return () => controller.abort();
  }, [visible, preferSport]);

  // One sport board at a time.
  useEffect(() => {
    if (!visible || !sport) return undefined;
    const hit = cache.current.get(sport);
    if (hit) {
      setRow(hit.row);
      setPending(Boolean(hit.pending));
      setError(false);
      return undefined;
    }
    const controller = new AbortController();
    let timer;
    let attempts = 0;
    setLoading(true);
    setError(false);
    const load = () => {
      fetch(`/api/web/results-summary?sport=${encodeURIComponent(sport)}`, {
        signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
      })
        .then((res) => {
          if (!res.ok) throw new Error('board');
          return res.json();
        })
        .then((data) => {
          if (controller.signal.aborted) return;
          const next = (data.rows && data.rows[0]) || null;
          setRow(next);
          setPending(Boolean(data.pending));
          setLoading(false);
          setError(false);
          if (next && !data.pending) cache.current.set(sport, { row: next, pending: false });
          if (data.pending && attempts++ < 4) timer = setTimeout(load, 4000);
          else if (next) cache.current.set(sport, { row: next, pending: Boolean(data.pending) });
        })
        .catch((err) => {
          if (err.name === 'AbortError') return;
          setLoading(false);
          setError(true);
        });
    };
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [visible, sport]);

  const boardMeta = BOARDS.find((item) => item.id === board) || BOARDS[0];
  const columns = useMemo(() => {
    if (board === 'ml') return models.length ? models : [
      { id: 'glicko2', label: 'Grinder2' }, { id: 'trueskill', label: 'Takedown' },
      { id: 'elo', label: 'Edge' }, { id: 'xgboost', label: 'XSharp' },
      { id: 'ensemble', label: 'Market Consensus' }, { id: 'efficiency', label: 'Efficiency' },
    ];
    return MARKET_MODELS;
  }, [board, models]);

  const selected = sports.find((item) => item.key === sport);

  return (
    <section ref={section} className="pl-asr" aria-label="Sport results">
      <div className="pl-home-results-title">
        <h2>Results</h2>
        <a href={selected?.results_url || '/all-sports-results'}>
          {selected ? `Open ${selected.name}` : 'Open results page'}
        </a>
      </div>

      <div className="pl-asr-controls">
        <label className="pl-asr-field">
          <span>Sport</span>
          <select
            value={sport}
            disabled={!sports.length}
            onChange={(event) => { setSport(event.target.value); setRow(null); }}
            aria-label="Results sport"
          >
            {!sports.length && <option value="">Loading sports…</option>}
            {sports.map((item) => (
              <option key={item.key} value={item.key}>{item.name}</option>
            ))}
          </select>
        </label>
        <label className="pl-asr-field">
          <span>Board</span>
          <select
            value={board}
            onChange={(event) => setBoard(event.target.value)}
            aria-label="Results board"
          >
            {BOARDS.map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
      </div>

      <p className="pl-asr-blurb">{boardMeta.blurb}</p>
      {row?.through && board !== 'ml' && (
        <p className="pl-asr-through">Through {row.through}</p>
      )}

      {error && (
        <p className="pl-empty" role="alert">
          Could not load this board.{' '}
          <a href={selected?.results_url || '/all-sports-results'}>Open the results page</a>
        </p>
      )}
      {loading && !row && !error && <p className="pl-empty">Loading {selected?.name || 'results'}…</p>}
      {pending && row && <p className="pl-asr-pending">Updating from the live results page…</p>}

      {row && (
        <div className="pl-asr-card" key={`${sport}-${board}`}>
          <div className="pl-asr-card-head">
            <strong>{selected?.name || row.name}</strong>
            <span>{boardMeta.label}</span>
          </div>
          <ul className="pl-asr-stats">
            {columns.map((col) => {
              const cell = (row[board] || {})[col.id];
              return (
                <li key={col.id}>
                  <span className="pl-asr-label">{col.label}</span>
                  <Cell cell={cell} />
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {!loading && !error && !row && sport && (
        <p className="pl-empty">No published board for this sport yet.</p>
      )}
    </section>
  );
}
