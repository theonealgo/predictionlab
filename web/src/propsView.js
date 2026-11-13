export function matchesPropSide(row, side = 'all') {
  if (side === 'all') return true;
  const selection = row.pick || row.recorded_side;
  if (selection) return String(selection).toUpperCase() === side;
  return (row.model_forecasts || []).some(forecast => String(forecast.side || '').toUpperCase() === side);
}

export function filterPropResults(items, { model = 'all', market = 'all', search = '', lineType = 'all', game = '', side = 'all' } = {}) {
  const query = search.trim().toLowerCase();
  return (items || []).filter(row => (model === 'all' || row.model_id === model)
    && (market === 'all' || row.prop_type === market)
    && (lineType === 'all' || row.line_type === lineType)
    && matchesPropSide(row, side)
    && (!game || row.game_id === game || String(row.event_id) === game || `${row.league}_${row.event_id}` === game)
    && (!query || String(row.player_name || '').toLowerCase().includes(query)));
}

export function modelPropCounts(rows) {
  const grouped = new Map();
  rows.forEach(row => {
    if (!row.model_id) return;
    const key = `${row.league}:${row.model_id}:${row.prop_type}`;
    const result = grouped.get(key) || { league: row.league, model_id: row.model_id,
      model_name: row.model_name, prop_type: row.prop_type, recorded: 0,
      wins: 0, losses: 0, pushes: 0, pending: 0, voids: 0, unavailable: 0 };
    result.recorded += 1;
    const field = { Win: 'wins', Loss: 'losses', Push: 'pushes', Pending: 'pending', Void: 'voids', 'N/A': 'unavailable' }[row.result];
    if (field) result[field] += 1;
    grouped.set(key, result);
  });
  return [...grouped.values()].map(row => ({ ...row, decided: row.wins + row.losses,
    win_rate: row.wins + row.losses ? 100 * row.wins / (row.wins + row.losses) : null }))
    .sort((a, b) => `${a.league}:${a.prop_type}:${a.model_name}`.localeCompare(`${b.league}:${b.prop_type}:${b.model_name}`));
}

export function propTypeCounts(rows) {
  const counts = new Map();
  rows.forEach(row => {
    const summary = counts.get(row.prop_type) || { prop_type: row.prop_type, recorded: 0, wins: 0, losses: 0, pushes: 0 };
    summary.recorded += 1;
    // Unassigned legacy grades must never become model results.
    if (row.model_id && row.result === 'Win') summary.wins += 1;
    if (row.model_id && row.result === 'Loss') summary.losses += 1;
    if (row.model_id && row.result === 'Push') summary.pushes += 1;
    counts.set(row.prop_type, summary);
  });
  return [...counts.values()].sort((a, b) => a.prop_type.localeCompare(b.prop_type)).map(summary => ({
    ...summary,
    win_rate: summary.wins + summary.losses ? 100 * summary.wins / (summary.wins + summary.losses) : null,
  }));
}

export function modelCounts(rows, models) {
  return (models || []).map(model => {
    const selected = rows.filter(row => row.model_id === model.id);
    const wins = selected.filter(row => row.result === 'Win').length;
    const losses = selected.filter(row => row.result === 'Loss').length;
    const pushes = selected.filter(row => row.result === 'Push').length;
    return { ...model, captured: selected.length, wins, losses, pushes, decided: wins + losses,
      win_rate: wins + losses ? 100 * wins / (wins + losses) : null };
  });
}
