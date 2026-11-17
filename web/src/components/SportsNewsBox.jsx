import { useEffect, useState } from 'react';
import { getJson, peekJson } from '../api.js';

const FEED = '/api/web/blog?section=news&limit=60';

/**
 * Sports News in the same bordered right-column box as Results.
 * Sports-news articles only — never Soro / gambling embeds.
 */
export default function SportsNewsBox({ className = '' }) {
  const seed = peekJson('/api/web/blog');
  const [items, setItems] = useState(() => seed?.news?.items || []);
  const [next, setNext] = useState(() => seed?.news?.nextOffset);
  const [loading, setLoading] = useState(!seed?.news);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getJson(FEED, { maxAge: 60000 })
      .then((payload) => {
        if (!alive) return;
        setItems(payload?.news?.items || []);
        setNext(payload?.news?.nextOffset ?? null);
        setError(false);
      })
      .catch(() => { if (alive) setError(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  async function loadMore() {
    if (busy || next == null) return;
    setBusy(true);
    try {
      const payload = await getJson(`/api/web/blog?section=news&offset=${next}&limit=60`);
      const page = payload?.news;
      setItems((rows) => {
        const seen = new Set(rows.map((r) => r.url));
        return [...rows, ...(page?.items || []).filter((r) => r.url && !seen.has(r.url))];
      });
      setNext(page?.nextOffset ?? null);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className={`pl-right pl-news-box ${className}`.trim()} aria-label="Sports news">
      <div className="pl-right-title">Sports News</div>
      <div className="pl-news-scroll" role="region" aria-label="Sports news articles" tabIndex={0}>
        {loading && !items.length && <p className="pl-empty">Loading sports news…</p>}
        {error && !items.length && (
          <p className="pl-empty" role="alert">Sports news could not be loaded.</p>
        )}
        {!loading && !error && !items.length && (
          <p className="pl-empty">No sports news right now.</p>
        )}
        {items.map((article) => (
          <a className="pl-news-item" key={article.url} href={article.url}>
            <img
              src={article.image || '/static/pl-logo.svg'}
              alt=""
              width="72"
              height="54"
              loading="lazy"
            />
            <span>
              <strong>{article.title}</strong>
              {(article.displayDate || article.sport) && (
                <em>{[article.sport, article.displayDate].filter(Boolean).join(' · ')}</em>
              )}
            </span>
          </a>
        ))}
        {next != null && (
          <button type="button" className="pl-news-more" disabled={busy} onClick={loadMore}>
            {busy ? 'Loading…' : 'More articles'}
          </button>
        )}
      </div>
      <a className="pl-news-all" href="/blog">View all sports news →</a>
    </aside>
  );
}
