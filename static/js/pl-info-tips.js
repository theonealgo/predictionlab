/* Shared click/hover/focus tooltips for ⓘ / info buttons. */
(function () {
  if (window.__plInfoTipsBound) return;
  window.__plInfoTipsBound = true;

  var TIP_SEL =
    ".pl-info-btn, .pct-info-btn, .rl-conf-info, .h2h-info-btn, .asr-info, [data-pl-info-tip]";
  var RL_FALLBACK =
    "Run Line Confidence is how strongly our model favors its run-line side, from 0 to 100. It is the confidence behind that run-line lean — not a moneyline or totals pick.";
  var pinned = null;
  var ignoreMoveUntil = 0;

  function tipEl() {
    var el = document.getElementById("rlConfLiveTip");
    if (el) return el;
    el = document.createElement("div");
    el.id = "rlConfLiveTip";
    el.className = "rl-conf-live-tip";
    el.setAttribute("role", "tooltip");
    el.hidden = true;
    document.body.appendChild(el);
    return el;
  }

  function tipText(btn) {
    if (!btn) return "";
    var text = (
      btn.getAttribute("data-tip") ||
      btn.getAttribute("data-pl-info-tip") ||
      btn.getAttribute("aria-label") ||
      btn.getAttribute("title") ||
      ""
    ).trim();
    if (!text && btn.classList && btn.classList.contains("rl-conf-info")) {
      text = RL_FALLBACK;
    }
    return text;
  }

  function hideTip() {
    var t = document.getElementById("rlConfLiveTip");
    if (t) {
      t.hidden = true;
      t.classList.remove("open");
      t.textContent = "";
    }
    document.querySelectorAll(TIP_SEL + '[aria-expanded="true"]').forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.removeAttribute("aria-describedby");
    });
    pinned = null;
  }

  function placeTip(btn) {
    var text = tipText(btn);
    if (!text) return;
    var t = tipEl();
    t.textContent = text;
    t.hidden = false;
    t.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    btn.setAttribute("aria-describedby", "rlConfLiveTip");
    var r = btn.getBoundingClientRect();
    var w = Math.min(280, Math.max(200, window.innerWidth - 16));
    t.style.maxWidth = w + "px";
    t.style.left = "8px";
    t.style.top = "8px";
    var tw = t.getBoundingClientRect().width;
    var th = t.getBoundingClientRect().height;
    var left = r.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    var top = r.bottom + 8;
    if (top + th > window.innerHeight - 8) top = Math.max(8, r.top - th - 8);
    t.style.left = left + "px";
    t.style.top = top + "px";
    ignoreMoveUntil = Date.now() + 450;
  }

  function fromEvent(e) {
    return e.target && e.target.closest ? e.target.closest(TIP_SEL) : null;
  }

  document.addEventListener("click", function (e) {
    var btn = fromEvent(e);
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      if (pinned === btn) {
        hideTip();
        return;
      }
      hideTip();
      pinned = btn;
      placeTip(btn);
      return;
    }
    if (e.target && e.target.id === "rlConfLiveTip") return;
    hideTip();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hideTip();
  });
  document.addEventListener("mouseover", function (e) {
    var btn = fromEvent(e);
    if (!btn || pinned) return;
    placeTip(btn);
  });
  document.addEventListener("mouseout", function (e) {
    var btn = fromEvent(e);
    if (!btn || pinned) return;
    var rel = e.relatedTarget;
    if (rel && (btn.contains(rel) || rel.id === "rlConfLiveTip")) return;
    if (document.activeElement === btn) return;
    hideTip();
  });
  document.addEventListener("focusin", function (e) {
    if (!e.target || !e.target.closest || !e.target.closest(TIP_SEL)) return;
    if (pinned && pinned !== e.target) return;
    placeTip(e.target.closest(TIP_SEL));
  });
  document.addEventListener("focusout", function (e) {
    if (pinned) return;
    if (e.target && e.target.closest && e.target.closest(TIP_SEL)) hideTip();
  });
  window.addEventListener(
    "scroll",
    function () {
      if (Date.now() < ignoreMoveUntil) return;
      if (pinned) hideTip();
    },
    true
  );
  window.addEventListener("resize", function () {
    if (Date.now() < ignoreMoveUntil) return;
    hideTip();
  });
})();
