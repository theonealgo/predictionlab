/* Try a Week FREE modal: intercept weekly CTAs, copy PLAY26, trap focus. */
(function () {
  if (window.__plTryWeekModalBound) return;
  window.__plTryWeekModalBound = true;

  var CODE = "PLAY26";
  var DEFAULT_HREF = "/checkout/weekly";
  var lastFocus = null;
  var copyTimer = null;

  function modalRoot() {
    return document.getElementById("plTryWeekModal");
  }

  function dialogEl() {
    var root = modalRoot();
    return root ? root.querySelector(".pl-try-week-dialog") : null;
  }

  function checkoutLink() {
    return document.getElementById("plTryWeekCheckout");
  }

  function isOpen() {
    var root = modalRoot();
    return !!(root && !root.hasAttribute("hidden"));
  }

  function focusable(root) {
    if (!root) return [];
    var nodes = root.querySelectorAll(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
    );
    return Array.prototype.filter.call(nodes, function (el) {
      if (el.hasAttribute("disabled") || el.getAttribute("aria-hidden") === "true") return false;
      return el.getClientRects().length > 0;
    });
  }

  function openModal(href, trigger) {
    var root = modalRoot();
    var dialog = dialogEl();
    var link = checkoutLink();
    if (!root || !dialog) return;
    lastFocus = trigger || document.activeElement;
    if (link) link.setAttribute("href", href || DEFAULT_HREF);
    if (typeof window.tvClose === "function") {
      try { window.tvClose(); } catch (err) {}
    }
    root.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    var first = focusable(dialog)[0] || dialog;
    window.setTimeout(function () { first.focus(); }, 0);
  }

  function closeModal() {
    var root = modalRoot();
    if (!root || root.hasAttribute("hidden")) return;
    root.setAttribute("hidden", "");
    document.body.style.overflow = "";
    var restore = lastFocus;
    lastFocus = null;
    if (restore && typeof restore.focus === "function") {
      window.setTimeout(function () { restore.focus(); }, 0);
    }
  }

  function copyCode() {
    var btn = document.getElementById("plTryWeekCopy");
    function showCopied() {
      if (!btn) return;
      btn.textContent = "Copied";
      btn.classList.add("is-copied");
      if (copyTimer) window.clearTimeout(copyTimer);
      copyTimer = window.setTimeout(function () {
        btn.textContent = "Copy Code";
        btn.classList.remove("is-copied");
      }, 1600);
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = CODE;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (err) {}
      document.body.removeChild(ta);
      showCopied();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(CODE).then(showCopied).catch(fallback);
    } else {
      fallback();
    }
  }

  document.addEventListener("click", function (e) {
    var closer = e.target.closest ? e.target.closest("[data-pl-try-week-close]") : null;
    if (closer && isOpen()) {
      e.preventDefault();
      closeModal();
      return;
    }
    if (e.target.closest && e.target.closest("#plTryWeekCopy")) {
      e.preventDefault();
      copyCode();
      return;
    }
    var cta = e.target.closest ? e.target.closest("a.js-try-week-cta") : null;
    if (!cta || !modalRoot()) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    openModal(cta.getAttribute("href") || DEFAULT_HREF, cta);
  });

  document.addEventListener(
    "keydown",
    function (e) {
      if (!isOpen()) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        closeModal();
        return;
      }
      if (e.key !== "Tab") return;
      var dialog = dialogEl();
      var items = focusable(dialog);
      if (!items.length) {
        e.preventDefault();
        if (dialog) dialog.focus();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      var active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !dialog.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last) {
        e.preventDefault();
        first.focus();
      }
    },
    true
  );
})();
