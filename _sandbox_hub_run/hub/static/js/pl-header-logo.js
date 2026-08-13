/**
 * Header logo: short click follows href (home). Press-and-hold downloads full-quality asset.
 */
(function () {
  var HOLD_MS = 650;

  function wireLogo(anchor) {
    var img = anchor.querySelector("img.pl-brand-logo__img, img[data-pl-logo-hq]");
    if (!img) return;
    var hqUrl = img.getAttribute("data-pl-logo-hq") || img.getAttribute("src");
    if (!hqUrl) return;

    var timer = null;
    var longPressed = false;

    function clearTimer() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      anchor.classList.remove("pl-brand-logo--holding");
    }

    function doDownload() {
      fetch(hqUrl, { credentials: "same-origin", cache: "force-cache" })
        .then(function (r) {
          if (!r.ok) throw new Error("fetch failed");
          return r.blob();
        })
        .then(function (blob) {
          var ext = "png";
          try {
            var path = new URL(hqUrl, window.location.origin).pathname.toLowerCase();
            if (path.endsWith(".svg")) ext = "svg";
            else if (path.endsWith(".jpg") || path.endsWith(".jpeg")) ext = "jpg";
            else if (path.endsWith(".webp")) ext = "webp";
          } catch (_e) {}
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = "predictionlab-logo." + ext;
          a.rel = "noopener";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(function () {
            URL.revokeObjectURL(url);
          }, 2500);
        })
        .catch(function () {
          var a = document.createElement("a");
          a.href = hqUrl;
          a.download = "predictionlab-logo.png";
          a.target = "_blank";
          a.rel = "noopener";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        });
    }

    function start(ev) {
      if (ev.type === "mousedown" && typeof ev.button === "number" && ev.button !== 0) return;
      longPressed = false;
      clearTimer();
      anchor.classList.add("pl-brand-logo--holding");
      timer = setTimeout(function () {
        timer = null;
        longPressed = true;
        anchor.classList.remove("pl-brand-logo--holding");
        doDownload();
      }, HOLD_MS);
    }

    function end() {
      clearTimer();
    }

    anchor.addEventListener("mousedown", start);
    anchor.addEventListener("mouseup", end);
    anchor.addEventListener("mouseleave", end);
    anchor.addEventListener("touchstart", start, { passive: true });
    anchor.addEventListener("touchend", end);
    anchor.addEventListener("touchcancel", end);
    anchor.addEventListener(
      "click",
      function (ev) {
        if (longPressed) {
          ev.preventDefault();
          ev.stopPropagation();
          longPressed = false;
        }
      },
      true
    );
  }

  function run() {
    document.querySelectorAll("a.pl-brand-logo").forEach(wireLogo);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
