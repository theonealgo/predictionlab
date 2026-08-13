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
const API = (window.SOCCER_API_BASE || "/api").replace(/\/$/, "");

let STATE = {
  markets: null,
  active: "moneyline",
  league: "ALL",
};

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
      <div class="match">${esc(c.away_team_id)} <span class="at">@</span> ${esc(c.home_team_id)}</div>
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
    <td>${esc(c.away_team_id)} @ ${esc(c.home_team_id)}</td>
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
      <div class="match">${esc(c.away_team_id)} <span class="at">@</span> ${esc(c.home_team_id)}</div>
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
    <td>${esc(c.away_team_id)} @ ${esc(c.home_team_id)}</td>
    <td>${c.home_score != null ? esc(c.home_score) + "–" + esc(c.away_score) : "—"}</td>
    <td>${esc(row.pick || "—")}</td>
    <td>${resultMark(row.correct, row.push)}</td>
  </tr>`;
}

function tallyBlock(title, block, order) {
  if (!block) return "";
  const models = block.models || {};
  const names = (order && order.length)
    ? order
    : (Object.keys(models).length ? Object.keys(models) : DEFAULT_ORDER);
  const cards = names.map((name) => {
    const m = models[name] || {};
    const n = m.n || 0;
    const pct = m.pct;
    const rec = m.record || `${m.w || 0}-${m.l || 0}`;
    let acc = '<div class="acc muted">—</div>';
    let cls = "";
    if (n > 0 && pct != null) {
      cls = pct >= 52 ? "ok" : pct < 40 ? "bad" : "";
      acc = `<div class="acc ${cls}">${pct}%</div>`;
    }
    return `<div class="tally-card">
      <div class="mlabel">${esc(name)}</div>
      ${acc}
      <div class="rec">${esc(rec)}${n ? "" : " · no picks"}</div>
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
  // Spread/Totals: Prediction Lab only — never reuse moneyline model order.
  const order = key === "moneyline"
    ? ((market && market.model_order) || DEFAULT_ORDER)
    : ((market && market.model_order) || ["Prediction Lab"]).filter(
      (n) => n === "Prediction Lab" || n === "Edge"
    );
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

  const windowBlocks = WINDOW_KEYS.map((wk) => {
    const block = tallies[wk];
    if (!block) return "";
    return tallyBlock(block.label || wk, block, order);
  }).filter(Boolean);

  wrap.hidden = false;
  wrap.innerHTML =
    `<div class="bet-type-banner" data-market="${esc(key)}">${esc(label.toUpperCase())}</div>` +
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
    head.innerHTML = `<tr>
      <th>Date</th><th>League</th><th>Match</th><th>Score</th>
      <th>Edge pick</th><th>%</th><th>Result</th><th>Models</th>
    </tr>`;
    body.innerHTML = finals.map((c) => mlRowHtml(c, order)).join("") ||
      '<tr><td colspan="8" class="muted">No finals for this league.</td></tr>';
    cards.innerHTML = finals.map((c) => mlCardHtml(c, order)).join("");
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
  if (data.markets && data.markets.moneyline) return data.markets;
  // Fallback for older payloads: moneyline-only
  return {
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
    spread: {
      label: "Spread",
      tallies: {},
      model_order: ["Prediction Lab"],
      finals: [],
    },
    totals: {
      label: "Totals",
      tallies: {},
      model_order: ["Prediction Lab"],
      finals: [],
    },
  };
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
    STATE.markets = normalizeMarkets(data);
    document.getElementById("market-tabs").hidden = false;

    if (!MARKET_ORDER.includes(STATE.active)) STATE.active = "moneyline";
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
