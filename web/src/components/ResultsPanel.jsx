import { useRef, useState } from 'react';
import { routeLinks, trimFrame } from '../frameTools.js';

// The sport's results page in the right column. Cards and Chart are its two views (the Chart view is the
// same page with ?view=chart), so this panel's buttons replace the page's own. The site's header and footer
// are hidden inside the column. Links inside the page are routed by frameTools.routeLinks.
export default function ResultsPanel({ src, picksPath, onPredictions }) {
  const frame = useRef(null);
  const [view, setView] = useState('cards');
  const url = view === 'chart' ? `${src}?view=chart` : src;
  return (
    <>
      <div className="pl-results-views" role="tablist">
        <button type="button" role="tab" aria-selected={view === 'cards'} className={view === 'cards' ? 'is-active' : ''} onClick={() => setView('cards')}>
          Cards
        </button>
        <button type="button" role="tab" aria-selected={view === 'chart'} className={view === 'chart' ? 'is-active' : ''} onClick={() => setView('chart')}>
          Chart
        </button>
      </div>
      <iframe
        ref={frame}
        className="pl-results-frame"
        title="Results"
        src={url}
        onLoad={() => {
          trimFrame(frame.current);
          routeLinks(frame.current, picksPath, onPredictions);
        }}
      />
    </>
  );
}
