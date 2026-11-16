import BlogFeedBox from '../../components/BlogFeedBox.jsx';
import { TOOLS } from '../../data/toolsCatalog.js';

/** Shared Tools page shell: calculator + guide left, Blog Box + CTA right. */
export default function ToolLayout({ title, subtitle, children, guide, faqJsonLd }) {
  return (
    <div className="pl-tools-layout">
      <main className="pl-tools-main">
        <header className="pl-tools-intro">
          <p className="pl-tools-kicker">
            <a href="/tools">Betting Tools</a>
          </p>
          <h1>{title}</h1>
          {subtitle && <p className="pl-tools-sub">{subtitle}</p>}
        </header>
        <section className="pl-tools-card" aria-label="Calculator">
          {children}
        </section>
        {guide && <section className="pl-tools-guide">{guide}</section>}
      </main>
      <aside className="pl-tools-side">
        <div className="pl-tools-cta">
          <div className="pl-tools-cta-title">Today&apos;s model predictions</div>
          <p>
            Compare your fair odds and Kelly stake against PredictionLab win probabilities graded in public.
          </p>
          <a className="pl-btn primary" href="/all-sports-predictions">View today&apos;s predictions</a>
          <a className="pl-tools-cta-link" href="/performance">Model performance →</a>
        </div>
        <BlogFeedBox />
        <nav className="pl-tools-more" aria-label="More betting tools">
          <div className="pl-tools-cta-title">More tools</div>
          <ul>
            {TOOLS.map((t) => (
              <li key={t.path}>
                <a href={t.path}>{t.title}</a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      {faqJsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }}
        />
      )}
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="pl-tools-field">
      <span className="pl-tools-label">{label}</span>
      {children}
      {hint && <span className="pl-tools-hint">{hint}</span>}
    </label>
  );
}

export function ResultRow({ label, value, tone }) {
  return (
    <div className={`pl-tools-result${tone ? ` is-${tone}` : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function ResetButton({ onClick }) {
  return (
    <button type="button" className="pl-tools-reset" onClick={onClick}>
      Reset
    </button>
  );
}
