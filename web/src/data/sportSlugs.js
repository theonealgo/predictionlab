// The sports with a React page at /<slug>-picks and /<slug>-projections. Other pages whose name
// ends in -picks (such as /ladder-picks) are server pages and must not open the sports template.
// A sport page's public address: /<sport>-predictions (the old -picks / -projections addresses forward there).
export const publicPath = (slug) => `/${String(slug || '').replace(/-(picks|projections)$/, '-predictions')}`;

export const SPORT_SLUGS = ['nhl', 'nba', 'ncaab', 'ncaaw', 'mlb', 'soccer', 'nfl', 'ncaaf', 'wnba', 'tennis', 'ufc', 'golf', 'cfl'];
