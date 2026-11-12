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

    if (!target.closest('header.pl2-header .pl-menu-list, header.pl2-header .pl2-account-menu')) {
      closeAll(null);
    }
  });

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
})();
