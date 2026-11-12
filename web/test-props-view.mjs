import assert from 'node:assert/strict';
import { filterPropResults, modelCounts, propTypeCounts } from './src/propsView.js';

const rows = [
  { player_name: 'Legacy', model_id: null, prop_type: 'points', result: 'N/A', recorded_result: 'HIT' },
  { player_name: 'Player One', model_id: 'trueskill', prop_type: 'points', result: 'Win' },
  { player_name: 'Player Two', model_id: 'trueskill', prop_type: 'points', result: 'Loss' },
  { player_name: 'Player Three', model_id: 'trueskill', prop_type: 'assists', result: 'Push' },
];
const models = [{ id: 'trueskill', name: 'Takedown' }, { id: 'xgboost', name: 'XSharp' }];
const selected = filterPropResults(rows, { model: 'trueskill' });
assert.equal(selected.length, 3);
assert.equal(propTypeCounts(selected).reduce((sum, item) => sum + item.recorded, 0), selected.length);
const stats = modelCounts(selected, models)[0];
assert.deepEqual([stats.wins, stats.losses, stats.pushes, stats.decided, stats.win_rate], [1, 1, 1, 2, 50]);
const missing = filterPropResults(rows, { model: 'xgboost' });
assert.equal(missing.length, 0);
assert.equal(propTypeCounts(missing).length, 0);
assert.equal(modelCounts(missing, models)[1].win_rate, null);
assert.equal(filterPropResults(rows, { model: 'trueskill', market: 'assists' }).length, 1);
assert.equal(filterPropResults(rows, { search: 'player two' }).length, 1);
assert.equal(modelCounts([rows[0]], models)[0].captured, 0);
console.log('PASS: model/prop/player filters use the same cohort; pushes excluded; legacy grades not cloned.');
