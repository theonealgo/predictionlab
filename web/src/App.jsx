import Header from './components/Header.jsx';
import LiveStrip from './components/LiveStrip.jsx';
import Footer from './components/Footer.jsx';
import SportsList from './components/SportsList.jsx';
import SportPage from './pages/SportPage.jsx';
import HomePage from './pages/HomePage.jsx';
import PropsPage from './pages/PropsPage.jsx';
import AllSportsPage from './pages/AllSportsPage.jsx';
import { SPORT_SLUGS } from './data/sportSlugs.js';

// Sports pages live at /<sport>-picks and /<sport>-projections; both show the same page.
const SPORT_PATH = new RegExp(`^/(${SPORT_SLUGS.join('|')})-(picks|projections)$`);

function routeFromPath(pathname) {
  const clean = pathname.replace(/\/+$/, '') || '/';
  const match = clean.match(SPORT_PATH);
  if (match) {
    return { page: 'sport', slug: `${match[1]}-picks` };
  }
  if (clean === '/player-props') {
    return { page: 'props', slug: null };
  }
  if (clean === '/all-sports-picks' || clean === '/all-sports-projections') {
    return { page: 'all', slug: null };
  }
  return { page: 'home', slug: null };
}

export default function App() {
  const route = routeFromPath(window.location.pathname);
  return (
    <div className="pl-app">
      <Header />
      <LiveStrip />
      <div className="pl-body">
        <SportsList activeSlug={route.slug} />
        <div className="pl-stage">
          {route.page === 'props' && <PropsPage />}
          {route.page === 'sport' && <SportPage key={route.slug} slug={route.slug} />}
          {route.page === 'all' && <AllSportsPage />}
          {route.page === 'home' && <HomePage />}
        </div>
      </div>
      <Footer />
    </div>
  );
}
