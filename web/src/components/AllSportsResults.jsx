import { useEffect, useRef, useState } from 'react';
import useVisibleOnce from '../hooks/useVisibleOnce.js';
import '../../../static/css/all-sports-results.css';

export default function AllSportsResults({ enabled = true }) {
  const [html, setHtml] = useState('');
  const [error, setError] = useState(false);
  const section = useRef(null);
  const active = useVisibleOnce(section, enabled);
  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    let timer;
    let attempts = 0;
    async function load() {
      try {
        const response = await fetch('/api/web/results-summary', {
          signal: controller.signal, credentials: 'same-origin', cache: 'no-store',
        });
        if (!response.ok) throw new Error('Results unavailable');
        const data = await response.json();
        if (controller.signal.aborted) return;
        if (typeof data.html !== 'string') throw new Error('Invalid results');
        setHtml(data.html);
        setError(false);
        if (data.pending && attempts++ < 3) timer = setTimeout(load, 5000);
      } catch (e) {
        if (e.name !== 'AbortError') setError(true);
      }
    }
    load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active]);
  return (
    <section ref={section} aria-label="All sports results">
      <div className="pl-home-results-title">
        <h2>All sports results</h2>
        <a href="/all-sports-results">Open results page</a>
      </div>
      {error && <p className="pl-empty" role="alert">Could not load results. <a href="/all-sports-results">Open the results page to retry.</a></p>}
      {!html && !error && <p className="pl-empty">Loading results…</p>}
      {html && <div dangerouslySetInnerHTML={{ __html: html }} />}
    </section>
  );
}
