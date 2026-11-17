// Checks real hit targets and clicks; valid hrefs alone do not prove navigation works.
const fs = require('fs');
const path = require('path');
const { chromium } = require('../.checkpoints/browser/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.PL_NAV_BASE || 'http://127.0.0.1:5122';
const output = path.join(root, '.checkpoints/navigation-audit');
const pages = [
  '/', '/daily-report', '/all-sports-results', '/blog', '/plans',
  '/login', '/signup', '/forgot-password', '/account', '/privacy', '/terms',
  '/contact', '/affiliate', '/tutorial', '/results/downloads', '/historic-results',
  '/sports-props', '/player-props', '/performance', '/ladder',
  ...['nba', 'nhl', 'nfl', 'ncaaf', 'ncaab', 'ncaaw', 'mlb', 'wnba', 'cfl', 'soccer', 'tennis', 'ufc', 'golf']
    .flatMap(sport => [`/${sport}-predictions`, `/${sport}-results`]),
];

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const results = [];
  try {
    // Reproduce the exact regression against the shared stylesheet in isolation.
    const fixture = await browser.newPage();
    await fixture.setContent(`<style>body{margin:0}body::before{content:'';position:fixed;inset:0;z-index:0}
      ${fs.readFileSync(path.join(root, 'static/css/pl-chrome.css'), 'utf8')}</style>
      <header class="pl2-header"><div class="pl-top"><a class="pl-logo" href="#home">PredictionLab</a></div></header>`);
    await fixture.locator('a.pl-logo').click({ timeout: 3000 });
    if (!fixture.url().endsWith('#home')) throw Error('Decorative overlay still blocks the logo');
    await fixture.close();
    for (const width of [390, 1440]) {
      for (const url of pages) {
        const page = await browser.newPage({ viewport: { width, height: 1000 } });
        // Navigation is local; external ads/images must not stall DOM-ready checks.
        await page.route('**/*', route => {
          const url = new URL(route.request().url());
          return url.hostname === new URL(base).hostname ? route.continue() : route.abort();
        });
        const row = { url, width, status: null, controls: [], failures: [] };
        try {
          const response = await page.goto(base + url, { waitUntil: 'domcontentloaded', timeout: 30000 });
          row.status = response.status();
          if (row.status >= 500 || row.status === 404) row.failures.push(`HTTP ${row.status}`);
          const header = page.locator('header.pl2-header').first();
          await header.waitFor({ timeout: 5000 });
          const inspect = async (scope) => {
            const findings = await scope.locator('a').evaluateAll(anchors => anchors.map(a => {
                if (a.closest('details:not([open])')) return null;
                const r = a.getBoundingClientRect();
                if (!r.width || !r.height || getComputedStyle(a).visibility !== 'visible') return null;
                let left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
                let top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
                for (let n = a.parentElement; n && n !== document.body; n = n.parentElement) {
                  const s = getComputedStyle(n), box = n.getBoundingClientRect();
                  if (/(auto|scroll|hidden|clip)/.test(s.overflowX)) {
                    left = Math.max(left, box.left); right = Math.min(right, box.right);
                  }
                  if (/(auto|scroll|hidden|clip)/.test(s.overflowY)) {
                    top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
                  }
                }
                if (right <= left || bottom <= top) return null;
                const x = (left + right) / 2, y = (top + bottom) / 2;
                const target = document.elementFromPoint(x, y);
                return {
                  text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                  href: a.getAttribute('href'),
                  hit: target === a || a.contains(target),
                  blockedBy: target?.tagName + '.' + target?.className,
                };
              }).filter(Boolean));
            for (const finding of findings) {
                row.controls.push(finding);
                if (!finding.href || finding.href === '#') row.failures.push('Missing destination: ' + finding.text);
                if (!finding.hit) row.failures.push('Blocked link: ' + finding.text + ' by ' + finding.blockedBy);
            }
          };
          await inspect(header);
          const menus = header.locator('.pl-menu > button');
          for (let i = 0; i < await menus.count(); i++) {
            await menus.nth(i).click({ timeout: 3000 });
            if (await menus.nth(i).getAttribute('aria-expanded') !== 'true')
              row.failures.push('Menu did not open: ' + await menus.nth(i).innerText());
            const list = header.locator('.pl-menu-list').nth(i);
            await inspect(list);
            await page.keyboard.press('Escape');
          }
          const drawer = header.locator('[data-pl-drawer-open]');
          if (await drawer.isVisible()) {
            await drawer.click({ timeout: 3000 });
            const panel = page.locator('#plDrawer');
            if (!await panel.isVisible()) row.failures.push('Drawer did not open');
            else {
              await inspect(panel);
              const sections = panel.locator('details');
              for (let i = 0; i < await sections.count(); i++) {
                const summary = sections.nth(i).locator('summary').first();
                await summary.scrollIntoViewIfNeeded();
                await summary.click({ timeout: 3000 });
                await inspect(sections.nth(i));
                await summary.click({ timeout: 3000 });
              }
            }
            await page.keyboard.press('Escape');
          }
          // A real navigation, not force-click: any covering layer makes this fail.
          await header.locator('a[href="/"]').first().click({ timeout: 3000 });
          await page.waitForURL(base + '/', { timeout: 12000, waitUntil: 'domcontentloaded' });
        } catch (err) {
          row.failures.push(String(err.message).slice(0, 500));
        } finally {
          results.push(row);
          await page.close();
        }
        console.log(`${row.failures.length ? 'FAIL' : 'PASS'} ${width} ${url} (${row.controls.length} visible link targets)`);
        fs.writeFileSync(path.join(output, 'browser-navigation.json'), JSON.stringify(results, null, 2));
      }
    }
  } finally {
    await browser.close();
  }
  const failed = results.filter(row => row.failures.length);
  console.log(JSON.stringify({ pages: results.length, failures: failed.length,
    checkedLinkTargets: results.reduce((n, row) => n + row.controls.length, 0) }));
  if (failed.length) process.exitCode = 1;
})().catch(err => { console.error(err); process.exitCode = 1; });
