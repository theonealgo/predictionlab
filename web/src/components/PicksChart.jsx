import { useEffect, useRef } from 'react';
import { routeLinks, trimFrame } from '../frameTools.js';

// The sport's prediction chart: the classic picks page opened in its Chart view (Moneyline / Spread /
// Totals tables, same paywall), shown in the middle column. The frame grows to the page's height.
const CHART_ONLY_CSS =
  '.pl-sport-grid{display:block!important}'
  + '.pl-sport-side,.pl-sport-results,.pv-toggle,.picks-view-controls .pv-copy{display:none!important}'
  + 'body{background:#fff!important}';

// What stays visible in the framed page: the date picker, the market buttons and the chart itself.
// Everything else (intro, previews, write-up, image) is hidden; the page layouts differ by sport.
const KEEP = '.date-nav, .picks-view-controls, .date-section';

function showChartOnly(doc) {
  doc.documentElement.setAttribute('data-view', 'classic');
  const keep = new Set(doc.querySelectorAll(KEEP));
  if (keep.size === 0) return;
  const onPath = new Set();
  keep.forEach((el) => {
    for (let node = el.parentElement; node && node !== doc.body; node = node.parentElement) onPath.add(node);
  });
  const prune = (parent) => {
    [...parent.children].forEach((child) => {
      if (keep.has(child) || /^(SCRIPT|STYLE|LINK)$/.test(child.tagName)) return;
      if (onPath.has(child)) prune(child);
      else child.style.setProperty('display', 'none', 'important');
    });
  };
  prune(doc.body);
  doc.querySelectorAll('.pv-toggle, .picks-view-controls .pv-copy').forEach((el) => {
    el.style.setProperty('display', 'none', 'important');
  });
}

export default function PicksChart({ slug, onPredictions }) {
  const frame = useRef(null);
  const observer = useRef(null);
  const picksPath = `/${slug}`;
  const src = `/classic/${slug}?${slug.startsWith('soccer') ? 'region=all&' : ''}view=chart`;

  useEffect(() => () => observer.current && observer.current.disconnect(), []);

  const onLoad = () => {
    const node = frame.current;
    if (!node) return;
    trimFrame(node);
    routeLinks(node, picksPath, onPredictions);
    try {
      const doc = node.contentDocument;
      const style = doc.createElement('style');
      style.textContent = CHART_ONLY_CSS;
      doc.head.appendChild(style);
      showChartOnly(doc);
      const section = doc.querySelector('.date-section');
      const content = (section && section.parentElement) || doc.body;
      const fit = () => {
        const bottom = content.getBoundingClientRect().bottom + doc.defaultView.scrollY;
        node.style.height = `${Math.max(400, Math.ceil(bottom) + 16)}px`;
      };
      fit();
      if (observer.current) observer.current.disconnect();
      if (window.ResizeObserver) {
        observer.current = new ResizeObserver(fit);
        observer.current.observe(content);
      }
    } catch (err) {
      // The frame keeps its default height if the page cannot be reached.
    }
  };

  return (
    <iframe
      ref={frame}
      className="pl-picks-chart-frame"
      title="Prediction chart"
      src={src}
      onLoad={onLoad}
    />
  );
}
