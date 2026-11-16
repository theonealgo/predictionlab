/** Betting Tools catalog — routes, SEO copy, FAQ for each calculator. */

export const TOOLS = [
  {
    slug: 'odds-conversion-calculator',
    path: '/tools/odds-conversion-calculator',
    title: 'Odds Conversion Calculator',
    short: 'Convert American, decimal, fractional odds and implied probability instantly.',
    metaTitle: 'Odds Conversion Calculator — American, Decimal, Fractional | PredictionLab',
    metaDescription:
      'Free odds conversion calculator: convert American, decimal, and fractional odds to implied probability, plus payout and profit from a stake.',
  },
  {
    slug: 'parlay-calculator',
    path: '/tools/parlay-calculator',
    title: 'Parlay Calculator',
    short: 'Combine multiple legs into parlay odds, win probability, payout, and profit.',
    metaTitle: 'Parlay Calculator — Combined Odds, Payout & Profit | PredictionLab',
    metaDescription:
      'Free parlay calculator for American odds: add legs, see combined decimal and American odds, implied win probability, total payout, and net profit.',
  },
  {
    slug: 'hold-calculator',
    path: '/tools/hold-calculator',
    title: 'Hold Calculator',
    short: 'Measure sportsbook overround and hold from two-way or three-way markets.',
    metaTitle: 'Hold Calculator — Sportsbook Overround & Vig | PredictionLab',
    metaDescription:
      'Free sportsbook hold calculator: enter both sides (or 1X2), see implied probabilities, market overround, and hold percentage.',
  },
  {
    slug: 'no-vig-calculator',
    path: '/tools/no-vig-calculator',
    title: 'No-Vig Calculator',
    short: 'Remove the vig to estimate fair win probabilities and fair odds.',
    metaTitle: 'No-Vig Fair Odds Calculator | PredictionLab',
    metaDescription:
      'Free no-vig calculator: remove sportsbook margin from two-way or three-way markets to get fair probabilities and fair American/decimal odds.',
  },
  {
    slug: 'clv-calculator',
    path: '/tools/clv-calculator',
    title: 'CLV Calculator',
    short: 'Compare your bet price to the closing line and measure closing line value.',
    metaTitle: 'CLV Calculator — Closing Line Value | PredictionLab',
    metaDescription:
      'Free closing line value (CLV) calculator: compare odds taken vs closing odds, optional no-vig close, and see CLV % with a clear positive/negative badge.',
  },
  {
    slug: 'kelly-criterion-calculator',
    path: '/tools/kelly-criterion-calculator',
    title: 'Kelly Criterion Calculator',
    short: 'Size bets from bankroll, odds, and your estimated win probability.',
    metaTitle: 'Kelly Criterion Calculator — Stake Sizing | PredictionLab',
    metaDescription:
      'Free Kelly criterion calculator: compute EV, full Kelly, and half/quarter Kelly stake recommendations from bankroll, American odds, and true win probability.',
  },
];

export const TOOL_BY_PATH = Object.fromEntries(TOOLS.map((t) => [t.path, t]));

export function toolBySlug(slug) {
  return TOOLS.find((t) => t.slug === slug) || null;
}
