// Helpers for the classic pages shown inside an iframe (the results column and the chart views on the main page).

// Hides the classic page's own header, footer, premium bar and the duplicate Cards/Chart pills. The site's
// header and footer come from React, and the sport's Cards/Chart buttons come from the panel itself.
export function trimFrame(node) {
  try {
    const doc = node.contentDocument;
    if (!doc) return;
    ['.pl2-header', '.site-directory-footer', '.join-premium-bar', '.share-strip', '.navbar', '#joinPremiumBar', '.pl-view-toggle', '.pl-view-btn']
      .forEach((selector) => {
        doc.querySelectorAll(selector).forEach((el) => { el.style.display = 'none'; });
      });
  } catch (err) {
    // The frame stays as it is if the page cannot be reached.
  }
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
  if (!doc) return;
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
      window.top.location.assign(url.href);
    }
  }, true);
}
