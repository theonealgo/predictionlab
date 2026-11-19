import { useEffect, useRef, useState } from 'react';
import { routeLinks, trimFrame, whenFrameParsed } from '../frameTools.js';

// The sport's results page in the right column. Cards and Chart are its two views (the Chart view is the
// same page with ?view=chart), so this panel's buttons replace the page's own. The site's header and footer
// are hidden inside the column. Links inside the page are routed by frameTools.routeLinks.
// Both are set up as soon as the page starts arriving, not after every image and script has loaded.
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
  // Load as soon as the column is enabled — do not wait for IntersectionObserver
  // (mobile starts with the pane hidden, so visibility never flipped and results
  // stayed on "Loading results…" forever).
  const active = Boolean(enabled || requested);
  const url = view === 'chart' ? `${src}?view=chart` : src;

  const prepare = () => {
    trimFrame(frame.current);
    routeLinks(frame.current, picksPath, onPredictions);
    if (view === 'cards') holdLastNightCards(frame.current);
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
