// Pulls college basketball and football conference membership and the AP Top 25 from ESPN's public site API.
// Teams are keyed by ESPN team id, the number in each team's logo file on the picks cards.
// Writes src/data/collegeGroups.json. Run from web/: node scripts/fetch-college-groups.mjs
import { writeFile } from 'node:fs/promises';

const API = 'https://site.api.espn.com/apis';
const OUT = new URL('../src/data/collegeGroups.json', import.meta.url);

// ESPN sport paths, by our sport key.
const LEAGUES = {
  NCAAB: 'basketball/mens-college-basketball',
  NCAAW: 'basketball/womens-college-basketball',
  NCAAF: 'football/college-football',
};

// Standings groups to read (ESPN group ids): Division I basketball, FBS and FCS football.
const DIVISIONS = {
  NCAAB: { 'Division I': 50 },
  NCAAW: { 'Division I': 50 },
  NCAAF: { FBS: 80, FCS: 81 },
};

// Division I basketball conferences (men and women), and the ESPN conference names (by prefix) each covers.
const BASKETBALL_CHIPS = {
  SEC: ['Southeastern'],
  'Big Ten': ['Big Ten'],
  ACC: ['Atlantic Coast'],
  'Big 12': ['Big 12'],
  'Big East': ['Big East'],
  'A-10': ['Atlantic 10'],
  American: ['American Conference'],
  'America East': ['America East'],
  ASUN: ['Atlantic Sun'],
  'Big Sky': ['Big Sky'],
  'Big South': ['Big South'],
  'Big West': ['Big West'],
  CAA: ['Coastal Athletic'],
  'C-USA': ['Conference USA'],
  Horizon: ['Horizon'],
  Ivy: ['Ivy'],
  MAAC: ['Metro Atlantic', 'Metro Conference'],
  MAC: ['Mid-American'],
  MEAC: ['Mid-Eastern'],
  'Mountain West': ['Mountain West'],
  MVC: ['Missouri Valley'],
  NEC: ['Northeast'],
  OVC: ['Ohio Valley'],
  Patriot: ['Patriot'],
  SoCon: ['Southern Conference'],
  Southland: ['Southland'],
  Summit: ['Summit'],
  'Sun Belt': ['Sun Belt'],
  SWAC: ['Southwestern Athletic'],
  WAC: ['Western Athletic', 'United Athletic'],
  WCC: ['West Coast'],
};

// Each filter chip on the sport page, and the ESPN conference names (by prefix) it covers.
const CHIPS = {
  NCAAB: BASKETBALL_CHIPS,
  NCAAW: BASKETBALL_CHIPS,
  NCAAF: {
    SEC: ['Southeastern'],
    'Big Ten': ['Big Ten'],
    ACC: ['Atlantic Coast'],
    'Big 12': ['Big 12'],
    'Pac-12': ['Pac-12'],
    American: ['American Conference'],
    'Mountain West': ['Mountain West'],
    'Sun Belt': ['Sun Belt'],
    MAC: ['Mid-American'],
    'C-USA': ['Conference USA'],
    'FBS Ind.': ['FBS Independents'],
    'Big Sky': ['Big Sky'],
    CAA: ['Coastal Athletic'],
    MVFC: ['Missouri Valley Football'],
    SoCon: ['Southern Conference'],
    Southland: ['Southland'],
    SWAC: ['Southwestern Athletic'],
    Patriot: ['Patriot'],
    Pioneer: ['Pioneer Football'],
    Ivy: ['Ivy'],
    MEAC: ['Mid-Eastern'],
    NEC: ['Northeast'],
    OVC: ['Ohio Valley'],
    UAC: ['United Athletic'],
    'FCS Ind.': ['FCS Independents'],
  },
};

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
}

// Every team id in a standings node, its divisions included (Sun Belt East / West).
function teamIds(node) {
  const own = (node.standings?.entries || []).map((entry) => String(entry.team.id));
  return [...own, ...(node.children || []).flatMap(teamIds)];
}

// Conference name and its team ids, from the standings (one child per conference).
function conferencesOf(standings) {
  return (standings.children || []).map((conference) => ({ name: conference.name, ids: teamIds(conference) }));
}

function idsFor(conferences, prefixes) {
  const ids = conferences
    .filter((conference) => prefixes.some((prefix) => conference.name.startsWith(prefix)))
    .flatMap((conference) => conference.ids);
  return [...new Set(ids)];
}

async function fetchLeague(sport, path) {
  const conferences = [];
  const divisions = {};
  for (const [label, group] of Object.entries(DIVISIONS[sport])) {
    const standings = await getJson(`${API}/v2/sports/${path}/standings?group=${group}`);
    const list = conferencesOf(standings);
    conferences.push(...list);
    divisions[label] = [...new Set(list.flatMap((conference) => conference.ids))];
  }
  const groups = {};
  for (const [label, prefixes] of Object.entries(CHIPS[sport])) {
    groups[label] = idsFor(conferences, prefixes);
  }
  if (sport === 'NCAAB') {
    groups['Power 5'] = [
      ...new Set([
        ...groups.ACC, ...groups['Big Ten'], ...groups['Big 12'], ...groups.SEC,
        ...idsFor(conferences, ['Pac-12']),
      ]),
    ];
  }

  if (sport === 'NCAAF') {
    groups.FBS = divisions.FBS;
    groups.FCS = divisions.FCS;
  }

  const rankings = await getJson(`${API}/site/v2/sports/${path}/rankings`);
  const poll = rankings.rankings.find((item) => item.name === 'AP Top 25') || rankings.rankings[0];
  const top25 = poll.ranks.map((rank) => ({
    rank: rank.current,
    id: String(rank.team.id),
    name: `${rank.team.location} ${rank.team.name}`,
    logo: rank.team.logo || '',
  }));
  groups['Top 25'] = top25.map((team) => team.id);

  for (const [label, ids] of Object.entries(groups)) {
    if (ids.length === 0) console.warn(`${sport}: no ESPN conference matched "${label}"`);
  }
  return { groups, top25 };
}

const out = {};
for (const [sport, path] of Object.entries(LEAGUES)) {
  out[sport] = await fetchLeague(sport, path);
  console.log(`${sport}: ${Object.keys(out[sport].groups).length} filters, ${out[sport].top25.length} ranked teams`);
}
await writeFile(OUT, `${JSON.stringify(out, null, 2)}\n`);
console.log(`wrote ${OUT.pathname}`);
