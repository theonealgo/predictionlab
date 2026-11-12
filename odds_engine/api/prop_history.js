const STAT_NAMES = {
  points: ['points'], rebounds: ['totalrebounds', 'rebounds'], assists: ['assists'],
  threes: ['threepointfieldgoalsmade', 'threepointfieldgoalsmadethreepointfieldgoalsattempted'],
  passing_yards: ['passingyards'], rushing_yards: ['rushingyards'], receiving_yards: ['receivingyards'],
  receptions: ['receptions'], shots_on_goal: ['shotsongoal'], shots: ['totalshots', 'shots'],
  shots_on_target: ['shotsontarget'], goals: ['goals'], hits: ['hits'], runs: ['runs'],
  rbis: ['rbis', 'runsbattedin'], home_runs: ['homeruns'], walks: ['walks'], strikeouts: ['strikeouts'],
};
const LABELS = { points: 'PTS', rebounds: 'REB', assists: 'AST', threes: '3PT',
  shots_on_goal: 'SOG', shots: 'SH', goals: 'G', hits: 'H', runs: 'R', rbis: 'RBI',
  home_runs: 'HR', walks: 'BB', strikeouts: 'K', receptions: 'REC' };
const normalize = value => String(value || '').toLowerCase().replace(/[^a-z]/g, '');
const etDay = date => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(date);

export function readPropHistory(body, propType, now = new Date()) {
  const labels = body.labels || [];
  const names = (body.names || []).map(normalize);
  let index = names.findIndex(name => (STAT_NAMES[propType] || []).includes(name));
  if (index < 0 && LABELS[propType]) index = labels.indexOf(LABELS[propType]);
  if (index < 0) return null;
  if (propType === 'strikeouts' && labels.includes('AB')) return null;
  if (propType === 'hits' && labels.includes('IP')) return null;
  const observations = new Map();
  for (const season of body.seasonTypes || []) {
    if (!String(season.displayName || '').toLowerCase().includes('regular season')) continue;
    const rows = [...(season.events || []), ...(season.categories || []).flatMap(category => category.events || [])];
    for (const row of rows) {
      const metadata = (body.events || {})[row.eventId] || {};
      const start = new Date(metadata.gameDate);
      if (!Number.isFinite(start.getTime()) || etDay(start) >= etDay(now)) continue;
      if (!['W', 'L'].includes(String(metadata.gameResult || '').toUpperCase())) continue;
      const raw = (row.stats || [])[index];
      if (raw === null || raw === undefined || raw === '' || raw === '--') continue;
      let value;
      if (propType === 'threes') {
        const parts = String(raw).split('-').map(Number);
        if (parts.length !== 2 || parts.some(part => !Number.isInteger(part) || part < 0) || parts[0] > parts[1]) continue;
        [value] = parts;
      } else value = Number(raw);
      if (!Number.isFinite(value)) continue;
      const minutesIndex = labels.indexOf('MIN');
      if (minutesIndex >= 0 && !(Number((row.stats || [])[minutesIndex]) > 0)) continue;
      observations.set(String(row.eventId), { start: start.getTime(), value });
    }
  }
  const recent = [...observations.values()].sort((a, b) => b.start - a.start).slice(0, 20);
  return recent.length >= 5 ? { recent_form: recent.map(row => row.value),
    history_verified: true, history_count: recent.length, as_of: now.toISOString() } : null;
}
