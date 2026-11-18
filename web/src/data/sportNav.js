// Sport pages are the game predictions. Player props live on /player-props only.
// A filter chip works only where the site has the teams (see SportPage).
const GAMES_ONLY = ['Games'];
const TOURNAMENTS_ONLY = ['Tournaments'];
const TEAM_CONFERENCE_FILTERS = ['All', 'East', 'West'];
const NFL_CONFERENCE_FILTERS = ['All', 'AFC', 'NFC'];
const COLLEGE_BASKETBALL_CONFERENCES = [
  'SEC', 'Big Ten', 'ACC', 'Big 12', 'Big East', 'A-10', 'American', 'America East',
  'ASUN', 'Big Sky', 'Big South', 'Big West', 'CAA', 'C-USA', 'Horizon', 'Ivy', 'MAAC', 'MAC', 'MEAC',
  'Mountain West', 'MVC', 'NEC', 'OVC', 'Patriot', 'SoCon', 'Southland', 'Summit', 'Sun Belt', 'SWAC', 'WAC', 'WCC',
];
const COLLEGE_MEN_FILTERS = ['All', 'Top 25', 'Power 5', ...COLLEGE_BASKETBALL_CONFERENCES];
const COLLEGE_WOMEN_FILTERS = ['All', 'Top 25', ...COLLEGE_BASKETBALL_CONFERENCES];
const COLLEGE_FOOTBALL_FILTERS = [
  'All', 'Top 25', 'FBS', 'FCS', 'SEC', 'Big Ten', 'ACC', 'Big 12', 'Pac-12', 'American', 'Mountain West',
  'Sun Belt', 'MAC', 'C-USA', 'FBS Ind.', 'Big Sky', 'CAA', 'MVFC', 'SoCon', 'Southland', 'SWAC', 'Patriot',
  'Pioneer', 'Ivy', 'MEAC', 'NEC', 'OVC', 'UAC', 'FCS Ind.',
];

// Soccer's league bubbles: the active leagues from soccer_league_catalog.py, in the catalog's order.
export const SOCCER_LEAGUES = [
  'All', 'English Premier League', 'Italian Serie A', 'German Bundesliga', 'Spanish LaLiga', 'French Ligue 1',
  'UEFA Champions League', 'Brazilian Serie A', 'Portuguese Primeira Liga', 'Belgian Pro League', 'Major League Soccer',
  'EFL Championship', 'Dutch Eredivisie', 'Turkish Super Lig', 'Austrian Bundesliga', 'Scottish Premiership',
  'Swiss Super League',
];

export const DEFAULT_NAV = { tabs: GAMES_ONLY, filters: null };

export const SPORT_NAV = {
  NBA: { tabs: GAMES_ONLY, filters: TEAM_CONFERENCE_FILTERS },
  NHL: { tabs: GAMES_ONLY, filters: TEAM_CONFERENCE_FILTERS },
  MLB: { tabs: GAMES_ONLY, filters: TEAM_CONFERENCE_FILTERS },
  WNBA: { tabs: GAMES_ONLY, filters: TEAM_CONFERENCE_FILTERS },
  NFL: { tabs: GAMES_ONLY, filters: NFL_CONFERENCE_FILTERS },
  NCAAF: { tabs: GAMES_ONLY, filters: COLLEGE_FOOTBALL_FILTERS, bubbles: true, text: true },
  SOCCER: { tabs: GAMES_ONLY, filters: SOCCER_LEAGUES, bubbles: true, text: true },
  NCAAB: { tabs: GAMES_ONLY, filters: COLLEGE_MEN_FILTERS, bubbles: true, text: true },
  NCAAW: { tabs: GAMES_ONLY, filters: COLLEGE_WOMEN_FILTERS, bubbles: true, text: true },
  CFL: { tabs: GAMES_ONLY, filters: null },
  UFC: { tabs: ['Matches'], filters: null },
  GOLF: { tabs: TOURNAMENTS_ONLY, filters: null },
  TENNIS: { tabs: GAMES_ONLY, filters: null },
};
