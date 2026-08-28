/**
 * Sandbox hub picks Chart — Cards|Chart + Moneyline / Spread / Totals tabs.
 * Values come from card data-* attrs + face / View Details (same as card).
 *
 * Config (optional): window.PICKS_CHART = {
 *   sport: "mlb"|"soccer"|...,
 *   markets: ["moneyline","spread","totals"],  // omit spread/totals for ML-only
 *   showRunLineConfidence: true|false,         // default true for mlb
 *   showBooks: true|false                      // omit to auto-detect from cards
 * }
 */
var PICKS_CHART_CFG = (function () {
  var c = window.PICKS_CHART || {};
  var markets = Array.isArray(c.markets) && c.markets.length
    ? c.markets.slice()
    : ["moneyline", "spread", "totals"];
  var sport = String(c.sport || "mlb").toLowerCase();
  var showRl =
    typeof c.showRunLineConfidence === "boolean"
      ? c.showRunLineConfidence
      : sport === "mlb";
  var showBooks = typeof c.showBooks === "boolean" ? c.showBooks : null;
  return {
    sport: sport,
    markets: markets,
    showRunLineConfidence: showRl,
    showBooks: showBooks,
  };
})();
var PICKS_CHART_MARKET = "moneyline";
/** @deprecated alias — older MLB inject / inline callers */
var MLB_PICKS_CHART_MARKET = PICKS_CHART_MARKET;
var PICKS_CHART_SHOW_BOOKS = true;
var PICKS_CHART_MODEL_ATTRS = [
  ["data-m-grinder2", "Grinder2"],
  ["data-m-takedown", "Takedown"],
  ["data-m-edge", "Edge"],
  ["data-m-xsharp", "XSharp"],
  ["data-m-efficiency", "Efficiency"],
  ["data-m-consensus", "Sharp Cons."],
];
var MLB_PICKS_MODEL_ATTRS = PICKS_CHART_MODEL_ATTRS;

function _pcEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}
function _mlbPcEsc(s) {
  return _pcEsc(s);
}
function _pcConfClass(pct) {
  return pct >= 65 ? "conf-strong" : pct >= 55 ? "conf-mod" : "conf-weak";
}
function _mlbPcConfClass(pct) {
  return _pcConfClass(pct);
}
function _pcRlConfClass(n) {
  if (n == null || isNaN(n)) return "";
  return n >= 60 ? "rl-conf-strong" : n >= 45 ? "rl-conf-mod" : "rl-conf-weak";
}
function _mlbPcRlConfClass(n) {
  return _pcRlConfClass(n);
}
function _pcTextAttr(st, attr) {
  const v = (st.getAttribute(attr) || "").trim();
  return v || "—";
}
function _mlbPcTextAttr(st, attr) {
  return _pcTextAttr(st, attr);
}
function _pcHasRealBooksVal(v) {
  const t = String(v == null ? "" : v).trim();
  if (!t || t === "—" || t === "-" || t === "–" || t === "N/A" || t.toLowerCase() === "n/a") {
    return false;
  }
  return true;
}
function _pcDetectBooksOnStacks(stacks) {
  if (PICKS_CHART_CFG.showBooks === false) return false;
  if (PICKS_CHART_CFG.showBooks === true) return true;
  let found = false;
  stacks.forEach(function (st) {
    if (found) return;
    const pick = (st.getAttribute("data-pick") || "").trim();
    const ml = _pcSlotOdds(st, pick).books;
    if (_pcHasRealBooksVal(ml)) found = true;
    if (_pcHasRealBooksVal(_pcBooksSpread(st))) found = true;
    if (_pcHasRealBooksVal(_pcBooksTotal(st))) found = true;
  });
  return found;
}
function _pcHasMarket(m) {
  return PICKS_CHART_CFG.markets.indexOf(m) >= 0;
}
function _pcIsMlbRunLine() {
  return PICKS_CHART_CFG.sport === "mlb";
}
function _pcSpreadNoun() {
  return _pcIsMlbRunLine() ? "run line" : "spread";
}
/** Fill missing home/away/pick/time from face markup (CFL-style sparse stacks). */
function _pcHydrateStack(st) {
  if (!st || st.getAttribute("data-pc-hydrated") === "1") return;
  const names = [];
  st.querySelectorAll(".team-slot .team-name").forEach(function (el) {
    const t = (el.textContent || "").trim();
    if (t) names.push(t);
  });
  if (names.length >= 2) {
    if (!(st.getAttribute("data-away") || "").trim()) {
      st.setAttribute("data-away", names[0]);
    }
    if (!(st.getAttribute("data-home") || "").trim()) {
      st.setAttribute("data-home", names[1]);
    }
  }
  if (!(st.getAttribute("data-pick") || "").trim()) {
    const fav = st.querySelector(".team-slot.favored .team-name");
    if (fav && fav.textContent) {
      st.setAttribute("data-pick", fav.textContent.trim());
    } else {
      let best = "",
        bestPct = -1;
      st.querySelectorAll(".team-slot").forEach(function (slot) {
        const nEl = slot.querySelector(".team-name");
        const pEl = slot.querySelector(".win-pct");
        if (!nEl || !pEl) return;
        const pct = parseFloat(String(pEl.textContent || "").replace(/[^\d.]/g, ""));
        if (!isNaN(pct) && pct > bestPct) {
          bestPct = pct;
          best = (nEl.textContent || "").trim();
        }
      });
      if (best) {
        st.setAttribute("data-pick", best);
        if (!(st.getAttribute("data-conf") || "").trim() && bestPct >= 0) {
          st.setAttribute("data-conf", String(bestPct));
        }
      }
    }
  }
  if (!(st.getAttribute("data-conf") || "").trim()) {
    const pick = (st.getAttribute("data-pick") || "").trim().toLowerCase();
    st.querySelectorAll(".team-slot").forEach(function (slot) {
      const nEl = slot.querySelector(".team-name");
      const pEl = slot.querySelector(".win-pct");
      if (!nEl || !pEl) return;
      if ((nEl.textContent || "").trim().toLowerCase() !== pick) return;
      const pct = parseFloat(String(pEl.textContent || "").replace(/[^\d.]/g, ""));
      if (!isNaN(pct)) st.setAttribute("data-conf", String(pct));
    });
  }
  if (!(st.getAttribute("data-time") || "").trim()) {
    const gt = st.querySelector(".game-time");
    if (gt && gt.textContent) st.setAttribute("data-time", gt.textContent.trim());
  }
  // Totals chart H2H L10: prefer data-h2h; copy face chip only when attr empty
  if (!(st.getAttribute("data-h2h") || "").trim()) {
    const items = st.querySelectorAll(".sf-item");
    let chipH2h = "";
    for (let i = 0; i < items.length; i++) {
      const lab = items[i].querySelector(".sf-label");
      const val = items[i].querySelector(".sf-val");
      const name = ((lab && lab.textContent) || "").trim().toLowerCase();
      if (name.indexOf("h2h") < 0) continue;
      chipH2h = (val && val.textContent ? val.textContent : "").trim();
      break;
    }
    if (chipH2h && chipH2h !== "—" && chipH2h !== "-" && chipH2h !== "–" && chipH2h.toLowerCase() !== "n/a") {
      st.setAttribute("data-h2h", chipH2h);
    } else if (PICKS_CHART_CFG.sport === "soccer") {
      st.setAttribute("data-h2h", "N/A");
    }
  }
  st.setAttribute("data-pc-hydrated", "1");
}
function _pcInfoTh(label, tip) {
  const tipTxt = String(tip || "").trim() || "About " + String(label || "");
  return (
    '<th class="num pct-info-th">' +
    '<span class="pct-th-label">' +
    _pcEsc(label) +
    '</span><button type="button" class="pct-info-btn" title="' +
    _pcEsc(tipTxt) +
    '" aria-label="' +
    _pcEsc(tipTxt) +
    '" data-tip="' +
    _pcEsc(tipTxt) +
    '">ⓘ</button></th>'
  );
}
function _mlbPcInfoTh(label, tip) {
  return _pcInfoTh(label, tip);
}
function _pcInfoThCtr(label, tip) {
  const tipTxt = String(tip || "").trim() || "About " + String(label || "");
  return (
    '<th class="ctr pct-info-th">' +
    '<span class="pct-th-label">' +
    _pcEsc(label) +
    '</span><button type="button" class="pct-info-btn" title="' +
    _pcEsc(tipTxt) +
    '" aria-label="' +
    _pcEsc(tipTxt) +
    '" data-tip="' +
    _pcEsc(tipTxt) +
    '">ⓘ</button></th>'
  );
}
function _mlbPcInfoThCtr(label, tip) {
  return _pcInfoThCtr(label, tip);
}
function _pcModelInfo(st, attr) {
  const raw = st.getAttribute(attr);
  if (raw === null || raw === "") return { valid: false };
  const p = parseFloat(raw);
  if (isNaN(p)) return { valid: false };
  const home = _pcEsc(st.getAttribute("data-home") || "");
  const away = _pcEsc(st.getAttribute("data-away") || "");
  return {
    valid: true,
    side: p >= 50 ? home : away,
    pct: Math.round((p >= 50 ? p : 100 - p) * 10) / 10,
  };
}
function _mlbPcModelInfo(st, attr) {
  return _pcModelInfo(st, attr);
}
function _pcEdgeTxt(st) {
  const edge = st.getAttribute("data-edge") || "";
  if (!edge) return "—";
  const n = parseFloat(edge);
  if (isNaN(n)) return _pcEsc(edge) + "%";
  return (n > 0 ? "+" : "") + _pcEsc(edge) + "%";
}
function _mlbPcEdgeTxt(st) {
  return _pcEdgeTxt(st);
}
function _pcFmtPctSigned(raw) {
  let t = String(raw == null ? "" : raw).trim();
  if (!t || t === "—") return "—";
  t = t.replace(/%/g, "").trim();
  const n = parseFloat(t);
  if (isNaN(n)) return "—";
  const body = String(t).replace(/^\+/, "");
  return (n > 0 ? "+" : "") + _pcEsc(body) + "%";
}
function _mlbPcFmtPctSigned(raw) {
  return _pcFmtPctSigned(raw);
}
function _pcTotalEvTxt(st) {
  const fromAttr = (st.getAttribute("data-total-ev") || "").trim();
  if (fromAttr) return _pcFmtPctSigned(fromAttr);
  const items = st.querySelectorAll(".sf-item");
  for (let i = 0; i < items.length; i++) {
    const lab = items[i].querySelector(".sf-label");
    const val = items[i].querySelector(".sf-val");
    const name = ((lab && lab.textContent) || "").trim().toLowerCase();
    if (name !== "total ev") continue;
    const txt = (val && val.textContent ? val.textContent : "").trim();
    if (txt) return _pcFmtPctSigned(txt);
  }
  return "—";
}
function _mlbPcTotalEvTxt(st) {
  return _pcTotalEvTxt(st);
}
function _pcOddsClass(raw) {
  const t = String(raw || "").trim();
  if (!t || t === "—") return "";
  if (t.startsWith("-") || t.startsWith("−")) return "fav";
  if (t.startsWith("+")) return "dog";
  return "";
}
function _mlbPcOddsClass(raw) {
  return _pcOddsClass(raw);
}
function _pcSlotOdds(st, teamName) {
  const want = (teamName || "").trim().toLowerCase();
  const slots = st.querySelectorAll(".team-slot");
  let books = "—";
  let pl = "—";
  slots.forEach(function (slot) {
    const nameEl = slot.querySelector(".team-name");
    const name = (nameEl && nameEl.textContent ? nameEl.textContent : "")
      .trim()
      .toLowerCase();
    if (!want || name !== want) return;
    const b = slot.querySelector(".face-books-ml .ml-num");
    const p = slot.querySelector(".face-pl-ml .ml-num");
    if (b && b.textContent) books = b.textContent.trim();
    if (p && p.textContent) pl = p.textContent.trim();
  });
  return { books: books || "—", pl: pl || "—" };
}
function _mlbPcSlotOdds(st, teamName) {
  return _pcSlotOdds(st, teamName);
}
function _pcDash(v) {
  const t = (v || "").trim();
  return t && t !== "—" ? t : "—";
}
function _mlbPcDash(v) {
  return _pcDash(v);
}
function _pcOddsLineCells(st, kind) {
  const want =
    kind === "total"
      ? ["total"]
      : ["run line", "runline", "puck line", "spread"];
  const rows = st.querySelectorAll(".odds-pricing-table tbody tr");
  for (let i = 0; i < rows.length; i++) {
    const kEl = rows[i].querySelector(".market-k");
    const lab = ((kEl && kEl.textContent) || "").trim().toLowerCase();
    let hit = false;
    for (let j = 0; j < want.length; j++) {
      if (lab.indexOf(want[j]) >= 0) {
        hit = true;
        break;
      }
    }
    if (!hit) continue;
    const books = rows[i].querySelector(".val-books");
    const pl = rows[i].querySelector(".val-pl");
    const xs = rows[i].querySelector(".val-xs");
    return {
      books: _pcDash(books && books.textContent),
      pl: _pcDash(pl && pl.textContent),
      xs: _pcDash(xs && xs.textContent),
    };
  }
  return { books: "—", pl: "—", xs: "—" };
}
function _mlbPcOddsLineCells(st, kind) {
  return _pcOddsLineCells(st, kind);
}
function _pcRunLineConfidence(st) {
  const chip = st.querySelector(".rl-confidence-chip .line-chip-val");
  if (chip && chip.textContent) {
    const n = parseFloat(String(chip.textContent).replace(/[^\d.]/g, ""));
    if (!isNaN(n)) return n;
  }
  return null;
}
function _mlbPcRunLineConfidence(st) {
  return _pcRunLineConfidence(st);
}
function _pcChipVal(st, labelSubstr) {
  const want = String(labelSubstr || "").toLowerCase();
  const labels = st.querySelectorAll(".line-chip-label");
  for (let i = 0; i < labels.length; i++) {
    const lab = (labels[i].textContent || "").trim().toLowerCase();
    if (lab.indexOf(want) < 0) continue;
    const val =
      labels[i].parentElement &&
      labels[i].parentElement.querySelector(".line-chip-val");
    if (val) return (val.textContent || "").trim() || "—";
  }
  return "—";
}
function _pcBooksSpread(st) {
  const fromAttr = (st.getAttribute("data-books-spread") || "").trim();
  if (fromAttr) return fromAttr;
  const fromTbl = _pcOddsLineCells(st, "run").books;
  if (fromTbl !== "—") return fromTbl;
  let chip = _pcChipVal(st, "books run line");
  if (chip !== "—") return chip;
  chip = _pcChipVal(st, "books spread");
  if (chip !== "—") return chip;
  return "—";
}
function _mlbPcBooksRunLine(st) {
  return _pcBooksSpread(st);
}
function _pcPlSpread(st) {
  const fromAttr = (st.getAttribute("data-pl-spread") || "").trim();
  if (fromAttr) return fromAttr;
  const fromTbl = _pcOddsLineCells(st, "run").pl;
  if (fromTbl !== "—") return fromTbl;
  const chip = _pcChipVal(st, "model spread");
  return chip !== "—" ? chip : "—";
}
function _mlbPcPlRunLine(st) {
  return _pcPlSpread(st);
}
function _pcXsSpread(st) {
  const fromAttr = (st.getAttribute("data-xs-spread") || "").trim();
  if (fromAttr) return fromAttr;
  return _pcOddsLineCells(st, "run").xs;
}
function _mlbPcXsRunLine(st) {
  return _pcXsSpread(st);
}
function _pcBooksTotal(st) {
  const fromAttr = (st.getAttribute("data-books-total") || "").trim();
  if (fromAttr) return fromAttr;
  const fromTbl = _pcOddsLineCells(st, "total").books;
  if (fromTbl !== "—") return fromTbl;
  const chip = _pcChipVal(st, "books total");
  return chip !== "—" ? chip : "—";
}
function _mlbPcBooksTotal(st) {
  return _pcBooksTotal(st);
}
function _pcProjFromDetails(st, which) {
  const rows = st.querySelectorAll(".proj-row");
  for (let i = 0; i < rows.length; i++) {
    const model = rows[i].querySelector(".proj-model");
    const val = rows[i].querySelector(".proj-val");
    if (!val || !val.textContent) continue;
    const txt = val.textContent.trim();
    if (!txt) continue;
    const name = ((model && model.textContent) || "").trim().toLowerCase();
    const cls = ((model && model.className) || "").toLowerCase();
    if (which === "pl") {
      if (name.indexOf("prediction") >= 0 || name === "pl" || /\bpl\b/.test(cls)) {
        return txt;
      }
    }
    if (which === "xs") {
      if (name.indexOf("xsharp") >= 0 || /\bxs\b/.test(cls)) return txt;
    }
  }
  return "";
}
function _mlbPcProjFromDetails(st, which) {
  return _pcProjFromDetails(st, which);
}
function _pcLabelScoreline(st, raw) {
  const s = (raw || "").trim();
  if (!s || s === "—") return "";
  if (/[A-Za-z]/.test(s) && /\d/.test(s) && /[–—-]/.test(s)) return s;
  const nums = s.match(/(\d+(?:\.\d+)?)/g);
  if (!nums || nums.length < 2) return "";
  const away = (st.getAttribute("data-away") || "").trim();
  const home = (st.getAttribute("data-home") || "").trim();
  const a = nums[nums.length - 2];
  const b = nums[nums.length - 1];
  if (away && home) return away + " " + a + " – " + home + " " + b;
  return a + "–" + b;
}
function _mlbPcLabelScoreline(st, raw) {
  return _pcLabelScoreline(st, raw);
}
function _pcPlProjDisplay(st) {
  const fromDetails = _pcProjFromDetails(st, "pl");
  if (fromDetails) {
    const labeled = _pcLabelScoreline(st, fromDetails);
    if (labeled) return labeled;
    return fromDetails;
  }
  const el = st.querySelector(".proj-row .proj-model.pl ~ .proj-val, .proj-row .proj-val");
  if (el && el.textContent && el.textContent.trim()) {
    const labeled = _pcLabelScoreline(st, el.textContent.trim());
    if (labeled) return labeled;
  }
  const raw = (st.getAttribute("data-pl-proj") || "").trim();
  const fromAttr = _pcLabelScoreline(st, raw) || raw;
  if (fromAttr) return fromAttr;
  // Odds & Lines PL total when Projected Score / data-pl-proj omitted (soccer PK cards)
  const oddsPl =
    (st.getAttribute("data-pl-total") || "").trim() || _pcPlOddsTotal(st);
  const T = _pcParseTotalNum(oddsPl);
  if (T == null) return "—";
  const awayName = (st.getAttribute("data-away") || "").trim();
  const homeName = (st.getAttribute("data-home") || "").trim();
  const home = _pcRoundHalf(T / 2);
  const away = _pcRoundHalf(T - home);
  const a = _pcFmtHalf(away);
  const b = _pcFmtHalf(home);
  if (awayName && homeName) return awayName + " " + a + " – " + homeName + " " + b;
  return a + "–" + b;
}
function _mlbPcPlProjDisplay(st) {
  return _pcPlProjDisplay(st);
}
function _pcRoundHalf(n) {
  return Math.round(Number(n) * 2) / 2;
}
function _mlbPcRoundHalfRuns(n) {
  return _pcRoundHalf(n);
}
function _pcFmtHalf(n) {
  const r = _pcRoundHalf(n);
  return Number.isInteger(r) ? String(r) : String(r);
}
function _mlbPcFmtRuns(n) {
  return _pcFmtHalf(n);
}
function _pcParseTotalNum(raw) {
  const m = String(raw == null ? "" : raw).match(/(\d+(?:\.\d+)?)/);
  if (!m) return null;
  const n = parseFloat(m[1]);
  return isNaN(n) || n <= 0 ? null : n;
}
function _mlbPcParseTotalNum(raw) {
  return _pcParseTotalNum(raw);
}
function _pcDeriveXsScorelineFromPl(st, xsTotalRaw, plProjDisp) {
  const T = _pcParseTotalNum(xsTotalRaw);
  if (T == null) return "";
  const nums = String(plProjDisp || "").match(/(\d+(?:\.\d+)?)/g);
  if (!nums || nums.length < 2) return "";
  const plA = parseFloat(nums[nums.length - 2]);
  const plH = parseFloat(nums[nums.length - 1]);
  if (isNaN(plA) || isNaN(plH) || plA + plH <= 0) return "";
  const sum = plA + plH;
  const away = _pcRoundHalf((plA / sum) * T);
  const home = _pcRoundHalf(T - away);
  const awayName = (st.getAttribute("data-away") || "").trim();
  const homeName = (st.getAttribute("data-home") || "").trim();
  const a = _pcFmtHalf(away);
  const b = _pcFmtHalf(home);
  if (awayName && homeName) return awayName + " " + a + " – " + homeName + " " + b;
  return a + "–" + b;
}
function _mlbPcDeriveXsScorelineFromPl(st, xsTotalRaw, plProjDisp) {
  return _pcDeriveXsScorelineFromPl(st, xsTotalRaw, plProjDisp);
}
function _pcXsProjCell(st) {
  const fromDetails = _pcProjFromDetails(st, "xs");
  if (fromDetails) {
    const labeled = _pcLabelScoreline(st, fromDetails);
    if (labeled) return { text: labeled, kind: "real" };
    return { text: fromDetails, kind: "real" };
  }
  const fromAttr = (st.getAttribute("data-xs-proj") || "").trim();
  const labeled = _pcLabelScoreline(st, fromAttr);
  if (labeled) return { text: labeled, kind: "real" };
  if (fromAttr && /\d/.test(fromAttr) && /[–—-]/.test(fromAttr)) {
    return { text: fromAttr, kind: "real" };
  }
  let oddsXs = _pcXsOddsTotal(st);
  let xsNum = _pcParseTotalNum(oddsXs);
  if (xsNum == null) {
    oddsXs = _pcBooksTotal(st);
    xsNum = _pcParseTotalNum(oddsXs);
  }
  if (xsNum == null) return { text: "—", kind: "none" };
  const plProj = _pcPlProjDisplay(st);
  const derived = _pcDeriveXsScorelineFromPl(st, oddsXs, plProj);
  if (derived) return { text: derived, kind: "estimated" };
  return { text: _pcFmtHalf(xsNum), kind: "total-only" };
}
function _mlbPcXsProjCell(st) {
  return _pcXsProjCell(st);
}
function _mlbPcXsProjDisplay(st) {
  return _pcXsProjCell(st).text;
}
function _pcSumProjTotal(disp) {
  if (!disp || disp === "—") return "—";
  const nums = String(disp).match(/(\d+(?:\.\d+)?)/g);
  if (!nums || nums.length < 2) return "—";
  const a = parseFloat(nums[nums.length - 2]);
  const b = parseFloat(nums[nums.length - 1]);
  if (isNaN(a) || isNaN(b)) return "—";
  const sum = a + b;
  return Number.isInteger(sum) ? String(sum) : String(Math.round(sum * 10) / 10);
}
function _mlbPcSumProjTotal(disp) {
  return _pcSumProjTotal(disp);
}
/**
 * Stacked totals cell HTML: total on its own line, scoreline underneath.
 * e.g. 9.5 / Boston Red Sox 4.5 – Toronto Blue Jays 5
 */
function _pcFormatProjStackedHtml(disp) {
  let s = String(disp == null ? "" : disp).trim();
  if (!s || s === "—") return "—";
  const pref = s.match(/^(\d+(?:\.\d+)?)\s*[·•]\s*(.+)$/);
  let total = "";
  let scoreline = s;
  if (pref) {
    total = pref[1];
    scoreline = pref[2].trim();
  } else {
    total = _pcSumProjTotal(s);
    if (total === "—") {
      if (/^\d+(?:\.\d+)?$/.test(s)) {
        return '<span class="proj-total">' + _pcEsc(s) + "</span>";
      }
      return _pcEsc(s);
    }
  }
  if (!scoreline || scoreline === total) {
    return '<span class="proj-total">' + _pcEsc(total) + "</span>";
  }
  return (
    '<span class="proj-total">' +
    _pcEsc(total) +
    '</span><span class="proj-scoreline">' +
    _pcEsc(scoreline) +
    "</span>"
  );
}
/** @deprecated alias — older callers expecting "total · scoreline" string */
function _pcFormatProjWithTotal(disp) {
  const s = String(disp == null ? "" : disp).trim();
  if (!s || s === "—") return "—";
  if (/^\d+(?:\.\d+)?\s*[·•]/.test(s)) return s;
  const sum = _pcSumProjTotal(s);
  if (sum === "—") return s;
  return sum + " · " + s;
}
function _mlbPcFormatProjWithTotal(disp) {
  return _pcFormatProjWithTotal(disp);
}
function _mlbPcFormatProjStackedHtml(disp) {
  return _pcFormatProjStackedHtml(disp);
}
function _pcXsOddsTotal(st) {
  return _pcOddsLineCells(st, "total").xs;
}
function _mlbPcXsOddsTotal(st) {
  return _pcXsOddsTotal(st);
}
function _pcPlOddsTotal(st) {
  return _pcOddsLineCells(st, "total").pl;
}
function _pcH2hL10(st) {
  // Totals H2H L10: use View Details / face chip OR data-h2h (either source).
  // Prior regression preferred empty data-h2h over a real chip — keep both.
  function _ok(t) {
    return !!(t && t !== "—" && t !== "-" && t !== "–" && t.toLowerCase() !== "n/a");
  }
  let fromChip = "";
  try {
    const items = st.querySelectorAll(".sf-item");
    for (let i = 0; i < items.length; i++) {
      const lab = items[i].querySelector(".sf-label");
      const val = items[i].querySelector(".sf-val");
      const name = ((lab && lab.textContent) || "").trim().toLowerCase();
      if (name.indexOf("h2h") < 0) continue;
      const t = (val && val.textContent ? val.textContent : "").trim();
      if (_ok(t)) {
        fromChip = t;
        break;
      }
    }
  } catch (e) {}
  const fromAttr = (st.getAttribute("data-h2h") || "").trim();
  const best = _ok(fromChip) ? fromChip : fromAttr;
  if (_ok(best)) {
    if ((st.getAttribute("data-h2h") || "").trim() !== best) {
      st.setAttribute("data-h2h", best);
    }
    return best;
  }
  // Soccer: first meetings / no DB history must read N/A, not an em-dash.
  if (PICKS_CHART_CFG.sport === "soccer") {
    return "N/A";
  }
  return _pcTextAttr(st, "data-h2h");
}
function _mlbPcH2hL10(st) {
  return _pcH2hL10(st);
}
function _pcEnsureMarketTabs() {
  let bar = document.getElementById("picksMarketTabs");
  if (bar) return bar;
  const markets = PICKS_CHART_CFG.markets;
  if (markets.length <= 1) {
    bar = document.createElement("nav");
    bar.id = "picksMarketTabs";
    bar.className = "picks-market-tabs picks-market-tabs-hidden";
    bar.style.display = "none";
    document.body.appendChild(bar);
    return bar;
  }
  const controls = document.querySelector(".picks-view-controls");
  bar = document.createElement("nav");
  bar.id = "picksMarketTabs";
  bar.className = "picks-market-tabs";
  bar.setAttribute("aria-label", "Pick market");
  const labels = {
    moneyline: "Moneyline",
    spread: "Spread",
    totals: "Totals",
  };
  markets.forEach(function (key, idx) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "market-tab" + (idx === 0 ? " active" : "");
    btn.setAttribute("data-market", key);
    btn.textContent = labels[key] || key;
    btn.addEventListener("click", function () {
      setPicksChartMarket(key);
    });
    bar.appendChild(btn);
  });
  if (controls) {
    controls.style.flexWrap = "wrap";
    controls.appendChild(bar);
  } else {
    document.body.appendChild(bar);
  }
  return bar;
}
function _mlbPcEnsureMarketTabs() {
  return _pcEnsureMarketTabs();
}
function _pcSyncMarketTabActive() {
  const bar = _pcEnsureMarketTabs();
  bar.querySelectorAll(".market-tab").forEach(function (btn) {
    btn.classList.toggle(
      "active",
      btn.getAttribute("data-market") === PICKS_CHART_MARKET
    );
  });
  MLB_PICKS_CHART_MARKET = PICKS_CHART_MARKET;
}
function _mlbPcSyncMarketTabActive() {
  _pcSyncMarketTabActive();
}
function setPicksChartMarket(market) {
  if (!_pcHasMarket(market)) {
    market = PICKS_CHART_CFG.markets[0] || "moneyline";
  }
  PICKS_CHART_MARKET =
    market === "spread" || market === "totals" ? market : "moneyline";
  if (!_pcHasMarket(PICKS_CHART_MARKET)) {
    PICKS_CHART_MARKET = PICKS_CHART_CFG.markets[0] || "moneyline";
  }
  MLB_PICKS_CHART_MARKET = PICKS_CHART_MARKET;
  _pcSyncMarketTabActive();
  refreshChartForActive();
}
function _pcModelCells(st, hasModels) {
  if (!hasModels) return "";
  const infos = PICKS_CHART_MODEL_ATTRS.map(function (pair) {
    return _pcModelInfo(st, pair[0]);
  });
  let maxPct = -1;
  infos.forEach(function (i) {
    if (i.valid && i.pct > maxPct) maxPct = i.pct;
  });
  let html = "";
  infos.forEach(function (i) {
    if (!i.valid) {
      html += '<td class="mdl ctr"><span class="na">N/A</span></td>';
      return;
    }
    const best = i.pct === maxPct ? " best-model" : "";
    html +=
      '<td class="mdl ctr"><span class="' +
      _pcConfClass(i.pct) +
      best +
      '">' +
      i.side +
      " " +
      i.pct +
      "%</span></td>";
  });
  return html;
}
function _mlbPcModelCells(st, hasModels) {
  return _pcModelCells(st, hasModels);
}
function _pcModelHead(hasModels) {
  if (!hasModels) return "";
  return PICKS_CHART_MODEL_ATTRS.map(function (pair) {
    return '<th class="ctr">' + _pcEsc(pair[1]) + "</th>";
  }).join("");
}
function _mlbPcModelHead(hasModels) {
  return _pcModelHead(hasModels);
}
function _pcMoneylineRow(st, hasModels) {
  _pcHydrateStack(st);
  const away = _pcEsc(st.getAttribute("data-away"));
  const home = _pcEsc(st.getAttribute("data-home"));
  const pick = (st.getAttribute("data-pick") || "").trim() || "—";
  const odds = _pcSlotOdds(st, pick);
  return (
    "<tr>" +
    '<td class="mu-col"><span class="mu-away">' +
    away +
    '</span><span class="mu-home">' +
    home +
    "</span></td>" +
    '<td class="ctr t-time">' +
    _pcEsc(st.getAttribute("data-time")) +
    "</td>" +
    '<td class="ctr pick-cell"><strong>' +
    _pcEsc(pick) +
    "</strong></td>" +
    (PICKS_CHART_SHOW_BOOKS
      ? '<td class="ctr odds-cell"><span class="' +
        _pcOddsClass(odds.books) +
        '">' +
        _pcEsc(odds.books) +
        "</span></td>"
      : "") +
    '<td class="ctr odds-cell"><span class="' +
    _pcOddsClass(odds.pl) +
    '">' +
    _pcEsc(odds.pl) +
    "</span></td>" +
    _pcModelCells(st, hasModels) +
    "</tr>"
  );
}
function _mlbPcMoneylineRow(st, hasModels) {
  return _pcMoneylineRow(st, hasModels);
}
function _pcSpreadRow(st) {
  _pcHydrateStack(st);
  const away = _pcEsc(st.getAttribute("data-away"));
  const home = _pcEsc(st.getAttribute("data-home"));
  const booksRl = _pcBooksSpread(st);
  const plRl = _pcPlSpread(st);
  const xsRl = _pcXsSpread(st);
  return (
    "<tr>" +
    '<td class="mu-col"><span class="mu-away">' +
    away +
    '</span><span class="mu-home">' +
    home +
    "</span></td>" +
    '<td class="ctr t-time">' +
    _pcEsc(st.getAttribute("data-time")) +
    "</td>" +
    (PICKS_CHART_SHOW_BOOKS ? '<td class="num">' + _pcEsc(booksRl) + "</td>" : "") +
    '<td class="num">' +
    _pcEsc(plRl) +
    "</td>" +
    '<td class="num">' +
    _pcEsc(xsRl) +
    "</td>" +
    "</tr>"
  );
}
function _mlbPcSpreadRow(st) {
  return _pcSpreadRow(st);
}
function _pcTotalsRow(st, hasProj) {
  _pcHydrateStack(st);
  const away = _pcEsc(st.getAttribute("data-away"));
  const home = _pcEsc(st.getAttribute("data-home"));
  const tot = _pcBooksTotal(st);
  const totDisp =
    tot === "—" ? "—" : tot.toLowerCase().indexOf("o/u") >= 0 ? tot : "O/U " + tot;
  const h2h = _pcH2hL10(st);
  const plProj = hasProj
    ? _pcFormatProjStackedHtml(_pcPlProjDisplay(st))
    : "—";
  const xsCell = _pcXsProjCell(st);
  const xsProj = _pcFormatProjStackedHtml(xsCell.text);
  return (
    "<tr>" +
    '<td class="mu-col"><span class="mu-away">' +
    away +
    '</span><span class="mu-home">' +
    home +
    "</span></td>" +
    '<td class="ctr t-time">' +
    _pcEsc(st.getAttribute("data-time")) +
    "</td>" +
    (PICKS_CHART_SHOW_BOOKS ? '<td class="num">' + _pcEsc(totDisp) + "</td>" : "") +
    '<td class="ctr h2h-cell">' +
    _pcEsc(h2h) +
    "</td>" +
    '<td class="proj-col proj-cell">' +
    plProj +
    "</td>" +
    '<td class="proj-col proj-cell">' +
    xsProj +
    "</td>" +
    "</tr>"
  );
}
function _mlbPcTotalsRow(st, hasProj) {
  return _pcTotalsRow(st, hasProj);
}

function _pcEnsureChartWrap(section) {
  if (!section) return null;
  let wrap = section.querySelector(".chart-table-wrap");
  if (wrap) return wrap;
  const id = section.id || "";
  const m = /^date-(.+)$/.exec(id);
  if (m) {
    const orphan = document.getElementById("chart-" + m[1]);
    if (orphan) {
      section.appendChild(orphan);
      return orphan;
    }
  }
  wrap = document.createElement("div");
  wrap.className = "chart-table-wrap";
  if (m) wrap.id = "chart-" + m[1];
  section.appendChild(wrap);
  return wrap;
}

function _pcEnsureStacksInGrid(section) {
  if (!section) return;
  let grid = section.querySelector(":scope > .games-grid");
  if (!grid) {
    grid = document.createElement("div");
    grid.className = "games-grid";
    const header = section.querySelector(":scope > .date-header");
    if (header && header.nextSibling) {
      section.insertBefore(grid, header.nextSibling);
    } else {
      section.insertBefore(grid, section.firstChild);
    }
  }
  [...section.querySelectorAll(":scope > .game-card-stack")].forEach(function (st) {
    grid.appendChild(st);
  });
}

function buildChartTable(section) {
  if (!section) return;
  _pcEnsureStacksInGrid(section);
  const wrap = _pcEnsureChartWrap(section);
  if (!wrap) return;
  _pcEnsureMarketTabs();
  const controls = document.querySelector(".picks-view-controls");
  if (controls) controls.classList.add("chart-active");

  const stacks = section.querySelectorAll("[data-pick-card]");
  let hasModels = false;
  let hasProj = false;
  stacks.forEach(function (st) {
    _pcHydrateStack(st);
    if (st.getAttribute("data-m-consensus")) hasModels = true;
    if (
      st.getAttribute("data-xs-proj") ||
      st.getAttribute("data-pl-proj") ||
      st.querySelector(".proj-val")
    ) {
      hasProj = true;
    }
  });
  PICKS_CHART_SHOW_BOOKS = _pcDetectBooksOnStacks(stacks);

  const EDGE_TIP =
    "Difference between our win probability and the market's implied probability.";
  const SPREAD_EDGE_TIP =
    "Same moneyline Edge as the Moneyline tab: our win % vs the books' implied win %. Not a separate run-line/spread edge.";
  const RL_CONF_TIP =
    "How strongly our run-line model favors its side (0–100). One score for that run-line lean — not a second PL or XSharp pick.";
  const TOTAL_EV_TIP =
    "Expected value on the totals market: how our projected run total compares to the books’ O/U line, shown as a percentage. Positive means the model sees value vs the market; negative means it doesn’t. Not moneyline Edge.";
  const XS_PROJ_TIP =
    "XSharp projected scoreline when the card has one; otherwise estimated from the XSharp total using the Prediction Lab score split (or the total alone if no PL scoreline).";
  const XS_TOTAL_TIP =
    "Sum of XSharp projected scores when a scoreline exists; otherwise Odds & Lines XSharp total.";
  const WIN_TIP =
    "Sharp Consensus win probability for the pick (same % shown on the card).";

  let head = "";
  let label = "";
  const market = PICKS_CHART_MARKET;
  if (market === "spread" && _pcHasMarket("spread")) {
    const noun = _pcSpreadNoun();
    label = _pcIsMlbRunLine() ? "Spread / run line" : "Spread";
    head =
      '<th class="mu-col">Matchup</th><th class="ctr">Time</th>' +
      (PICKS_CHART_SHOW_BOOKS ? '<th class="num">Books ' + noun + "</th>" : "") +
      '<th class="num">PL ' +
      noun +
      "</th>" +
      '<th class="num">XSharp ' +
      noun +
      "</th>";
  } else if (market === "totals" && _pcHasMarket("totals")) {
    label = "Totals";
    head =
      '<th class="mu-col">Matchup</th><th class="ctr">Time</th>' +
      (PICKS_CHART_SHOW_BOOKS ? '<th class="num">Books total</th>' : "") +
      '<th class="ctr">H2H L10</th>' +
      '<th class="proj-col">Prediction Lab total</th>' +
      '<th class="proj-col">XSharp total</th>';
  } else {
    label = "Moneyline";
    head =
      '<th class="mu-col">Matchup</th><th class="ctr">Time</th>' +
      '<th class="ctr">Pick</th>' +
      (PICKS_CHART_SHOW_BOOKS ? '<th class="ctr">Books ML</th>' : "") +
      '<th class="ctr">PL odds</th>' +
      _pcModelHead(hasModels);
  }

  const colCount = (head.match(/<th/g) || []).length;
  let rows = "";
  let lastLeague = "";
  stacks.forEach(function (st) {
    const lg = st.getAttribute("data-league") || "";
    if (lg && lg !== lastLeague) {
      lastLeague = lg;
      rows +=
        '<tr class="pct-league-row"><td colspan="' +
        colCount +
        '">' +
        _pcEsc(lg) +
        "</td></tr>";
    }
    if (market === "spread" && _pcHasMarket("spread")) rows += _pcSpreadRow(st);
    else if (market === "totals" && _pcHasMarket("totals"))
      rows += _pcTotalsRow(st, hasProj);
    else rows += _pcMoneylineRow(st, hasModels);
  });

  if (!rows) {
    wrap.innerHTML =
      '<div style="padding:16px;color:#64748b;">No picks to chart for this date.</div>';
    return;
  }
  wrap.innerHTML =
    '<div class="picks-chart-market-label">' +
    _pcEsc(label) +
    "</div>" +
    '<table class="picks-chart-table"><thead><tr>' +
    head +
    "</tr></thead><tbody>" +
    rows +
    "</tbody></table>";
}

function refreshChartForActive() {
  const sec = document.querySelector(".date-section.visible");
  document.querySelectorAll(".date-section").forEach(function (s) {
    if (s !== sec) s.classList.remove("chart-mode");
  });
  if (!sec) return;
  if (typeof picksViewMode !== "undefined" && picksViewMode === "chart") {
    buildChartTable(sec);
    sec.classList.add("chart-mode");
    const controls = document.querySelector(".picks-view-controls");
    if (controls) controls.classList.add("chart-active");
  } else {
    sec.classList.remove("chart-mode");
    const controls = document.querySelector(".picks-view-controls");
    if (controls) controls.classList.remove("chart-active");
  }
}

function setPicksView(mode) {
  picksViewMode = mode === "chart" ? "chart" : "cards";
  const cb = document.getElementById("pvCardsBtn");
  const hb = document.getElementById("pvChartBtn");
  if (cb) cb.classList.toggle("active", picksViewMode === "cards");
  if (hb) hb.classList.toggle("active", picksViewMode === "chart");
  const controls = document.querySelector(".picks-view-controls");
  if (picksViewMode === "cards") {
    document.querySelectorAll(".date-section").forEach(function (s) {
      s.classList.remove("chart-mode");
    });
    if (controls) controls.classList.remove("chart-active");
  } else {
    if (controls) controls.classList.add("chart-active");
    _pcEnsureMarketTabs();
    _pcSyncMarketTabActive();
    refreshChartForActive();
  }
}

(function _pcBootFromQuery() {
  function run() {
    const params = new URLSearchParams(window.location.search || "");
    const view = (params.get("view") || "").toLowerCase();
    const market = (params.get("market") || "").toLowerCase();
    if (
      (market === "spread" || market === "totals" || market === "moneyline") &&
      _pcHasMarket(market)
    ) {
      PICKS_CHART_MARKET = market;
      MLB_PICKS_CHART_MARKET = market;
    } else {
      PICKS_CHART_MARKET = PICKS_CHART_CFG.markets[0] || "moneyline";
      MLB_PICKS_CHART_MARKET = PICKS_CHART_MARKET;
    }
    _pcEnsureMarketTabs();
    _pcSyncMarketTabActive();
    if (view === "chart" || view === "tabs" || view === "markets") {
      setPicksView("chart");
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(run, 0);
    });
  } else {
    setTimeout(run, 0);
  }
})();
