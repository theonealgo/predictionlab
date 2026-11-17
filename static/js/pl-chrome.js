// Shared site header behaviour, for every page: the nav dropdowns, the account menu and the all-links
// drawer. Event delegation, so one script serves the server-rendered header and the React front end,
// which loads the same header after the page starts.
(function () {
  if (window.plChromeReady) return;
  window.plChromeReady = true;

  function setDropdown(menu, open) {
    var list = menu.querySelector('.pl-menu-list');
    var button = menu.querySelector(':scope > button');
    if (list) {
      list.hidden = !open;
      ['position', 'left', 'right', 'top', 'minWidth', 'maxHeight'].forEach(function (name) {
        list.style[name] = '';
      });
      if (open && button && window.innerWidth <= 900) {
        var top = Math.round(button.getBoundingClientRect().bottom + 8);
        list.style.position = 'fixed';
        list.style.left = '12px';
        list.style.right = '12px';
        list.style.top = top + 'px';
        list.style.minWidth = '0';
        list.style.maxHeight = Math.max(80, window.innerHeight - top - 16) + 'px';
      }
    }
    if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function setAccount(open) {
    var menu = document.getElementById('pl2AccountMenu');
    var button = document.querySelector('header.pl2-header .pl2-account-button');
    if (menu) menu.hidden = !open;
    if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function setDrawer(open) {
    var drawer = document.getElementById('plDrawer');
    var button = document.querySelector('header.pl2-header .pl-hamburger');
    if (drawer) drawer.hidden = !open;
    if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function closeAll(keep) {
    document.querySelectorAll('header.pl2-header .pl-menu').forEach(function (menu) {
      if (menu !== keep) setDropdown(menu, false);
    });
    setAccount(false);
  }

  document.addEventListener('click', function (event) {
    var target = event.target;

    var dropdownButton = target.closest('header.pl2-header .pl-menu > button');
    if (dropdownButton) {
      var menu = dropdownButton.parentElement;
      var dropdownOpening = menu.querySelector('.pl-menu-list').hidden;
      closeAll(menu);
      setDropdown(menu, dropdownOpening);
      return;
    }

    if (target.closest('header.pl2-header .pl2-account-button')) {
      var accountMenu = document.getElementById('pl2AccountMenu');
      var accountOpening = accountMenu ? accountMenu.hidden : false;
      closeAll(null);
      setAccount(accountOpening);
      return;
    }

    if (target.closest('header.pl2-header [data-pl-drawer-open]')) {
      setDrawer(true);
      return;
    }
    if (target.closest('header.pl2-header [data-pl-drawer-close]')) {
      setDrawer(false);
      return;
    }

    // SPA: a click inside the open list (especially a link) must close it — the document
    // does not unload the way a classic full-page navigation does.
    if (target.closest('header.pl2-header .pl-menu-list a, header.pl2-header .pl2-account-menu a')) {
      closeAll(null);
      setDrawer(false);
      return;
    }

    if (!target.closest('header.pl2-header .pl-menu-list, header.pl2-header .pl2-account-menu')) {
      closeAll(null);
    }
  });

  // React in-place navigations never unload the document; close menus when the URL changes.
  window.addEventListener('popstate', function () {
    closeAll(null);
    setDrawer(false);
  });
  try {
    var _push = history.pushState;
    history.pushState = function () {
      var out = _push.apply(this, arguments);
      try {
        closeAll(null);
        setDrawer(false);
      } catch (ignore) { /* never break SPA navigation */ }
      return out;
    };
  } catch (err) { /* leave menus alone if history cannot be wrapped */ }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeAll(null);
      setDrawer(false);
    }
  });

  var menuWidth = window.innerWidth;
  window.addEventListener('resize', function () {
    if (window.innerWidth !== menuWidth) {
      closeAll(null);
      menuWidth = window.innerWidth;
    }
  });
  // The install action is shared by desktop, mobile, and the React shell.
  var installer = document.createElement('script');
  installer.src = '/static/js/pl-install.js';
  installer.defer = true;
  document.head.appendChild(installer);

  // Left-corner jump control for long picks/results pages.
  // Only on the top window — classic pages inside React iframes also load this
  // script, and a second fixed control stacks on top of the shell's one.
  function ensurePageJump() {
    try {
      if (window.top !== window) return;
    } catch (err) {
      return;
    }
    var extras = document.querySelectorAll('.pl-page-jump');
    if (extras.length > 1) {
      for (var i = 1; i < extras.length; i += 1) extras[i].remove();
    }
    if (document.getElementById('plPageJump') || document.querySelector('.pl-page-jump')) return;
    var wrap = document.createElement('div');
    wrap.id = 'plPageJump';
    wrap.className = 'pl-page-jump';
    wrap.setAttribute('role', 'navigation');
    wrap.setAttribute('aria-label', 'Jump to top, middle, or bottom');
    wrap.innerHTML =
      '<button type="button" data-pl-jump="top" aria-label="Jump to top" title="Top">▲</button>' +
      '<button type="button" data-pl-jump="middle" aria-label="Jump to middle" title="Middle">◆</button>' +
      '<button type="button" data-pl-jump="bottom" aria-label="Jump to bottom" title="Bottom">▼</button>';
    document.body.appendChild(wrap);
  }

  document.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-pl-jump]');
    if (!btn) return;
    var where = btn.getAttribute('data-pl-jump');
    var docH = Math.max(
      document.documentElement.scrollHeight,
      document.body ? document.body.scrollHeight : 0
    );
    var viewH = window.innerHeight || document.documentElement.clientHeight || 0;
    var top = 0;
    if (where === 'bottom') {
      top = docH;
    } else if (where === 'middle') {
      top = Math.max(0, Math.round((docH - viewH) / 2));
    }
    try {
      window.scrollTo({ top: top, left: 0, behavior: 'smooth' });
    } catch (err) {
      window.scrollTo(0, top);
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensurePageJump);
  } else {
    ensurePageJump();
  }
})();
