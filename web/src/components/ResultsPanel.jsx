import { useEffect, useRef, useState } from 'react';
import { routeLinks, trimFrame, whenFrameParsed } from '../frameTools.js';
import useVisibleOnce from '../hooks/useVisibleOnce.js';

// The sport's results page in the right column. Cards and Chart are its two views (the Chart view is the
// same page with ?view=chart), so this panel's buttons replace the page's own. The site's header and footer
// are hidden inside the column. Links inside the page are routed by frameTools.routeLinks.
// Both are set up as soon as the page starts arriving, not after every image and script has loaded.
export default function ResultsPanel({ src, picksPath, onPredictions, enabled = true }) {
  const frame = useRef(null);
  const [view, setView] = useState('cards');
  const [requested, setRequested] = useState(false);
  const active = useVisibleOnce(frame, enabled || requested);
  const url = view === 'chart' ? `${src}?view=chart` : src;

  const prepare = () => {
    trimFrame(frame.current);
    routeLinks(frame.current, picksPath, onPredictions);
  };

  useEffect(() => (active && frame.current ? whenFrameParsed(frame.current, url, prepare) : undefined),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [url, active]);

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
      {!active && <p className="pl-empty">Loading results…</p>}
      <iframe ref={frame} className="pl-results-frame" title="Results" src={active ? url : undefined} onLoad={prepare} />
    </>
  );
}
