// Small fetch helper for the JSON API served by the Flask app (see web_api.py).
//
// Answers are kept in memory for this tab only, so moving between pages reuses them. Cards differ by
// viewer (guest / free / member) and signing in or out reloads the page, which empties this memory, so
// one viewer's answers are never shown to another.
const memory = new Map(); // path -> { ts, data }
const inflight = new Map(); // path -> Promise
const FRESH_MS = 60 * 1000;
const KEEP_MS = 30 * 60 * 1000;

function remember(path, data) {
  // A "still building" answer is not kept: the next ask must reach the server.
  if (data && typeof data === 'object' && (data.building || data.refreshing)) return;
  memory.set(path, { ts: Date.now(), data });
}

// The kept answer for a path (fresh or not, up to 30 minutes old), or null. Used to paint at once.
export function peekJson(path) {
  const hit = memory.get(path);
  return hit && Date.now() - hit.ts < KEEP_MS ? hit.data : null;
}

export function getJson(path, { maxAge = FRESH_MS } = {}) {
  const hit = memory.get(path);
  if (hit && maxAge > 0 && Date.now() - hit.ts < maxAge) return Promise.resolve(hit.data);
  if (inflight.has(path)) return inflight.get(path);
  const request = fetch(path, { headers: { Accept: 'application/json' } })
    .then((res) => {
      if (!res.ok) throw new Error(`${path} returned ${res.status}`);
      return res.json();
    })
    .then((data) => {
      remember(path, data);
      return data;
    })
    .finally(() => inflight.delete(path));
  inflight.set(path, request);
  return request;
}

// Start loading a path in the background (a link the visitor is about to open).
export function prefetchJson(path) {
  getJson(path).catch(() => {});
}
