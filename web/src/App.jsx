import { useEffect, useState } from 'react';
import Header from './components/Header.jsx';
import LiveStrip from './components/LiveStrip.jsx';
import Footer from './components/Footer.jsx';
import SportsList from './components/SportsList.jsx';
import SportPage from './pages/SportPage.jsx';
import HomePage from './pages/HomePage.jsx';
import PropsPage from './pages/PropsPage.jsx';
import AllSportsPage from './pages/AllSportsPage.jsx';
import { SPORT_SLUGS } from './data/sportSlugs.js';
import { prefetchJson } from './api.js';

// Sports pages live at /<sport>-picks and /<sport>-projections; both show the same page.
const SPORT_PATH = new RegExp(`^/(${SPORT_SLUGS.join('|')})-(picks|projections|predictions)$`);
const ALL_PATHS = ['/all-sports-picks', '/all-sports-projections', '/all-sports-predictions'];

// The React page for an address, or null when the address is a server page.
function appRoute(pathname) {
  const clean = pathname.replace(/\/+$/, '') || '/';
  const match = clean.match(SPORT_PATH);
  if (match) return { page: 'sport', slug: `${match[1]}-picks`, sport: match[1] };
  if (clean === '/player-props') return { page: 'props', slug: null };
  if (ALL_PATHS.includes(clean)) return { page: 'all', slug: null };
  if (clean === '/') return { page: 'home', slug: null };
  return null;
}

function routeFromPath(pathname) {
  return appRoute(pathname) || { page: 'home', slug: null };
}

// Same titles and descriptions the server writes for these addresses (react_shell.py).
const SPORT_NAMES = { soccer: 'Soccer', tennis: 'Tennis', golf: 'Golf' };
function pageMeta(route) {
  if (route.page === 'sport') {
    const name = SPORT_NAMES[route.sport] || route.sport.toUpperCase();
    return [
      `${name} Predictions Today: AI Win Probabilities | Prediction Lab`,
      `Today's ${name} predictions from Prediction Lab: win probabilities from six models, model lines, and every result graded in public.`,
    ];
  }
  if (route.page === 'props') return ['Player Props Predictions | Prediction Lab', 'AI player prop projections with each model graded in public.'];
  if (route.page === 'all') return ['All Sports Predictions Today | Prediction Lab', "Every sport's AI predictions on one page, with each prediction type's recent record."];
  return [
    'Prediction Lab | AI Sports Predictions for Every Sport',
    "Today's AI predictions and win probabilities for the NFL, NBA, NHL, MLB, college sports, soccer, tennis, UFC and golf, with every model graded in public.",
  ];
}

function setMeta(route, url) {
  const [title, description] = pageMeta(route);
  document.title = title;
  document.querySelector('meta[name="description"]')?.setAttribute('content', description);
  document.querySelector('meta[property="og:title"]')?.setAttribute('content', title);
  document.querySelector('meta[property="og:description"]')?.setAttribute('content', description);
  const canonical = `https://predictionlab.io${url.pathname.replace(/\/+$/, '') || '/'}`;
  document.querySelector('link[rel="canonical"]')?.setAttribute('href', canonical);
  document.querySelector('meta[property="og:url"]')?.setAttribute('content', canonical);
}

// A same-site link to one of the React pages, or null.
function appLink(event) {
  const link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
  if (!link || link.target === '_blank' || link.hasAttribute('download')) return null;
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin) return null;
  const route = appRoute(url.pathname);
  return route ? { url, route } : null;
}

// What a page needs first, loaded when the visitor points at its link.
function preload(route) {
  if (route.page === 'sport' && route.slug !== 'golf-picks') prefetchJson(`/api/web/sports/${route.slug}/cards`);
  if (route.page === 'all') prefetchJson('/api/web/all-sports');
}

function currentAddress() {
  return window.location.pathname + window.location.search;
}

export default function App() {
  const [address, setAddress] = useState(currentAddress);
  const route = routeFromPath(window.location.pathname);

  // Links to the React pages switch the page in place: no reload, the header, live row and
  // sports list stay, and the new page paints from what is already loaded.
  useEffect(() => {
    const onClick = (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const found = appLink(event);
      if (!found) return;
      const { url, route: next } = found;
      const target = url.pathname + url.search;
      if (target === currentAddress() && url.hash) return;
      event.preventDefault();
      if (target !== currentAddress()) window.history.pushState({}, '', target + url.hash);
      setMeta(next, url);
      setAddress(currentAddress());
      window.scrollTo({ top: 0 });
      if (typeof window.gtag === 'function') {
        window.gtag('event', 'page_view', { page_location: window.location.href, page_title: document.title });
      }
    };
    const onPoint = (event) => {
      const found = appLink(event);
      if (found) preload(found.route);
    };
    const onBack = () => {
      setMeta(routeFromPath(window.location.pathname), window.location);
      setAddress(currentAddress());
    };
    document.addEventListener('click', onClick);
    document.addEventListener('mouseover', onPoint);
    document.addEventListener('touchstart', onPoint, { passive: true });
    window.addEventListener('popstate', onBack);
    return () => {
      document.removeEventListener('click', onClick);
      document.removeEventListener('mouseover', onPoint);
      document.removeEventListener('touchstart', onPoint);
      window.removeEventListener('popstate', onBack);
    };
  }, []);

  return (
    <div className="pl-app">
      <Header />
      <LiveStrip />
      <div className="pl-body">
        <SportsList activeSlug={route.slug} address={address} />
        <div className="pl-stage">
          {route.page === 'props' && <PropsPage key={address} />}
          {route.page === 'sport' && <SportPage key={address} slug={route.slug} />}
          {route.page === 'all' && <AllSportsPage key={address} />}
          {route.page === 'home' && <HomePage key={address} />}
        </div>
      </div>
      <Footer />
    </div>
  );
}
