import { useEffect, useState } from 'react';
import { getJson, peekJson } from '../api.js';
import './BlogPage.css';

const FEED = '/api/web/blog';

function ArticleCard({ article, titleArt = false, textOnly = false }) {
  return (
    <article className="pl-blog-card">
      <a href={article.url}>
        {textOnly ? null : titleArt ? <div className="pl-blog-title-art" aria-hidden="true">
          <span className="pl-blog-art-brand">PredictionLab</span>
          <span className="pl-blog-art-meta">{[article.sport, article.displayDate].filter(Boolean).join(' / ')}</span>
          <strong className="pl-blog-art-title">{article.title}</strong>
          <span className="pl-blog-art-footer">predictionlab.io</span>
        </div> : <div className={`pl-blog-image${article.imageIsLogo ? ' is-logo' : ''}`}>
          <img src={article.image} alt="" width="480" height="252" loading="lazy" />
        </div>}
        <div className="pl-blog-copy">
          <p className="pl-blog-meta">
            {article.sport && <span>{article.sport}</span>}
            {article.displayDate && <time dateTime={article.date}>{article.displayDate}</time>}
          </p>
          <h3>{article.title}</h3>
          {article.excerpt && <p className="pl-blog-excerpt">{article.excerpt}</p>}
          <span className="pl-blog-read">Read article <span aria-hidden="true">→</span></span>
        </div>
      </a>
    </article>
  );
}

function ArticleWindow({ title, section, group, loading, error, onRetry }) {
  const [more, setMore] = useState([]);
  const [next, setNext] = useState(undefined);
  const [busy, setBusy] = useState(false);
  const [moreError, setMoreError] = useState('');
  useEffect(() => {
    setMore([]);
    setNext(undefined);
    setMoreError('');
  }, [group]);
  const nextOffset = next === undefined ? group?.nextOffset : next;
  const items = [...new Map([...(group?.items || []), ...more].map(item => [item.url, item])).values()];
  async function loadMore() {
    if (busy || nextOffset == null) return;
    setBusy(true);
    setMoreError('');
    try {
      const payload = await getJson(`${FEED}?section=${section}&offset=${nextOffset}`);
      const page = payload[section];
      setMore(rows => [...rows, ...(page?.items || [])]);
      setNext(page?.nextOffset ?? null);
    } catch {
      setMoreError('More articles could not be loaded. Please retry.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="pl-blog-window" aria-labelledby={`blog-${section}-title`}>
      <div className="pl-blog-window-head">
        <h2 id={`blog-${section}-title`}>{title}</h2>
      </div>
      <div className="pl-blog-scroll" role="region" aria-label={`${title} articles`} tabIndex={0} aria-busy={loading}>
        {loading && !group && <p className="pl-blog-message">Loading articles…</p>}
        {error && <p className="pl-blog-message" role="alert">Articles could not be loaded. <button type="button" onClick={onRetry}>Retry</button></p>}
        {!loading && !error && !items.length && <p className="pl-blog-message">No articles available right now.</p>}
        {items.map(article => <ArticleCard key={article.url} article={article} titleArt={section === 'latest'} textOnly={section === 'previews'} />)}
        {moreError && <p className="pl-blog-message" role="alert">{moreError}</p>}
        {nextOffset != null && <button type="button" className="pl-blog-more" disabled={busy} onClick={loadMore}>
          {busy ? 'Loading…' : 'More articles'}
        </button>}
      </div>
    </section>
  );
}

export default function BlogPage() {
  const [data, setData] = useState(() => window.__PL_BLOG__ || peekJson(FEED));
  const [loading, setLoading] = useState(!data);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    getJson(FEED, { maxAge: retry ? 0 : 60000 }).then(payload => {
      if (alive) { setData(payload); setError(false); }
    }).catch(() => { if (alive) setError(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [retry]);
  const reload = () => setRetry(value => value+1);
  return (
    <main className="pl-blog-page">
      <header className="pl-blog-intro">
        <h1>PredictionLab Blog</h1>
        <p>Sports news and game recaps.</p>
      </header>
      <div className="pl-blog-pair">
        <ArticleWindow title="Sports News" section="news" group={data?.news} loading={loading} error={error} onRetry={reload} />
        <ArticleWindow title="Game Previews" section="previews" group={data?.previews} loading={loading} error={error} onRetry={reload} />
      </div>
    </main>
  );
}
