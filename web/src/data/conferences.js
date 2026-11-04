// Conference teams by full name, exactly as the cards print them.
// Used only for the All / East / West (NFL: All / AFC / NFC) filter under GAMES.
export const CONFERENCES = {
  NFL: {
    afc: [
      'Baltimore Ravens', 'Buffalo Bills', 'Cincinnati Bengals', 'Cleveland Browns', 'Denver Broncos',
      'Houston Texans', 'Indianapolis Colts', 'Jacksonville Jaguars', 'Kansas City Chiefs', 'Las Vegas Raiders',
      'Los Angeles Chargers', 'Miami Dolphins', 'New England Patriots', 'New York Jets', 'Pittsburgh Steelers',
      'Tennessee Titans',
    ],
    nfc: [
      'Arizona Cardinals', 'Atlanta Falcons', 'Carolina Panthers', 'Chicago Bears', 'Dallas Cowboys',
      'Detroit Lions', 'Green Bay Packers', 'Los Angeles Rams', 'Minnesota Vikings', 'New Orleans Saints',
      'New York Giants', 'Philadelphia Eagles', 'San Francisco 49ers', 'Seattle Seahawks',
      'Tampa Bay Buccaneers', 'Washington Commanders',
    ],
  },
  NBA: {
    east: [
      'Atlanta Hawks', 'Boston Celtics', 'Brooklyn Nets', 'Charlotte Hornets', 'Chicago Bulls',
      'Cleveland Cavaliers', 'Detroit Pistons', 'Indiana Pacers', 'Miami Heat', 'Milwaukee Bucks',
      'New York Knicks', 'Orlando Magic', 'Philadelphia 76ers', 'Toronto Raptors', 'Washington Wizards',
    ],
    west: [
      'Dallas Mavericks', 'Denver Nuggets', 'Golden State Warriors', 'Houston Rockets', 'LA Clippers',
      'Los Angeles Lakers', 'Memphis Grizzlies', 'Minnesota Timberwolves', 'New Orleans Pelicans',
      'Oklahoma City Thunder', 'Phoenix Suns', 'Portland Trail Blazers', 'Sacramento Kings',
      'San Antonio Spurs', 'Utah Jazz',
    ],
  },
  NHL: {
    east: [
      'Boston Bruins', 'Buffalo Sabres', 'Carolina Hurricanes', 'Columbus Blue Jackets',
      'Detroit Red Wings', 'Florida Panthers', 'Montreal Canadiens', 'New Jersey Devils',
      'New York Islanders', 'New York Rangers', 'Ottawa Senators', 'Philadelphia Flyers',
      'Pittsburgh Penguins', 'Tampa Bay Lightning', 'Toronto Maple Leafs', 'Washington Capitals',
    ],
    west: [
      'Anaheim Ducks', 'Arizona Coyotes', 'Utah Mammoth', 'Utah Hockey Club', 'Calgary Flames',
      'Chicago Blackhawks', 'Colorado Avalanche', 'Dallas Stars', 'Edmonton Oilers',
      'Los Angeles Kings', 'Minnesota Wild', 'Nashville Predators', 'San Jose Sharks',
      'Seattle Kraken', 'St. Louis Blues', 'Vancouver Canucks', 'Vegas Golden Knights', 'Winnipeg Jets',
    ],
  },
  // MLB has no official East and West. This follows the NHL split: the two East divisions
  // are East, and the Central and West divisions are West.
  MLB: {
    east: [
      'Baltimore Orioles', 'Boston Red Sox', 'New York Yankees', 'Tampa Bay Rays', 'Toronto Blue Jays',
      'Atlanta Braves', 'Miami Marlins', 'New York Mets', 'Philadelphia Phillies', 'Washington Nationals',
    ],
    west: [
      'Chicago White Sox', 'Cleveland Guardians', 'Detroit Tigers', 'Kansas City Royals', 'Minnesota Twins',
      'Houston Astros', 'Los Angeles Angels', 'Oakland Athletics', 'Athletics', 'Sacramento Athletics',
      'Seattle Mariners', 'Texas Rangers',
      'Chicago Cubs', 'Cincinnati Reds', 'Milwaukee Brewers', 'Pittsburgh Pirates', 'St. Louis Cardinals',
      'Arizona Diamondbacks', 'Colorado Rockies', 'Los Angeles Dodgers', 'San Diego Padres', 'San Francisco Giants',
    ],
  },
  WNBA: {
    east: [
      'Atlanta Dream', 'Chicago Sky', 'Connecticut Sun', 'Indiana Fever', 'New York Liberty',
      'Washington Mystics', 'Toronto Tempo',
    ],
    west: [
      'Dallas Wings', 'Golden State Valkyries', 'Las Vegas Aces', 'Los Angeles Sparks',
      'Minnesota Lynx', 'Phoenix Mercury', 'Portland Fire', 'Seattle Storm',
    ],
  },
};
