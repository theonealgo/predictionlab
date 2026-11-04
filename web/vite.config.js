import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { SPORT_SLUGS } from './src/data/sportSlugs.js';

// React serves the home page, the sports pages (/nba-picks, /nba-projections) and /player-props. Every other
// path (the API, static files, and the older pages such as The Ladder, Results, Blog, Pricing,
// Login) goes to the preview forwarder on port 5141, so those links open the real pages.
const SPORT_ROUTE = new RegExp(`^/(${SPORT_SLUGS.join('|')})-(picks|projections)/?(\\?.*)?$`);
const PROPS_ROUTE = /^\/(player-props|all-sports-(picks|projections))\/?(\?.*)?$/;
const LOCAL_ASSET = /^\/(@|src\/|node_modules\/|index\.html$)/;
const PREVIEW = process.env.PL_PREVIEW || 'http://127.0.0.1:5141';
// The site treats every localhost request as paid. PL_PREVIEW_HOST sends another host name so the
// test copy applies the real paywall (free / logged-out view).
const PREVIEW_HOST = process.env.PL_PREVIEW_HOST || '';

// Dev server for the React sports front end. /api calls go to the local test copy
// through the logged-out preview forwarder (port 5141), so visitors see the public view.
export default defineConfig(({ command }) => ({
  // The build goes to static/web/, which Flask serves at /static/web/ (see react_shell.py).
  base: command === 'build' ? '/static/web/' : '/',
  build: { outDir: '../static/web', emptyOutDir: true },
  plugins: [react()],
  server: {
    port: Number(process.env.PL_WEB_PORT) || 5173,
    strictPort: true,
    proxy: {
      '^/(?!$)': {
        target: PREVIEW,
        ...(PREVIEW_HOST ? { headers: { host: PREVIEW_HOST } } : {}),
        bypass(req) {
          const url = req.url || '/';
          if (SPORT_ROUTE.test(url) || PROPS_ROUTE.test(url) || LOCAL_ASSET.test(url)) {
            return url;
          }
          return null;
        },
      },
    },
  },
}));
