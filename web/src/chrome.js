import { useEffect, useState } from 'react';
import { getJson } from './api.js';

// The shared header and footer, rendered by the server from templates/partials, so this front end
// shows the same chrome as every other page. One request, shared by the header and the footer.
// The page arrives with this visitor's chrome already in it (react_shell.py), so the header and its
// sign-in / sign-out links never wait on a busy server; the request is only the fallback.
const served = typeof window !== 'undefined' && window.__PL_CHROME__ && window.__PL_CHROME__.header
  ? window.__PL_CHROME__ : null;
let request = served ? Promise.resolve(served) : null;

function loadChrome() {
  if (!request) {
    request = getJson('/api/web/chrome').catch((error) => {
      request = null;
      throw error;
    });
  }
  return request;
}

export function useChrome() {
  const [chrome, setChrome] = useState(served);

  useEffect(() => {
    if (served) return undefined;
    let alive = true;
    loadChrome()
      .then((data) => {
        if (alive) setChrome(data);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return chrome;
}
