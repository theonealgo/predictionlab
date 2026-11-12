import { useEffect, useRef } from 'react';
import { useChrome } from '../chrome.js';
import { getJson } from '../api.js';

function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// The header's search box. The header arrives as HTML, so its own script does not run here:
// this lists matching players, teams and sports; Enter opens the resolved first match.
function wireSearch(root) {
  const form = root.querySelector('form.pl-search-form');
  const input = form && form.querySelector('input[name="query"]');
  if (!form || !input) return () => {};
  const list = document.createElement('div');
  list.className = 'pl-search-results';
  list.hidden = true;
  form.style.position = 'relative';
  form.appendChild(list);
  let results = [];
  let timer = null;
  let asked = '';
  let completed = '';
  let partial = false;

  const show = () => {
    if (!input.value.trim()) {
      list.hidden = true;
      return;
    }
    list.innerHTML = results.length
      ? results.map((r) => (
        `<a class="pl-search-item" href="${escapeHtml(r.href)}"><span>${escapeHtml(r.label)}</span>`
        + `<span class="pl-search-sport">${escapeHtml(r.sport)}</span></a>`
      )).join('')
      : `<div class="pl-search-empty">${partial ? 'Player directory unavailable. Please retry.' : 'No players or teams found'}</div>`;
    list.hidden = false;
  };

  const lookUp = () => {
    const q = input.value.trim();
    asked = q;
    if (q.length < 2) {
      results = [];
      list.hidden = true;
      return Promise.resolve();
    }
    return getJson(`/api/web/search?q=${encodeURIComponent(q)}`)
      .then((data) => {
        if (asked !== q || input.value.trim() !== q) return;
        results = data.results || [];
        partial = Boolean(data.partial);
        completed = q;
        show();
      })
      .catch(() => {
        if (asked !== q || input.value.trim() !== q) return;
        results = [];
        completed = '';
        partial = true;
        show();
      });
  };

  const onInput = () => {
    clearTimeout(timer);
    list.hidden = true;
    timer = setTimeout(lookUp, 250);
  };
  const onSubmit = (event) => {
    event.preventDefault();
    clearTimeout(timer);
    const go = () => {
      if (completed === input.value.trim() && results[0]) window.location.href = results[0].href;
      else show();
    };
    if (completed === input.value.trim()) go();
    else lookUp().then(go);
  };
  const onDocClick = (event) => {
    if (!form.contains(event.target)) list.hidden = true;
  };
  const onFocus = () => {
    if (completed === input.value.trim() && results.length) show();
  };

  input.setAttribute('autocomplete', 'off');
  input.addEventListener('input', onInput);
  input.addEventListener('focus', onFocus);
  form.addEventListener('submit', onSubmit);
  document.addEventListener('click', onDocClick);
  return () => {
    clearTimeout(timer);
    input.removeEventListener('input', onInput);
    input.removeEventListener('focus', onFocus);
    form.removeEventListener('submit', onSubmit);
    document.removeEventListener('click', onDocClick);
    list.remove();
  };
}

// The site header: the shared template, rendered by the server (templates/partials/research_header.html).
export default function Header() {
  const chrome = useChrome();
  const ref = useRef(null);

  useEffect(() => {
    if (!chrome || !ref.current) return undefined;
    return wireSearch(ref.current);
  }, [chrome]);

  // The header stays at the top while the page scrolls; the side panels sit just under it.
  useEffect(() => {
    if (!chrome || !ref.current || !window.ResizeObserver) return undefined;
    const el = ref.current;
    const setHeight = () => document.documentElement.style.setProperty('--pl-header-h', `${el.offsetHeight}px`);
    setHeight();
    const observer = new ResizeObserver(setHeight);
    observer.observe(el);
    return () => observer.disconnect();
  }, [chrome]);

  if (!chrome) return null;
  return <div className="pl-header-pin" ref={ref} dangerouslySetInnerHTML={{ __html: chrome.header }} />;
}
