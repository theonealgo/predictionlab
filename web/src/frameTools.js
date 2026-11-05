// Helpers for the classic pages shown inside an iframe (the results column and the chart views on the main page).

// Hides the classic page's own header, footer, premium bar and the duplicate Cards/Chart pills. The site's
// header and footer come from React, and the sport's Cards/Chart buttons come from the panel itself.
const HIDDEN_IN_FRAME = ['.pl2-header', '.site-directory-footer', '.join-premium-bar', '.share-strip', '.navbar',
  '#joinPremiumBar', '.pl-view-toggle', '.pl-view-btn'];

export function trimFrame(node) {
  try {
    const doc = node.contentDocument;
    if (!doc) return false;
    // A style rule covers parts that arrive later in the page, so this can run before the page finishes.
    if (!doc.getElementById('pl-frame-trim') && (doc.head || doc.body)) {
      const style = doc.createElement('style');
      style.id = 'pl-frame-trim';
      style.textContent = `${HIDDEN_IN_FRAME.join(',')}{display:none!important}`;
      (doc.head || doc.body).appendChild(style);
    }
    return Boolean(doc.getElementById('pl-frame-trim'));
  } catch (err) {
    return false; // the frame stays as it is if the page cannot be reached
  }
}

// Calls ready() as soon as the frame's new page has begun (its <head> exists), long before it finishes.
export function whenFrameParsed(node, url, ready) {
  let timer = null;
  const started = Date.now();
  const check = () => {
    try {
      const doc = node.contentDocument;
      const loc = doc && doc.location;
      const same = loc && loc.href !== 'about:blank' && loc.pathname + loc.search === url;
      if (same && doc.head) {
        ready();
        return;
      }
    } catch (err) {
      ready();
      return;
    }
    if (Date.now() - started < 20000) timer = setTimeout(check, 15);
  };
  timer = setTimeout(check, 15);
  return () => clearTimeout(timer);
}

const RESULTS_PAGE = /-results\/?$/;
const PICKS_PAGE = /-(picks|projections|predictions)\/?$/;

// Links inside a frame: another results page stays in the frame. The sport's predictions link calls
// onPredictions (the cards in the middle column). Any other link opens its page in the main window, so a
// second copy of the site never loads inside the frame.
export function routeLinks(node, picksPath, onPredictions) {
  let doc = null;
  try {
    doc = node.contentDocument;
  } catch (err) {
    return;
  }
  if (!doc || doc.plLinksRouted) return;
  doc.plLinksRouted = true;
  doc.addEventListener('click', (event) => {
    const link = event.target.closest ? event.target.closest('a[href]') : null;
    if (!link) return;
    const url = new URL(link.href);
    if (!/^https?:$/.test(url.protocol)) return;
    if (url.origin !== window.location.origin) {
      event.preventDefault();
      window.open(url.href, '_blank', 'noopener');
      return;
    }
    if (RESULTS_PAGE.test(url.pathname)) return;
    event.preventDefault();
    const picks = url.pathname.replace(PICKS_PAGE, '-picks').replace(/\/$/, '');
    if (picksPath && picks === picksPath && onPredictions) {
      onPredictions();
    } else {
      // A link click on the main page, so the site's pages switch in place (App.jsx) and others load normally.
      const link = window.document.createElement('a');
      link.href = url.href;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
    }
  }, true);
}
