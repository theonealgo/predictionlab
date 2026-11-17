import { useEffect, useRef, useState } from 'react';
import Header from './components/Header.jsx';
import LiveStrip from './components/LiveStrip.jsx';
import Footer from './components/Footer.jsx';
import SportsList from './components/SportsList.jsx';
import SportPage from './pages/SportPage.jsx';
import HomePage from './pages/HomePage.jsx';
import PropsPage from './pages/PropsPage.jsx';
import AllSportsPage from './pages/AllSportsPage.jsx';
import BlogPage from './pages/BlogPage.jsx';
import ModelsPage from './pages/ModelsPage.jsx';
import ToolsPage from './pages/ToolsPage.jsx';
import { SPORT_SLUGS } from './data/sportSlugs.js';
import { TOOL_BY_PATH } from './data/toolsCatalog.js';
import { prefetchJson } from './api.js';

// Sports pages live at /<sport>-picks and /<sport>-projections; both show the same page.
const SPORT_PATH = new RegExp(`^/(${SPORT_SLUGS.join('|')})-(picks|projections|predictions)$`);
const ALL_PATHS = ['/all-sports-picks', '/all-sports-projections', '/all-sports-predictions'];

// Props & Models menu + Daily Results — classic page in the center, blog feed box on the right.
// Include every public URL (and rename targets) so a 301 never lands on Home without the blogs box.
const MODELS_PAGES = {
  '/performance': 'Model Performance',
  '/ladder-picks': 'The Ladder',
  '/ladder': 'The Ladder',
  '/daily-report': 'Daily Results',
  '/our-model-vs-sportsbooks': 'Model vs Sportsbooks',
  '/our-model-vs-market': 'Model vs Sportsbooks',
  '/tutorial': 'Tutorial',
  '/ai-sports-betting-picks-today': 'AI Predictions Today',
  '/ai-sports-predictions-today': 'AI Predictions Today',
  '/ai-sports-projections-today': 'AI Predictions Today',
};

// /classic iframe path: renamed addresses still need the Flask route that serves the HTML.
const CLASSIC_IFRAME = {
  '/ladder': '/ladder-picks',
  '/ai-sports-predictions-today': '/ai-sports-betting-picks-today',
  '/ai-sports-projections-today': '/ai-sports-betting-picks-today',
  '/our-model-vs-market': '/our-model-vs-sportsbooks',
};

// The React page for an address, or null when the address is a server page.
function appRoute(pathname) {
  const clean = pathname.replace(/\/+$/, '') || '/';
  const match = clean.match(SPORT_PATH);
  if (match) return { page: 'sport', slug: `${match[1]}-picks`, sport: match[1] };
  if (clean === '/player-props' || clean === '/sports-props') return { page: 'props', slug: null };
  if (clean === '/blog') return { page: 'blog', slug: null };
  if (ALL_PATHS.includes(clean)) return { page: 'all', slug: null };
  if (clean === '/tools' || TOOL_BY_PATH[clean]) {
    return { page: 'tools', slug: null, path: clean, title: TOOL_BY_PATH[clean]?.title || 'Betting Tools' };
  }
  if (MODELS_PAGES[clean]) {
    return {
      page: 'models',
      slug: null,
      classicPath: CLASSIC_IFRAME[clean] || clean,
      title: MODELS_PAGES[clean],
    };
  }
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
  if (route.page === 'props') return ['Sports Props | Prediction Lab', 'Explore player props by sport and review recorded results by model and prop type.'];
  if (route.page === 'blog') return ['Prediction Lab Blog | predictionlab.io', 'Original sports roundups, verified final scores, and the latest from Prediction Lab.'];
  if (route.page === 'all') return ['All Sports Predictions Today | Prediction Lab', "Every sport's AI predictions on one page, with each prediction type's recent record."];
  if (route.page === 'models') {
    return [
      `${route.title} | Prediction Lab`,
      `${route.title} on Prediction Lab, with Sports News and the latest from PredictionLab beside the page.`,
    ];
  }
  if (route.page === 'tools') {
    const tool = TOOL_BY_PATH[route.path];
    if (tool) return [tool.metaTitle, tool.metaDescription];
    return [
      'Sports Betting Calculators & Tools | PredictionLab',
      'Free sports betting calculators: odds conversion, parlays, hold, no-vig fair odds, CLV, and Kelly criterion — built for PredictionLab.',
    ];
  }
  return [
    'PredictionLab | Sports forecasting models',
    'PredictionLab statistical probability engine for Straight-Up (SU) forecasts, historical accuracy, and sports research across multiple sports.',
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
  if (route.page === 'sport' && route.slug !== 'golf-picks') prefetchJson(`/api/web/sports/${route.slug}/cards?limit=5`);
  if (route.page === 'all') prefetchJson('/api/web/all-sports');
  if (route.page === 'blog' || route.page === 'models' || route.page === 'tools') prefetchJson('/api/web/blog');
}

function currentAddress() {
  return window.location.pathname + window.location.search;
}

export default function App() {
  const [address, setAddress] = useState(currentAddress);
  const viewerRole = useRef(null);
  const route = routeFromPath(window.location.pathname);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const checkViewer = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch('/api/web/me', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) return;
        const me = await response.json();
        if (typeof me.loggedIn !== 'boolean' || typeof me.premium !== 'boolean') return;
        const next = `${me.loggedIn}:${me.premium}`;
        if (viewerRole.current !== null && viewerRole.current !== next) {
          window.location.reload();
          return;
        }
        viewerRole.current = next;
      } catch {
        // A failed check is not a logout; the server enforces every data request.
      } finally {
        pending = false;
      }
    };
    checkViewer();
    window.addEventListener('focus', checkViewer);
    window.addEventListener('pageshow', checkViewer);
    return () => {
      controller.abort();
      window.removeEventListener('focus', checkViewer);
      window.removeEventListener('pageshow', checkViewer);
    };
  }, [address]);

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

  const wideLayout = route.page === 'models' || route.page === 'blog' || route.page === 'tools';

  return (
    <div className="pl-app">
      <Header />
      {!wideLayout && <LiveStrip />}
      <div className={wideLayout ? 'pl-body pl-body-wide' : 'pl-body'}>
        {!wideLayout && <SportsList activeSlug={route.slug} address={address} />}
        <div className="pl-stage">
          {route.page === 'props' && <PropsPage key={address} />}
          {route.page === 'blog' && <BlogPage />}
          {route.page === 'tools' && <ToolsPage key={address} path={route.path} />}
          {route.page === 'models' && (
            <ModelsPage
              key={route.classicPath || address}
              classicPath={route.classicPath}
              title={route.title}
            />
          )}
          {route.page === 'sport' && <SportPage key={address} slug={route.slug} />}
          {route.page === 'all' && <AllSportsPage key={address} />}
          {route.page === 'home' && <HomePage key={address} />}
        </div>
      </div>
      <Footer />
    </div>
  );
}
