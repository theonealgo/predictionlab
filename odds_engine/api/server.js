import Fastify from 'fastify';
import axios from 'axios';
import pg from 'pg';
import Redis from 'ioredis';

const app = Fastify({ logger: true });

const PORT = process.env.PORT || 7000;
const MODEL_URL = process.env.MODEL_URL || 'http://localhost:7001/predict';
const VIG = parseFloat(process.env.VIG || '0.04');

const pool = new pg.Pool({
  host: process.env.PGHOST || 'localhost',
  port: process.env.PGPORT ? parseInt(process.env.PGPORT, 10) : 5432,
  user: process.env.PGUSER || 'odds',
  password: process.env.PGPASSWORD || 'odds',
  database: process.env.PGDATABASE || 'odds_engine',
});

const redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379');

const roundToHalf = (num) => Math.round(num * 2) / 2;
const americanFromProb = (p) => {
  if (!p || p <= 0 || p >= 1) return null;
  if (p >= 0.5) {
    return -Math.round((p / (1 - p)) * 100);
  }
  return Math.round(((1 - p) / p) * 100);
};

const unitsFromAmerican = (odds) => {
  if (odds === null || odds === undefined) return null;
  if (odds > 0) return odds / 100;
  return 100 / Math.abs(odds);
};

const applyVig = (pHome, pAway, vig) => {
  const total = pHome + pAway;
  const ph = total > 0 ? pHome / total : 0.5;
  const pa = total > 0 ? pAway / total : 0.5;
  const vigFactor = 1 + vig;
  return {
    home: Math.min(ph * vigFactor, 0.99),
    away: Math.min(pa * vigFactor, 0.99),
  };
};

const getGameById = async (gameId) => {
  const { rows } = await pool.query('SELECT * FROM games WHERE game_id = $1', [gameId]);
  return rows[0];
};

const getTeamStats = async (sport, team) => {
  const { rows } = await pool.query(
    'SELECT offense, defense FROM team_stats WHERE sport = $1 AND team = $2',
    [sport, team]
  );
  return rows[0];
};

const callModel = async (payload) => {
  const { data } = await axios.post(MODEL_URL, payload, { timeout: 8000 });
  return data;
};

const upsertModelOutput = async (gameId, model) => {
  await pool.query(
    `INSERT INTO model_outputs
      (game_id, win_prob_home, win_prob_away, expected_home_score, expected_away_score, spread, total)
     VALUES ($1,$2,$3,$4,$5,$6,$7)
     ON CONFLICT (game_id) DO UPDATE SET
       win_prob_home = EXCLUDED.win_prob_home,
       win_prob_away = EXCLUDED.win_prob_away,
       expected_home_score = EXCLUDED.expected_home_score,
       expected_away_score = EXCLUDED.expected_away_score,
       spread = EXCLUDED.spread,
       total = EXCLUDED.total,
       created_at = NOW()`,
    [
      gameId,
      model.win_prob_home,
      model.win_prob_away,
      model.expected_home_score,
      model.expected_away_score,
      model.spread,
      model.total,
    ]
  );
};

const upsertOddsLine = async (gameId, odds) => {
  await pool.query(
    `INSERT INTO odds_lines
      (game_id, moneyline_home, moneyline_away, spread, spread_price_home, spread_price_away, total, total_over_price, total_under_price, vig, source)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
     ON CONFLICT (game_id) DO UPDATE SET
       moneyline_home = EXCLUDED.moneyline_home,
       moneyline_away = EXCLUDED.moneyline_away,
       spread = EXCLUDED.spread,
       spread_price_home = EXCLUDED.spread_price_home,
       spread_price_away = EXCLUDED.spread_price_away,
       total = EXCLUDED.total,
       total_over_price = EXCLUDED.total_over_price,
       total_under_price = EXCLUDED.total_under_price,
       vig = EXCLUDED.vig,
       source = EXCLUDED.source,
       created_at = NOW()`,
    [
      gameId,
      odds.moneyline_home,
      odds.moneyline_away,
      odds.spread,
      odds.spread_price_home,
      odds.spread_price_away,
      odds.total,
      odds.total_over_price,
      odds.total_under_price,
      odds.vig,
      odds.source,
    ]
  );
};

const buildOdds = (modelOutput, vig) => {
  const implied = applyVig(modelOutput.win_prob_home, modelOutput.win_prob_away, vig);
  const moneyline_home = americanFromProb(implied.home);
  const moneyline_away = americanFromProb(implied.away);

  const rawSpread = modelOutput.expected_home_score - modelOutput.expected_away_score;
  const spread = rawSpread >= 0 ? -roundToHalf(rawSpread) : roundToHalf(Math.abs(rawSpread));
  const total = roundToHalf(modelOutput.expected_home_score + modelOutput.expected_away_score);

  const spreadHomeProb = modelOutput.cover_prob_home ?? modelOutput.win_prob_home;
  const spreadAwayProb = 1 - spreadHomeProb;
  const spreadImp = applyVig(spreadHomeProb, spreadAwayProb, vig);

  const totalOverProb = modelOutput.over_prob ?? 0.5;
  const totalUnderProb = 1 - totalOverProb;
  const totalImp = applyVig(totalOverProb, totalUnderProb, vig);

  return {
    moneyline_home,
    moneyline_away,
    spread,
    spread_price_home: americanFromProb(spreadImp.home),
    spread_price_away: americanFromProb(spreadImp.away),
    total,
    total_over_price: americanFromProb(totalImp.home),
    total_under_price: americanFromProb(totalImp.away),
    vig,
    source: 'engine',
  };
};

const getOrCreateOdds = async (game, bypassCache = false) => {
  const cacheKey = `odds:${game.game_id}`;
  if (!bypassCache) {
    const cached = await redis.get(cacheKey);
    if (cached) return JSON.parse(cached);
  }

  const homeStats = await getTeamStats(game.sport, game.home_team);
  const awayStats = await getTeamStats(game.sport, game.away_team);
  const modelPayload = {
    sport: game.sport,
    home_team: game.home_team,
    away_team: game.away_team,
    home_stats: homeStats,
    away_stats: awayStats,
  };

  const modelOutput = await callModel(modelPayload);
  await upsertModelOutput(game.game_id, modelOutput);

  const odds = buildOdds(modelOutput, VIG);
  await upsertOddsLine(game.game_id, odds);
  await redis.set(cacheKey, JSON.stringify(odds), 'EX', 300);
  return odds;
};

app.get('/health', async () => ({ ok: true }));

app.get('/games', async (request) => {
  const sport = request.query.sport;
  const { rows } = await pool.query(
    sport
      ? 'SELECT * FROM games WHERE sport = $1 ORDER BY start_time ASC'
      : 'SELECT * FROM games ORDER BY start_time ASC'
    ,
    sport ? [sport] : []
  );
  return { games: rows };
});

app.get('/model', async (request, reply) => {
  try {
    const { gameId, sport, home, away } = request.query;
    if (gameId) {
      const game = await getGameById(gameId);
      if (!game) return reply.code(404).send({ error: 'game not found' });
      const homeStats = await getTeamStats(game.sport, game.home_team);
      const awayStats = await getTeamStats(game.sport, game.away_team);
      return await callModel({
        sport: game.sport,
        home_team: game.home_team,
        away_team: game.away_team,
        home_stats: homeStats,
        away_stats: awayStats,
      });
    }
    if (sport && home && away) {
      return await callModel({ sport, home_team: home, away_team: away });
    }
    return reply.code(400).send({ error: 'gameId or sport/home/away required' });
  } catch (err) {
    request.log.error(err);
    return reply.code(500).send({ error: 'model error' });
  }
});

app.get('/odds', async (request, reply) => {
  try {
    const { gameId, sport, home, away } = request.query;
    if (gameId) {
      const game = await getGameById(gameId);
      if (!game) return reply.code(404).send({ error: 'game not found' });
      const odds = await getOrCreateOdds(game);
      return { gameId, odds };
    }
    if (sport && home && away) {
      const tempGame = {
        game_id: `${sport}_${home}_${away}`.replace(/\s+/g, '_'),
        sport,
        home_team: home,
        away_team: away,
      };
      const odds = await getOrCreateOdds(tempGame, true);
      return { gameId: tempGame.game_id, odds, transient: true };
    }
    return reply.code(400).send({ error: 'gameId or sport/home/away required' });
  } catch (err) {
    request.log.error(err);
    return reply.code(500).send({ error: 'odds error' });
  }
});

app.post('/bet', async (request, reply) => {
  try {
    const { gameId, market, side, stake } = request.body || {};
    if (!gameId || !market || !side || !stake) {
      return reply.code(400).send({ error: 'gameId, market, side, stake required' });
    }
    const game = await getGameById(gameId);
    if (!game) return reply.code(404).send({ error: 'game not found' });

    const odds = await getOrCreateOdds(game);
    const price =
      market === 'moneyline'
        ? (side === 'home' ? odds.moneyline_home : odds.moneyline_away)
        : market === 'spread'
          ? (side === 'home' ? odds.spread_price_home : odds.spread_price_away)
          : (side === 'over' ? odds.total_over_price : odds.total_under_price);

    await pool.query(
      'INSERT INTO bets (game_id, market, side, stake, price) VALUES ($1,$2,$3,$4,$5)',
      [gameId, market, side, stake, price]
    );

    const liability = stake * (unitsFromAmerican(price) || 1);
    const { rows } = await pool.query(
      'SELECT * FROM risk_exposure WHERE game_id = $1 AND market = $2',
      [gameId, market]
    );
    const current = rows[0];
    const fields = {
      home_liability: current?.home_liability || 0,
      away_liability: current?.away_liability || 0,
      over_liability: current?.over_liability || 0,
      under_liability: current?.under_liability || 0,
    };
    if (market === 'moneyline') {
      if (side === 'home') fields.home_liability += liability;
      else fields.away_liability += liability;
    } else if (market === 'spread') {
      if (side === 'home') fields.home_liability += liability;
      else fields.away_liability += liability;
    } else {
      if (side === 'over') fields.over_liability += liability;
      else fields.under_liability += liability;
    }

    if (current) {
      await pool.query(
        `UPDATE risk_exposure
         SET home_liability=$1, away_liability=$2, over_liability=$3, under_liability=$4, updated_at=NOW()
         WHERE id=$5`,
        [
          fields.home_liability,
          fields.away_liability,
          fields.over_liability,
          fields.under_liability,
          current.id,
        ]
      );
    } else {
      await pool.query(
        `INSERT INTO risk_exposure
          (game_id, market, home_liability, away_liability, over_liability, under_liability)
         VALUES ($1,$2,$3,$4,$5,$6)`,
        [
          gameId,
          market,
          fields.home_liability,
          fields.away_liability,
          fields.over_liability,
          fields.under_liability,
        ]
      );
    }

    const totalLiability =
      market === 'total'
        ? fields.over_liability + fields.under_liability
        : fields.home_liability + fields.away_liability;
    const imbalance =
      market === 'total'
        ? Math.abs(fields.over_liability - fields.under_liability)
        : Math.abs(fields.home_liability - fields.away_liability);

    if (totalLiability > 0 && imbalance / totalLiability > 0.25) {
      const reason = `exposure shift on ${market}`;
      const prevLine = JSON.stringify(odds);
      const newOdds = { ...odds };
      if (market === 'moneyline') {
        newOdds.moneyline_home -= 10;
        newOdds.moneyline_away += 10;
      } else if (market === 'spread') {
        newOdds.spread = roundToHalf(newOdds.spread - 0.5);
      } else {
        newOdds.total = roundToHalf(newOdds.total + 0.5);
      }
      await upsertOddsLine(gameId, newOdds);
      await pool.query(
        'INSERT INTO line_movement (game_id, market, prev_line, new_line, reason) VALUES ($1,$2,$3,$4,$5)',
        [gameId, market, prevLine, JSON.stringify(newOdds), reason]
      );
      await redis.del(`odds:${gameId}`);
    }

    return { ok: true, price, odds };
  } catch (err) {
    request.log.error(err);
    return reply.code(500).send({ error: 'bet error' });
  }
});

app.get('/risk', async (request) => {
  const { gameId } = request.query;
  const { rows } = await pool.query(
    gameId
      ? 'SELECT * FROM risk_exposure WHERE game_id = $1'
      : 'SELECT * FROM risk_exposure',
    gameId ? [gameId] : []
  );
  return { exposure: rows };
});

app.listen({ port: PORT, host: '0.0.0.0' });
