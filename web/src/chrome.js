import { useEffect, useState } from 'react';
import { getJson } from './api.js';

// The shared header and footer, rendered by the server from templates/partials, so this front end
// shows the same chrome as every other page. One request, shared by the header and the footer.
let request = null;

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
  const [chrome, setChrome] = useState(null);

  useEffect(() => {
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
