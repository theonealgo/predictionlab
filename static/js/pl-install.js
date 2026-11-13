(function () {
  'use strict';
  if (window.self !== window.top) return;
  if (window.plInstallReady) return;
  window.plInstallReady = true;
  var pending = null;
  var button, overlay, previousFocus;
  var mode = window.matchMedia ? window.matchMedia('(display-mode: standalone)') : null;
  function standalone() { return Boolean((mode && mode.matches) || navigator.standalone); }
  function headTag(tag, selector, attributes) {
    if (!document.head || document.head.querySelector(selector)) return;
    var node = document.createElement(tag);
    Object.keys(attributes).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    document.head.appendChild(node);
  }
  headTag('link', 'link[rel="manifest"]', { rel: 'manifest', href: '/static/manifest.webmanifest' });
  headTag('link', 'link[rel="apple-touch-icon"]', { rel: 'apple-touch-icon', href: '/static/apple-touch-icon.png', sizes: '180x180' });
  headTag('meta', 'meta[name="apple-mobile-web-app-capable"]', { name: 'apple-mobile-web-app-capable', content: 'yes' });
  headTag('meta', 'meta[name="apple-mobile-web-app-title"]', { name: 'apple-mobile-web-app-title', content: 'PredictionLab' });
  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    pending = event;
  });
  function close() {
    if (overlay) overlay.hidden = true;
    if (previousFocus && previousFocus.isConnected) previousFocus.focus();
  }
  function instructions() {
    var ua = navigator.userAgent;
    var ipad = /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (/iPhone|iPod/.test(ua) || ipad) {
      return 'In Safari, tap Share, then Add to Home Screen. Confirm the name and tap Add.';
    }
    if (/Android/.test(ua)) {
      return 'Open your browser menu (⋮) and choose Install app or Add to Home screen. Follow the confirmation.';
    }
    if (/Macintosh|Mac OS X/.test(ua) && /Safari/.test(ua) && !/Chrome|Chromium|Edg|OPR/.test(ua)) {
      return 'In supported versions of Safari, choose File → Add to Dock, then confirm Add. If unavailable, bookmark this page or use Chrome or Edge.';
    }
    if (/Firefox/.test(ua)) {
      return 'Desktop Firefox does not include app installation. Open this site in Chrome, Edge, or Safari on macOS to install it, or bookmark this page.';
    }
    return 'Use the app-install icon in your browser’s address bar, or choose Install app / Install page as app from the browser menu. In Edge, look under Apps.';
  }
  function guide() {
    previousFocus = document.activeElement;
    overlay.querySelector('.pl-install-instructions').textContent = instructions();
    overlay.hidden = false;
    overlay.querySelector('.pl-install-close').focus();
  }
  function update() {
    if (button) button.hidden = standalone();
    if (standalone()) close();
  }
  function boot() {
    if (!document.body || button) return;
    var style = document.createElement('style');
    style.textContent =
      '.pl-install-button{position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));z-index:9500;display:inline-flex;align-items:center;gap:7px;border:1px solid #075985;border-radius:24px;padding:11px 15px;background:#075985;color:#fff;font:600 13px/1.3 system-ui,sans-serif;box-shadow:0 3px 14px #0002;cursor:pointer}' +
      '.pl-install-button[hidden],.pl-install-overlay[hidden]{display:none!important}.pl-install-button:focus-visible,.pl-install-close:focus-visible{outline:3px solid #38bdf8;outline-offset:3px}' +
      '.pl-install-overlay{position:fixed;inset:0;z-index:12000;background:#0007;display:grid;place-items:center;padding:20px}.pl-install-dialog{box-sizing:border-box;width:min(420px,100%);border-radius:14px;padding:24px;background:#fff;color:#111827;box-shadow:0 12px 60px #0004;font:15px/1.6 system-ui,sans-serif}' +
      '.pl-install-dialog h2{font-size:20px;margin:0 0 12px}.pl-install-dialog p{margin:0 0 20px}.pl-install-close{background:#075985;color:white;border:0;border-radius:8px;padding:10px 18px;font:600 14px system-ui,sans-serif;cursor:pointer}';
    document.head.appendChild(style);
    button = document.createElement('button');
    button.type = 'button';
    button.className = 'pl-install-button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-label', 'Add PredictionLab shortcut');
    button.innerHTML = '<span aria-hidden="true">+</span><span>Add PredictionLab</span>';
    overlay = document.createElement('div');
    overlay.className = 'pl-install-overlay';
    overlay.hidden = true;
    overlay.innerHTML = '<section class="pl-install-dialog" role="dialog" aria-modal="true" aria-labelledby="plInstallTitle"><h2 id="plInstallTitle">Add a PredictionLab shortcut</h2><p>Keep this website handy on your home screen, desktop, or dock.</p><p class="pl-install-instructions"></p><button type="button" class="pl-install-close">Close</button></section>';
    document.body.appendChild(button);
    document.body.appendChild(overlay);
    button.addEventListener('click', function () {
      if (!pending) { guide(); return; }
      var request = pending;
      pending = null;
      button.disabled = true;
      var attempt;
      try { attempt = request.prompt(); }
      catch (error) { button.disabled = false; guide(); return; }
      Promise.resolve(attempt)
        .then(function () { return request.userChoice; })
        .catch(guide)
        .finally(function () { button.disabled = false; });
    });
    overlay.querySelector('.pl-install-close').addEventListener('click', close);
    overlay.addEventListener('click', function (event) { if (event.target === overlay) close(); });
    document.addEventListener('keydown', function (event) {
      if (!overlay || overlay.hidden) return;
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'Tab') {
        event.preventDefault();
        overlay.querySelector('.pl-install-close').focus();
      }
    });
    window.addEventListener('appinstalled', function () {
      pending = null;
      button.hidden = true;
      close();
    });
    if (mode && mode.addEventListener) mode.addEventListener('change', update);
    else if (mode && mode.addListener) mode.addListener(update);
    update();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
})();
