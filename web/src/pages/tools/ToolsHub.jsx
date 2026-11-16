import BlogFeedBox from '../../components/BlogFeedBox.jsx';
import { TOOLS } from '../../data/toolsCatalog.js';

export default function ToolsHub() {
  return (
    <div className="pl-tools-layout">
      <main className="pl-tools-main">
        <header className="pl-tools-intro">
          <p className="pl-tools-kicker">Betting Tools</p>
          <h1>Sports Betting Calculators</h1>
          <p className="pl-tools-sub">
            Free odds, parlay, hold, no-vig, CLV, and Kelly tools — built to match PredictionLab&apos;s
            probability-first workflow.
          </p>
        </header>
        <div className="pl-tools-hub-grid">
          {TOOLS.map((tool) => (
            <a key={tool.path} className="pl-tools-hub-card" href={tool.path}>
              <h2>{tool.title}</h2>
              <p>{tool.short}</p>
              <span>Open calculator →</span>
            </a>
          ))}
        </div>
      </main>
      <aside className="pl-tools-side">
        <div className="pl-tools-cta">
          <div className="pl-tools-cta-title">Today&apos;s model predictions</div>
          <p>Pair these calculators with graded PredictionLab win probabilities.</p>
          <a className="pl-btn primary" href="/all-sports-predictions">View today&apos;s predictions</a>
        </div>
        <BlogFeedBox />
      </aside>
    </div>
  );
}
