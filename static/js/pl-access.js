(function () {
  if (window.plAccessCheckReady) return;
  window.plAccessCheckReady = true;
  var pending = false;
  function check() {
    if (pending || document.visibilityState === 'hidden') return;
    pending = true;
    fetch('/api/web/me', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' }
    }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (viewer) {
      if (viewer && viewer.premium === false) window.location.reload();
    }).catch(function () {
      // A failed request is not evidence of logout.
    }).finally(function () {
      pending = false;
    });
  }
  window.addEventListener('focus', check);
  window.addEventListener('pageshow', check);
  document.addEventListener('visibilitychange', check);
  check();
})();
