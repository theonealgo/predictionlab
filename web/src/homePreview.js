// Read displayed values only. Never recompute a probability or borrow another model's value.
export function readCardPreview(card) {
  if (!card?.html) return null;
  const template = document.createElement('template');
  template.innerHTML = card.html;
  const root = template.content;
  const slots = [...root.querySelectorAll('.team-slot')].slice(0, 2);
  const names = [card.away, card.home];
  const read = (slot, fallback) => {
    const headline = slot?.querySelector('.win-pct');
    const match = headline?.textContent.match(/^\s*(\d+(?:\.\d+)?)\s*%/);
    const number = match ? Number(match[1]) : null;
    return {
      name: slot?.querySelector('.team-name')?.textContent.trim() || fallback || '',
      logo: slot?.querySelector('.team-logo')?.getAttribute('src') || '',
      percent: number !== null && number >= 0 && number <= 100 ? match[1] : null,
      reason: headline?.getAttribute('title') || 'A displayed probability is not available for this event.',
    };
  };
  const teams = names.map((name, index) => read(slots[index], name));
  if (!teams.some((team) => team.name)) return null;
  const draw = root.querySelector('.soccer-draw-row .draw-slot');
  if (draw) teams.push({ ...read(draw, 'Draw'), name: 'Draw' });
  let confidence = null;
  if (teams.every((team) => team.percent === null)) {
    const stamp = root.querySelector('[data-pick-card]');
    const published = stamp?.getAttribute('data-conf')?.match(/^(\d+(?:\.\d+)?)%?$/);
    const picked = stamp?.getAttribute('data-pick');
    if (published && Number(published[1]) <= 100 && picked) {
      confidence = { percent: published[1], team: picked };
    }
  }
  if (teams.every((team) => team.percent === null) && !confidence) {
    const consensus = [...root.querySelectorAll('.pc-box')].find((box) =>
      /^(?:Sharp|Market) Consensus$/i.test(box.querySelector('.pc-name')?.textContent.replace(/[ⓘℹ]/g, '').trim() || ''));
    const match = consensus?.querySelector('.pc-val')?.textContent.match(/^\s*(\d+(?:\.\d+)?)\s*%/);
    const team = consensus?.querySelector('.pc-side')?.textContent.trim();
    if (match && Number(match[1]) <= 100 && team) {
      confidence = { percent: match[1], team };
    }
  }
  return {
    teams,
    confidence,
    time: root.querySelector('.game-time')?.textContent.trim() || '',
    date: card.date || '',
    game: `${card.away || teams[0].name}|${card.home || teams[1].name}`,
  };
}

export function readGolfPreview(payload) {
  const event = payload.events?.find((item) => item.id === payload.selected);
  const rows = (payload.field || []).slice(0, 2);
  if (!rows.length) return null;
  return {
    title: event?.name || 'Golf tournament',
    date: event?.date || '',
    time: 'Tournament win probability',
    query: payload.selected ? { event: payload.selected } : {},
    teams: rows.map((row) => {
      const match = String(row.win || '').match(/^(\d+(?:\.\d+)?)\s*%$/);
      return {
        name: row.player,
        logo: row.photo,
        percent: match && Number(match[1]) <= 100 ? match[1] : null,
        reason: 'No tournament win probability was supplied for this player.',
      };
    }),
  };
}
