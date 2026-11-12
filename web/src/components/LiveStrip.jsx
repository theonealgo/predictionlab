import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { useDragScroll } from '../hooks/useDragScroll.js';
import { selectLiveStripItems } from '../liveStripItems.js';

// A short preview per sport, plus every live game, as boxes under the header.
// The row scrolls: drag it, use the trackpad, or use the arrows.
export default function LiveStrip() {
  const [items, setItems] = useState(null);
  const strip = useDragScroll();

  // Scores and official game status refresh independently of saved predictions.
  useEffect(() => {
    const load = () => getJson('/api/web/live', { maxAge: 0 })
      .then((data) => setItems(selectLiveStripItems(data.items)))
      .catch(() => setItems((current) => current || []));
    load();
    const timer = setInterval(load, 30 * 1000);
    return () => clearInterval(timer);
  }, []);

  // While the games load, empty boxes hold the strip's height so the page does not jump.
  if (items === null) {
    return (
      <section className="pl-live is-loading" aria-hidden="true">
        <div className="pl-live-head"><div className="pl-live-title">Live predictions</div></div>
        <div className="pl-live-row">
          {[0, 1, 2].map((n) => (
            <div className="pl-live-box" key={n}>
              <div className="pl-live-meta">&nbsp;</div>
              <div className="pl-live-team"><span>&nbsp;</span></div>
              <div className="pl-live-team"><span>&nbsp;</span></div>
            </div>
          ))}
        </div>
      </section>
    );
  }
  if (items.length === 0) {
    return null;
  }

  // Boxes grouped by league (soccer, tennis) or sport, in feed order, so groups with live games lead.
  const byGroup = new Map();
  items.forEach((item) => {
    const name = item.group || item.sport;
    if (!byGroup.has(name)) byGroup.set(name, []);
    byGroup.get(name).push(item);
  });
  const groups = [...byGroup.entries()];

  // One arrow press moves about two boxes.
  const scrollBy = (direction) => {
    strip.ref.current?.scrollBy({ left: direction * 300, behavior: 'smooth' });
  };

  return (
    <section className="pl-live">
      <div className="pl-live-head">
        <div className="pl-live-title">Live predictions</div>
        <div className="pl-live-arrows">
          <button type="button" aria-label="Scroll left" onClick={() => scrollBy(-1)}>‹</button>
          <button type="button" aria-label="Scroll right" onClick={() => scrollBy(1)}>›</button>
        </div>
      </div>
      <div className="pl-live-row" ref={strip.ref} {...strip.handlers}>
        {groups.map(([name, list]) => (
          <div className="pl-live-group" key={name}>
            <div className="pl-live-group-head">
              <span title={name}>{name}</span>
              <span>{list.length} {list.length === 1 ? 'game' : 'games'}</span>
            </div>
            <div className="pl-live-group-boxes">
              {list.map((item) => (
                <a
                  className={item.live ? 'pl-live-box is-live' : 'pl-live-box'}
                  key={`${item.sport}-${item.away}-${item.home}`}
                  href={`${item.href}?game=${encodeURIComponent(`${item.away}|${item.home}`)}`}
                >
                  <div className="pl-live-meta" title={item.scoreReason || undefined}>
                    {item.scoreState === 'delayed' ? 'Score update delayed'
                      : item.live ? `Live${item.statusDetail ? ` · ${item.statusDetail}` : ''}`
                        : item.scoreState === 'pre' ? item.statusDetail || 'Today'
                          : 'Today · score N/A'}
                  </div>
                  <div className="pl-live-columns" aria-hidden="true">
                    <span /><span>{item.scoreLabel || 'Score'}</span><span>Prediction %</span>
                  </div>
                  <div className="pl-live-team">
                    <span title={item.away}>{item.away}</span>
                    <strong title={item.scoreReason || undefined}>{item.awayScore ?? '—'}</strong>
                    <b title="Pregame prediction">{item.awayPct == null ? 'N/A' : `${item.awayPct}%`}</b>
                  </div>
                  <div className="pl-live-team">
                    <span title={item.home}>{item.home}</span>
                    <strong title={item.scoreReason || undefined}>{item.homeScore ?? '—'}</strong>
                    <b title="Pregame prediction">{item.homePct == null ? 'N/A' : `${item.homePct}%`}</b>
                  </div>
                </a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
