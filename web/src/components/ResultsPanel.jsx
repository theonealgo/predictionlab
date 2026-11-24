import { useEffect, useRef, useState } from 'react';
import { routeLinks, trimFrame, whenFrameParsed } from '../frameTools.js';

// Predictions (middle column) own the worker first. Results start once the
// cards fetch has finished. The frame stays up until the page arrives — pulling
// it down at 15s cancelled the build, so the page was never saved and the next
// visit waited the same length of time.

const RESULTS_START_AFTER_MS = 400;


export default function ResultsPanel({ src, picksPath, onPredictions, enabled = true }) {
  const frame = useRef(null);
  const [view, setView] = useState('cards');
  const [requested, setRequested] = useState(false);
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const want = Boolean(enabled || requested);
  const url = view === 'chart' ? `${src}?view=chart` : src;

  useEffect(() => {
    setLoaded(false);
    setReady(false);
  }, [url]);

  useEffect(() => {
    if (!want) {
      setReady(false);
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

  const prepare = () => {
    trimFrame(frame.current);
    routeLinks(frame.current, picksPath, onPredictions);
    setLoaded(true);
  };

  useEffect(() => (ready && frame.current ? whenFrameParsed(frame.current, url, prepare) : undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [url, ready]);

  const waiting = want && !loaded;
  const idle = !want && !loaded;

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
        {ready && (
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
