// Keep every live game; fill only the remaining preview slots, across leagues/tournaments.
export function selectLiveStripItems(items, limit = 4) {
  const rows = Array.isArray(items) ? items.filter(item => item && typeof item === 'object') : [];
  const sportOf = item => String(item.sport || '').trim().toUpperCase();
  const liveCounts = new Map();
  rows.forEach(item => {
    if (item.live === true) liveCounts.set(sportOf(item), (liveCounts.get(sportOf(item)) || 0) + 1);
  });
  const upcoming = new Map();
  const next = rows.filter(item => {
    if (item.live === true) return false;
    const sport = sportOf(item);
    const room = Math.max(0, limit - (liveCounts.get(sport) || 0));
    const count = upcoming.get(sport) || 0;
    if (count >= room) return false;
    upcoming.set(sport, count + 1);
    return true;
  });
  return [...rows.filter(item => item.live === true), ...next];
}
