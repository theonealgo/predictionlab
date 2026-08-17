const DEFAULT_ORDER = [
  "Grinder2", "Takedown", "Edge", "XSharp", "Sharp Consensus", "Efficiency",
];
const MARKET_ORDER = ["moneyline", "spread", "totals"];
const MARKET_LABELS = {
  moneyline: "Moneyline",
  spread: "Spread",
  totals: "Totals (O/U)",
};
const WINDOW_KEYS = ["last_night", "last_7", "season"];
const API = (window.TEAM_API_BASE || window.SOCCER_API_BASE || "/api").replace(
  /\/$/,
  ""
);

let STATE = {
  markets: null,
  analytics: null,
  active: "moneyline",
  league: "ALL",
  mlOnly: false,
};

function teamName(c, side) {
  if (side === "home") {
    return c.home || c.home_team || c.home_team_id || "";
  }
  return c.away || c.away_team || c.away_team_id || "";
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function displayLeague(lg) {
  const s = (lg || "").trim();
  if (!s || s.toLowerCase() === "soccer") return "Unknown league";
  return s;
}

function modelCompact(models, order) {
  const names = order && order.length ? order : DEFAULT_ORDER;
  return names.map((name) => {
    const m = models && models[name];
    if (!m) return `<span class="model-chip muted">${esc(name)} —</span>`;
    let mark = "";
    if (m.correct === true) mark = " <span class='yes'>✓</span>";
    if (m.correct === false) mark = " <span class='no'>✗</span>";
    return `<span class="model-chip"><b>${esc(name)}</b> ${esc(m.pick)}${mark}</span>`;
  }).join(" ");
}

function resultMark(correct, push) {
  if (push) return '<span class="muted">Push</span>';
  if (correct === true) return '<span class="yes">Correct</span>';
  if (correct === false) return '<span class="no">Wrong</span>';
  return '<span class="muted">—</span>';
}

function mlCardHtml(c, order) {
  let badge = `<span class="pill">Final</span>`;
  if (c.correct === true) badge = `<span class="pill ok-pill">Correct</span>`;
  if (c.correct === false) badge = `<span class="pill bad-pill">Wrong</span>`;
  const score = c.home_score != null
    ? `<div class="score">${esc(c.home_score)} – ${esc(c.away_score)}</div>` : "";
  const face = c.face_pick
    ? `<div class="face">Edge: <strong>${esc(c.face_pick)}</strong> · ${c.face_prob != null ? c.face_prob + "%" : "—"}</div>`
    : `<div class="face muted">${esc(c.note || "")}</div>`;
  const grid = (order || DEFAULT_ORDER).map((name) => {
    const m = c.models && c.models[name];
    if (!m) return `<div class="model-mini"><b>${esc(name)}</b><span>—</span></div>`;
    let mark = "";
    if (m.correct === true) mark = " <span class='yes'>✓</span>";
    if (m.correct === false) mark = " <span class='no'>✗</span>";
    return `<div class="model-mini"><b>${esc(name)}</b><span>${esc(m.pick)}${mark}</span><span class="prob">${m.prob != null ? m.prob + "%" : "—"}</span></div>`;
  }).join("");
  return `<article class="game-card ${c.correct === true ? "is-correct" : ""} ${c.correct === false ? "is-wrong" : ""}">
    <div class="game-top"><div>
      <div class="league">${esc(displayLeague(c.league))}</div>
      <div class="match">${esc(teamName(c, "away"))} <span class="at">@</span> ${esc(teamName(c, "home"))}</div>
      <div class="date">${esc(c.game_date)}</div>
    </div><div class="game-right">${badge}${score}</div></div>
    ${face}
    <div class="model-grid">${grid}</div>
  </article>`;
}

function mlRowHtml(c, order) {
  return `<tr>
    <td>${esc(c.game_date)}</td>
    <td>${esc(displayLeague(c.league))}</td>
    <td>${esc(teamName(c, "away"))} @ ${esc(teamName(c, "home"))}</td>
    <td>${c.home_score != null ? esc(c.home_score) + "–" + esc(c.away_score) : "—"}</td>
    <td>${esc(c.face_pick || "—")}</td>
    <td>${c.face_prob != null ? c.face_prob + "%" : "—"}</td>
    <td>${resultMark(c.correct)}</td>
    <td class="mono-models">${modelCompact(c.models, order)}</td>
  </tr>`;
}

function souCardHtml(c, marketKey) {
  const row = c[marketKey] || {};
  let badge = `<span class="pill">Final</span>`;
  if (row.push) badge = `<span class="pill">Push</span>`;
  else if (row.correct === true) badge = `<span class="pill ok-pill">Correct</span>`;
  else if (row.correct === false) badge = `<span class="pill bad-pill">Wrong</span>`;
  const score = c.home_score != null
    ? `<div class="score">${esc(c.home_score)} – ${esc(c.away_score)}</div>` : "";
  const label = marketKey === "spread" ? "Spread" : "Total";
  return `<article class="game-card ${row.correct === true ? "is-correct" : ""} ${row.correct === false ? "is-wrong" : ""}">
    <div class="game-top"><div>
      <div class="league">${esc(displayLeague(c.league))}</div>
      <div class="match">${esc(teamName(c, "away"))} <span class="at">@</span> ${esc(teamName(c, "home"))}</div>
      <div class="date">${esc(c.game_date)}</div>
    </div><div class="game-right">${badge}${score}</div></div>
    <div class="face">${esc(label)}: <strong>${esc(row.pick || "—")}</strong></div>
  </article>`;
}

function souRowHtml(c, marketKey) {
  const row = c[marketKey] || {};
  return `<tr>
    <td>${esc(c.game_date)}</td>
    <td>${esc(displayLeague(c.league))}</td>
    <td>${esc(teamName(c, "away"))} @ ${esc(teamName(c, "home"))}</td>
    <td>${c.home_score != null ? esc(c.home_score) + "–" + esc(c.away_score) : "—"}</td>
    <td>${esc(row.pick || "—")}</td>
    <td>${resultMark(row.correct, row.push)}</td>
  </tr>`;
}

function fmtUnits(u) {
  if (u == null || u === "") return "";
  const n = Number(u);
  if (Number.isNaN(n)) return "";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}u`;
}

function analyticsHtml(analytics) {
  if (!analytics) return "";
  const best = analytics.best_performing || {};
  const eff = analytics.efficiency_breakout || {};
  // UFC/Tennis (ml_only): never render blank Efficiency · Spread / Total dash cards.
  const mlOnly = !!(STATE.mlOnly || analytics.ml_only);

  const bestCard = (label, row) => {
    if (!row) {
      return `<div class="tally-card"><div class="mlabel">${esc(label)}</div><div class="acc muted">—</div></div>`;
    }
    const u = fmtUnits(row.units);
    return `<div class="tally-card">
      <div class="mlabel">${esc(label)}</div>
      <div class="rec"><b>${esc(row.name || "—")}</b></div>
      <div class="acc ok">${esc(row.pct)}%</div>
      <div class="rec">${esc(row.record || "")}${u ? " · " + esc(u) : ""}</div>
    </div>`;
  };

  const effCard = (row) => {
    if (!row) return "";
    const r = row || {};
    const n = Number(r.n || r.graded_games || 0) || 0;
    // Never show "—" when graded data exists
    const acc = r.accuracy != null ? `${r.accuracy}%` : (n > 0 ? "0%" : "—");
    const rec = r.record && r.record !== "—" ? r.record : (n > 0 ? "0-0" : "—");
    const u = fmtUnits(r.units) || (n > 0 ? "+0.0u" : "—");
    const games = n > 0 ? ` · ${n} graded games` : "";
    return `<div class="tally-card">
      <div class="mlabel">${esc(r.label || "Efficiency Season")}</div>
      <div class="acc">${esc(acc)}</div>
      <div class="rec">Accuracy ${esc(acc)} · Record ${esc(rec)} · Units ${esc(u)}${esc(games)}</div>
    </div>`;
  };

  const effCards = [effCard(eff.moneyline)];
  if (!mlOnly) {
    if (eff.spread) effCards.push(effCard(eff.spread));
    if (eff.total) effCards.push(effCard(eff.total));
  }

  return `<section class="tally pl-analytics">
    <h2>Best Performing Model</h2>
    <div class="tally-grid">
      ${bestCard("Today", best.today)}
      ${bestCard("Last 7", best.last_7)}
      ${bestCard("Season", best.season)}
    </div>
    <h2 style="margin-top:1.25rem">Efficiency by Market</h2>
    <div class="tally-grid">
      ${effCards.filter(Boolean).join("")}
    </div>
  </section>`;
}

function tallyBlock(title, block, order) {
  if (!block) return "";
  const models = block.models || {};
  let names = (order && order.length)
    ? order
    : (Object.keys(models).length ? Object.keys(models) : DEFAULT_ORDER);
  // Season face tallies often only have one model — don't spam empty 0-0 cards.
  const isSeason = /season/i.test(title || "") || /season/i.test(block.label || "");
  if (isSeason) {
    const withData = names.filter((name) => {
      const m = models[name] || {};
      return (m.n || 0) > 0 || (m.pct != null);
    });
    if (withData.length) names = withData;
  }
  const cards = names.map((name) => {
    const m = models[name] || {};
    const n = m.n || 0;
    let pct = m.pct;
    const rec = m.record || `${m.w || 0}-${m.l || 0}`;
    let u = fmtUnits(m.units);
    // Derive units from W-L when missing (display only)
    if (!u && n > 0) {
      const w = Number(m.w || 0);
      const l = Number(m.l || 0);
      if (w + l > 0) {
        const units = Math.round((w * (100 / 110) - l) * 10) / 10;
        u = `${units >= 0 ? "+" : ""}${units.toFixed(1)}u`;
      }
    }
    if (n > 0 && (pct == null || Number.isNaN(pct))) {
      const w = Number(m.w || 0);
      const l = Number(m.l || 0);
      if (w + l > 0) pct = Math.round((1000 * w) / (w + l)) / 10;
    }
    let label = name;
    if (/efficiency/i.test(name) && /season/i.test(title || "") && n > 0) {
      // Prefer full-season wording; thin samples get Graded Sample (matches hub analytics)
      const peerMax = names.reduce((mx, nm) => {
        if (/efficiency/i.test(nm)) return mx;
        return Math.max(mx, Number((models[nm] || {}).n || 0));
      }, 0);
      const graded = peerMax > 0 && n < Math.max(40, Math.floor(peerMax * 0.45));
      label = graded ? "Efficiency Season (Graded Sample)" : "Efficiency Season";
    }
    let acc = '<div class="acc muted">—</div>';
    let cls = "";
    if (n > 0) {
      const show = pct != null && !Number.isNaN(pct) ? pct : 0;
      cls = show >= 52 ? "ok" : show < 40 ? "bad" : "";
      acc = `<div class="acc ${cls}">${show}%</div>`;
    }
    return `<div class="tally-card">
      <div class="mlabel">${esc(label)}</div>
      ${acc}
      <div class="rec">${esc(rec)}${n ? "" : " · no picks"}${u ? " · " + esc(u) : ""}${n ? ` · ${n} graded` : ""}</div>
    </div>`;
  }).join("");
  const sub = block.date
    ? esc(block.date)
    : (block.date_from && block.date_to ? `${esc(block.date_from)} → ${esc(block.date_to)}` : "");
  const readyNote = block.ready === false && block.reason
    ? `<p class="note">${esc(block.reason)}</p>` : "";
  return `<section class="tally">
    <h2>${esc(title)} <span class="tag">(${block.games || 0} games${sub ? " · " + sub : ""})</span></h2>
    ${readyNote}
    <div class="tally-grid">${cards}</div>
  </section>`;
}

function setActiveTab(market) {
  STATE.active = market;
  document.body.dataset.market = market;
  document.querySelectorAll(".market-tab").forEach((btn) => {
    const on = btn.dataset.market === market;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  renderActiveMarket();
}

function renderActiveMarket() {
  const wrap = document.getElementById("tallies");
  const markets = STATE.markets || {};
  const key = STATE.active;
  const market = markets[key];
  const label = (market && market.label) || MARKET_LABELS[key] || key;
  // Spread/Totals: face model(s) only — never reuse full moneyline model order.
  // Season face can be XSharp (O/U) while Last Night/Last 7 stay Prediction Lab.
  const baseOrder = key === "moneyline"
    ? ((market && market.model_order) || DEFAULT_ORDER)
    : ((market && market.model_order) || ["Prediction Lab"]);
  const tallies = (market && market.tallies) || {};
  const finals = (market && market.finals) || [];
  const head = document.getElementById("results-head");
  const body = document.getElementById("results-body");
  const cards = document.getElementById("finals");
  const sum = document.getElementById("summary");

  // Always wipe previous market DOM so ML tallies never linger on Spread/Totals ads.
  wrap.innerHTML = "";
  head.innerHTML = "";
  body.innerHTML = "";
  cards.innerHTML = "";
  sum.textContent = "";

  if (!market) {
    wrap.hidden = true;
    document.getElementById("finals-wrap").hidden = true;
    sum.hidden = true;
    return;
  }

  const faceOrderFor = (block) => {
    if (key === "moneyline") return baseOrder;
    const models = (block && block.models) || {};
    const fromBlock = (block && Array.isArray(block.model_order) && block.model_order.length)
      ? block.model_order
      : Object.keys(models).filter((n) => {
        const m = models[n] || {};
        return (m.n || 0) > 0 || m.pct != null;
      });
    if (fromBlock.length) return fromBlock;
    // Keep non-ML grids off the six moneyline names unless they actually have face data.
    return baseOrder.filter((n) => n === "Prediction Lab" || n === "Edge" || n === "XSharp");
  };

  const windowBlocks = WINDOW_KEYS.map((wk) => {
    const block = tallies[wk];
    if (!block) return "";
    return tallyBlock(block.label || wk, block, faceOrderFor(block));
  }).filter(Boolean);

  wrap.hidden = false;
  const analyticsBlock = key === "moneyline" ? analyticsHtml(STATE.analytics) : "";
  wrap.innerHTML =
    `<div class="bet-type-banner" data-market="${esc(key)}">${esc(label.toUpperCase())}</div>` +
    analyticsBlock +
    windowBlocks.join("");

  const season = tallies.season || {};
  const face = (season.models && (
    season.models["Prediction Lab"] || season.models.Edge || Object.values(season.models)[0]
  )) || {};
  const seasonPct = face.pct != null ? `${face.pct}%` : (season.pct != null ? `${season.pct}%` : "—");
  const seasonRec = face.record || season.record || "—";
  sum.hidden = false;
  sum.textContent = `${label} · Season ${seasonPct} (${seasonRec}) · ${finals.length} recent graded games shown.`;

  document.getElementById("finals-wrap").hidden = false;
  document.getElementById("games-heading").textContent = `${label} games`;
  document.getElementById("game-count").textContent = `(${finals.length})`;

  if (key === "moneyline") {
    // Same MLB Moneyline chart columns (Edge pick + Models). ML-only sports keep League.
    head.innerHTML = `<tr>
      <th>Date</th><th>League</th><th>Match</th><th>Score</th>
      <th>Edge pick</th><th>%</th><th>Result</th><th>Models</th>
    </tr>`;
    body.innerHTML = finals.map((c) => mlRowHtml(c, baseOrder)).join("") ||
      '<tr><td colspan="8" class="muted">No finals for this league.</td></tr>';
    cards.innerHTML = finals.map((c) => mlCardHtml(c, baseOrder)).join("");
  } else {
    const pickLabel = key === "spread" ? "Spread pick" : "O/U pick";
    head.innerHTML = `<tr>
      <th>Date</th><th>League</th><th>Match</th><th>Score</th>
      <th>${esc(pickLabel)}</th><th>Result</th>
    </tr>`;
    body.innerHTML = finals.map((c) => souRowHtml(c, key)).join("") ||
      '<tr><td colspan="6" class="muted">No graded games for this market yet.</td></tr>';
    cards.innerHTML = finals.map((c) => souCardHtml(c, key)).join("");
  }
}

function normalizeMarkets(data) {
  if (data.markets && data.markets.moneyline) {
    // ML-only payloads may omit spread/totals — do not invent empty shells that
    // resurface blank Efficiency · Spread / Total cards.
    if (data.ml_only || (data.analytics && data.analytics.ml_only)) {
      return { moneyline: data.markets.moneyline };
    }
    return data.markets;
  }
  // Fallback for older payloads: moneyline-only
  const mlOnly = !!(data.ml_only || (data.analytics && data.analytics.ml_only));
  const markets = {
    moneyline: {
      label: "Moneyline",
      tallies: {
        last_night: (data.tallies || {}).last_night,
        last_7: (data.tallies || {}).last_7,
        season: (data.tallies || {}).season,
      },
      model_order: data.model_order || DEFAULT_ORDER,
      finals: data.finals || [],
    },
  };
  if (!mlOnly) {
    markets.spread = {
      label: "Spread",
      tallies: {},
      model_order: ["Prediction Lab"],
      finals: [],
    };
    markets.totals = {
      label: "Totals",
      tallies: {},
      model_order: ["Prediction Lab"],
      finals: [],
    };
  }
  return markets;
}

async function populateLeagues(preferred, fallbackNames) {
  const sel = document.getElementById("league");
  if (!sel || window.TEAM_HIDE_LEAGUE) return;
  const current = preferred || sel.value || "ALL";
  let opts = [];
  try {
    const lr = await (await fetch(`${API}/leagues`)).json();
    if (lr.ok && Array.isArray(lr.leagues)) {
      opts = lr.leagues.map((x) => (typeof x === "string" ? x : x.name)).filter(Boolean);
    }
  } catch (e) {}
  if (!opts.length && Array.isArray(fallbackNames)) {
    opts = fallbackNames.filter(Boolean);
  }
  const seen = new Set();
  opts = opts.filter((n) => {
    if (seen.has(n)) return false;
    seen.add(n);
    return true;
  });
  sel.innerHTML = ['<option value="ALL">All Leagues</option>']
    .concat(opts.map((l) => `<option value="${esc(l)}">${esc(l)}</option>`))
    .join("");
  sel.value = current;
  if (![...sel.options].some((o) => o.value === sel.value)) sel.value = "ALL";
}

async function loadResults() {
  const leagueEl = document.getElementById("league");
  const league = (leagueEl && leagueEl.value) || "ALL";
  const status = document.getElementById("status");
  const runBtn = document.getElementById("run");
  if (status) status.textContent = "Loading results…";
  if (runBtn) runBtn.disabled = true;
  try {
    await populateLeagues(league);

    const qs = window.TEAM_HIDE_LEAGUE ? "" : `?league=${encodeURIComponent(league)}`;
    const res = await fetch(`${API}/picks${qs}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Failed");

    if (!window.TEAM_HIDE_LEAGUE && Array.isArray(data.all_leagues) && data.all_leagues.length) {
      const names = data.all_leagues.map((x) => (typeof x === "string" ? x : x.name));
      await populateLeagues(league, names);
    }

    STATE.league = league;
    STATE.mlOnly = !!(data.ml_only || (data.analytics && data.analytics.ml_only));
    STATE.markets = normalizeMarkets(data);
    STATE.analytics = data.analytics || null;
    document.getElementById("market-tabs").hidden = false;
    // Hide Spread/Totals tab buttons when sport is moneyline-only.
    if (STATE.mlOnly) {
      document.querySelectorAll('.market-tab[data-market="spread"], .market-tab[data-market="totals"]').forEach((btn) => {
        btn.hidden = true;
        btn.style.display = "none";
      });
    }

    if (!MARKET_ORDER.includes(STATE.active) || (STATE.mlOnly && STATE.active !== "moneyline")) {
      STATE.active = "moneyline";
    }
    setActiveTab(STATE.active);
    if (status) status.textContent = "Done.";
  } catch (e) {
    if (status) status.textContent = String(e.message || e);
  } finally {
    if (runBtn) runBtn.disabled = false;
  }
}

const runBtn = document.getElementById("run");
const leagueEl = document.getElementById("league");
if (runBtn) runBtn.addEventListener("click", loadResults);
if (leagueEl && !window.TEAM_HIDE_LEAGUE) {
  leagueEl.addEventListener("change", loadResults);
}
document.getElementById("market-tabs").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".market-tab");
  if (!btn || !btn.dataset.market) return;
  setActiveTab(btn.dataset.market);
});
loadResults();
