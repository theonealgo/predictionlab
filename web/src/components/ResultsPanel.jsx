import { useEffect, useRef, useState } from 'react';
import { routeLinks, trimFrame, whenFrameParsed } from '../frameTools.js';

// Predictions (middle column) own the worker first. Results start only after
// `enabled` (live cards fetch finished), then may take up to 15s — never a minute.

const RESULTS_START_AFTER_MS = 2000;
const RESULTS_GIVE_UP_MS = 15_000;

function holdLastNightCards(frame) {
  const doc = frame && frame.contentDocument;
  if (!doc || doc.body.dataset.lastNightHeld) return;
  doc.querySelectorAll('[data-results-share="1"], .social-export-wrap').forEach((node) => node.remove());
  const cards = [...doc.querySelectorAll('.game-card-stack, [data-pick-card], .game-card')];
  if (!cards.length) return;
  doc.body.dataset.lastNightHeld = '1';
  const held = doc.createElement('template');
  const mount = cards[0].parentNode;
  const marker = doc.createElement('div');
  mount.insertBefore(marker, cards[0]);
  cards.forEach((card) => held.content.appendChild(card));
  const button = doc.createElement('button');
  button.type = 'button';
  button.textContent = 'Show this date';
  button.setAttribute('style', 'margin:12px 0;padding:8px 12px;border:1px solid #2a6;background:#143;color:#fff;border-radius:8px;cursor:pointer;font:inherit;');
  marker.replaceWith(button);
  button.addEventListener('click', () => {
    const picked = doc.querySelector('[aria-pressed="true"], button.is-active, button.active');
    const label = picked ? picked.textContent : '';
    const months = { jan: '01', feb: '02', mar: '03', apr: '04', may: '05', jun: '06', jul: '07', aug: '08', sep: '09', oct: '10', nov: '11', dec: '12' };
    const named = /([A-Za-z]{3})\s+(\d{1,2})/.exec(label || '');
    const iso = named ? `-${months[named[1].toLowerCase()] || ''}-${named[2].padStart(2, '0')}` : '';
    [...held.content.querySelectorAll('.game-card-stack, [data-pick-card], .game-card')].forEach((card) => {
      const stamp = `${card.getAttribute('data-date') || ''} ${card.textContent || ''}`;
      if (iso && !stamp.includes(iso)) return;
      mount.appendChild(card);
    });
    button.remove();
  });
}

export default function ResultsPanel({ src, picksPath, onPredictions, enabled = true }) {
  const frame = useRef(null);
  const [view, setView] = useState('cards');
  const [requested, setRequested] = useState(false);
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const want = Boolean(enabled || requested);
  const url = view === 'chart' ? `${src}?view=chart` : src;

  useEffect(() => {
    setLoaded(false);
    setTimedOut(false);
    setReady(false);
  }, [url]);

  useEffect(() => {
    if (!want) {
      setReady(false);
      setTimedOut(false);
      return undefined;
    }
    let cancelled = false;
    // Cards finished; brief pause so the worker can breathe, then start results.
    const timer = window.setTimeout(() => {
      if (!cancelled) setReady(true);
    }, requested ? 0 : RESULTS_START_AFTER_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [want, url, requested]);

  useEffect(() => {
    if (!ready || loaded || timedOut) return undefined;
    const timer = window.setTimeout(() => setTimedOut(true), RESULTS_GIVE_UP_MS);
    return () => window.clearTimeout(timer);
  }, [ready, loaded, timedOut, url]);

  // Drop the iframe on timeout so the browser aborts the hung results request.
  useEffect(() => {
    if (!timedOut) return;
    setReady(false);
  }, [timedOut]);

  const prepare = () => {
    trimFrame(frame.current);
    routeLinks(frame.current, picksPath, onPredictions);
    if (view === 'cards') holdLastNightCards(frame.current);
    setLoaded(true);
    setTimedOut(false);
  };

  useEffect(() => (ready && !timedOut && frame.current ? whenFrameParsed(frame.current, url, prepare) : undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [url, ready, timedOut]);

  const retry = () => {
    setTimedOut(false);
    setLoaded(false);
    setReady(false);
    setRequested(true);
    window.setTimeout(() => setReady(true), 50);
  };

  const waiting = want && !loaded && !timedOut;
  const idle = !want && !loaded && !timedOut;

  return (
    <>
      <div className="pl-results-views" role="tablist">
        <button type="button" role="tab" aria-selected={view === 'cards'} className={view === 'cards' ? 'is-active' : ''} onClick={() => { setView('cards'); setRequested(true); }}>
          Cards
        </button>
        <button type="button" role="tab" aria-selected={view === 'chart'} className={view === 'chart' ? 'is-active' : ''} onClick={() => { setView('chart'); setRequested(true); }}>
          Chart
        </button>
      </div>
      <div className={`pl-results-shell${loaded ? ' is-loaded' : ''}`}>
        {idle && <p className="pl-empty pl-results-pending">Waiting for predictions…</p>}
        {waiting && <p className="pl-empty pl-results-pending">Loading results…</p>}
        {timedOut && !loaded && (
          <div className="pl-empty pl-results-pending" role="status">
            <p>Results are taking too long.</p>
            <button type="button" className="pl-results-retry" onClick={retry}>Retry results</button>
            <p><a href={src}>Open full results page</a></p>
          </div>
        )}
        {ready && !timedOut && (
          <iframe
            ref={frame}
            className="pl-results-frame"
            title="Results"
            src={url}
            onLoad={prepare}
          />
        )}
      </div>
    </>
  );
}
