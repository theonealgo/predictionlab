import { useEffect, useState } from 'react';
import { getJson } from '../api.js';
import { useDragScroll } from '../hooks/useDragScroll.js';

// Every game dated today with no final score yet, across the sports, as boxes under the header.
// The row scrolls: drag it, use the trackpad, or use the arrows.
export default function LiveStrip() {
  const [items, setItems] = useState(null);
  const strip = useDragScroll();

  useEffect(() => {
    getJson('/api/web/live')
      .then((data) => setItems(data.items || []))
      .catch(() => setItems([]));
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
        {items.map((item) => (
          <a className="pl-live-box" key={`${item.sport}-${item.away}-${item.home}`} href={item.href}>
            <div className="pl-live-meta">{item.sport} · Today</div>
            <div className="pl-live-team"><span>{item.away}</span><b>{item.awayPct}%</b></div>
            <div className="pl-live-team"><span>{item.home}</span><b>{item.homePct}%</b></div>
          </a>
        ))}
      </div>
    </section>
  );
}
