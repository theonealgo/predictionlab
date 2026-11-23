/* One date picker on every sport picks / results page: arrows, date bubbles, and a dropdown. */
(function () {
  var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var ISO = /^\s*(\d{4})-(\d{2})-(\d{2})\s*$/;

  function todayIso() {
    try {
      return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
    } catch (e) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function label(iso) {
    var m = ISO.exec(iso || '');
    if (!m) return iso;
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    var txt = DAYS[d.getUTCDay()] + ', ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCDate();
    return iso === todayIso() ? txt + ' (Today)' : txt;
  }

  function bubbleDate(b) {
    var d = b.getAttribute('data-date') || b.getAttribute('title') || '';
    if (ISO.test(d)) return d.trim();
    var t = (b.textContent || '').trim();
    return ISO.test(t) ? t : '';
  }

  function topCards() {
    var scope = document.querySelector('.date-section') || document;
    return Array.prototype.slice.call(scope.querySelectorAll('.game-card-stack, .game-card'))
      .filter(function (c) { return !c.parentElement.closest('.game-card-stack'); });
  }

  function filterByText(d) {
    var cards = topCards();
    var hit = cards.filter(function (c) { return (c.textContent || '').indexOf(d) !== -1; });
    if (!hit.length) return false;
    cards.forEach(function (c) {
      if (hit.indexOf(c) === -1) c.style.setProperty('display', 'none', 'important');
      else c.style.removeProperty('display');
    });
    var head = document.querySelector('.date-section .date-header');
    if (head && ISO.test((head.textContent || '').replace(/[^\d-]/g, ''))) head.textContent = d;
    return true;
  }

  function go(nav, sel, d) {
    if (!d) return;
    sel.value = d;
    nav.querySelectorAll('.date-bubble, .date-btn').forEach(function (b) {
      b.classList.toggle('active', bubbleDate(b) === d);
    });
    var load = document.getElementById('plLoadDate');
    if (load) { load.click(); return; }
    var sec = document.getElementById('date-' + d);
    if (sec) {
      if (typeof window.showDate === 'function') {
        window.showDate(d);
        if (typeof window.renderDateBubbles === 'function') window.renderDateBubbles();
      } else {
        document.querySelectorAll('.date-section').forEach(function (s) {
          s.classList.toggle('visible', s === sec);
        });
      }
      return;
    }
    var b = Array.prototype.slice.call(nav.querySelectorAll('.date-bubble, .date-btn'))
      .filter(function (x) { return bubbleDate(x) === d; })[0];
    if (b && (b.onclick || b.getAttribute('onclick'))) { b.click(); return; }
    filterByText(d);
  }

  function ensureSelect(nav) {
    var sel = nav.querySelector('select#datePicker');
    if (!sel) {
      sel = document.getElementById('datePicker');
      if (!sel || sel.tagName !== 'SELECT') sel = null;
    }
    if (sel && sel !== nav.lastElementChild) nav.appendChild(sel);
    var dates = [];
    nav.querySelectorAll('.date-bubble, .date-btn').forEach(function (b) {
      var d = bubbleDate(b);
      if (d && dates.indexOf(d) === -1) dates.push(d);
    });
    if (!sel) {
      if (!dates.length) return null;
      sel = document.createElement('select');
      sel.id = 'datePicker';
      sel.setAttribute('aria-label', 'Jump to date');
      dates.sort().forEach(function (d) {
        var o = document.createElement('option');
        o.value = d;
        sel.appendChild(o);
      });
      nav.appendChild(sel);
      sel.addEventListener('change', function () { go(nav, sel, sel.value); });
      sel.__plBuilt = true;
    }
    if (!sel.getAttribute('aria-label')) sel.setAttribute('aria-label', 'Jump to date');
    relabel(sel);
    if (!sel.__plWatch && window.MutationObserver) {
      sel.__plWatch = new MutationObserver(function () { relabel(sel); });
      sel.__plWatch.observe(sel, { childList: true });
    }
    return sel;
  }

  function relabel(sel) {
    Array.prototype.forEach.call(sel.options, function (o) {
      var t = (o.text || '').trim();
      if (ISO.test(o.value) && (!t || t.indexOf(o.value) === 0)) {
        o.text = label(o.value) + t.slice(o.value.length).replace(/\s*\(Today\)\s*$/, '');
      }
    });
  }

  function ensureArrows(nav, sel) {
    if (nav.querySelector('.nav-arrow')) return;
    function arrow(txt, step) {
      var a = document.createElement('div');
      a.className = 'nav-arrow';
      a.setAttribute('role', 'button');
      a.setAttribute('aria-label', step < 0 ? 'Previous date' : 'Next date');
      a.textContent = txt;
      a.addEventListener('click', function () {
        var i = sel.selectedIndex + step;
        if (i < 0 || i >= sel.options.length) return;
        sel.selectedIndex = i;
        go(nav, sel, sel.value);
      });
      return a;
    }
    nav.insertBefore(arrow('\u2039', -1), nav.firstChild);
    var next = arrow('\u203A', 1);
    nav.insertBefore(next, sel);
  }

  function relabelBubbles(nav) {
    nav.querySelectorAll('.date-bubble, .date-btn').forEach(function (b) {
      var d = bubbleDate(b);
      if (d && ISO.test((b.textContent || '').trim())) b.textContent = label(d).replace(' (Today)', '');
    });
  }

  function wrapButtons(nav) {
    if (!nav.querySelector('.date-bubbles')) {
      var btns = nav.querySelectorAll('.date-btn');
      if (btns.length) {
        var box = document.createElement('div');
        box.className = 'date-bubbles';
        nav.insertBefore(box, btns[0]);
        btns.forEach(function (b) { box.appendChild(b); });
      }
    }
    relabelBubbles(nav);
    var strip = nav.querySelector('.date-bubbles');
    if (strip && !strip.__plWatch && window.MutationObserver) {
      strip.__plWatch = new MutationObserver(function () { relabelBubbles(nav); });
      strip.__plWatch.observe(strip, { childList: true });
    }
  }

  function unify(final) {
    var navs = Array.prototype.slice.call(document.querySelectorAll('.date-nav'));
    navs.forEach(function (nav) {
      if (nav.__plDateNav) return;
      wrapButtons(nav);
      var sel = ensureSelect(nav);
      if (!sel || !sel.options.length) {
        if (final && !nav.querySelector('.date-bubble, .date-btn')) nav.classList.add('pl-dp-empty');
        return;
      }
      nav.classList.remove('pl-dp-empty');
      nav.__plDateNav = true;
      sel.classList.add('pl-dp-on');
      sel.style.display = '';
      var load = document.getElementById('plLoadDate');
      if (load && !sel.__plAuto) {
        sel.__plAuto = true;
        sel.addEventListener('change', function () { load.click(); });
      }
      nav.querySelectorAll('.date-btn').forEach(function (b) {
        if (b.onclick || b.getAttribute('onclick')) return;
        b.addEventListener('click', function () { go(nav, sel, bubbleDate(b)); });
      });
      ensureArrows(nav, sel);
      if (sel.__plBuilt) {
        var last = sel.options[sel.options.length - 1].value;
        go(nav, sel, last);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { unify(false); });
  } else {
    unify(false);
  }
  window.addEventListener('load', function () { setTimeout(function () { unify(true); }, 300); });
})();
