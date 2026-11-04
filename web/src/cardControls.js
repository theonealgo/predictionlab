// The game cards come from the server pages as HTML, and their buttons call these functions by name
// (onclick="togglePickDetails(this)"). Same behaviour as the server pages. A panel is looked up inside
// its own card first: the home page shows several sports, and two sports can use the same panel id.

function panelFor(el, id) {
  if (!id) return null;
  const card = el && el.closest('.pick-card, .game-card, .game-card-stack');
  const safe = window.CSS && CSS.escape ? CSS.escape(id) : id;
  return (card && card.querySelector(`#${safe}`)) || document.getElementById(id);
}

window.toggleAnalysis = function toggleAnalysis(id, el) {
  const panel = panelFor(el, id);
  if (!panel) return;
  panel.classList.toggle('open');
  const open = panel.classList.contains('open');
  if (el) el.textContent = open ? 'Analysis ▴' : 'Analysis ▾';
};

window.togglePickDetails = function togglePickDetails(btn) {
  if (!btn) return;
  const panel = panelFor(btn, btn.getAttribute('aria-controls'));
  const card = btn.closest('.pick-card') || btn.closest('.game-card') || btn.closest('.game-card-stack');
  if (!panel) return;
  const willOpen = card ? !card.classList.contains('is-expanded') : panel.hasAttribute('hidden');
  if (card) card.classList.toggle('is-expanded', willOpen);
  if (willOpen) {
    panel.removeAttribute('hidden');
    btn.setAttribute('aria-expanded', 'true');
    btn.innerHTML = 'Less details <span class="chevron">▾</span>';
  } else {
    panel.setAttribute('hidden', '');
    btn.setAttribute('aria-expanded', 'false');
    btn.innerHTML = 'View Details <span class="chevron">▾</span>';
  }
};
