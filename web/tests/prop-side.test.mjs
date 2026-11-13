import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
const source = readFileSync(new URL('../src/propsView.js', import.meta.url), 'utf8');
const { matchesPropSide, filterPropResults, modelCounts } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('Both preserves all selections, Over and Under do not rewrite picks', () => {
  const rows = [{pick:'OVER'}, {pick:'under'}, {recorded_side:'OVER'}, {}];
  const before = JSON.stringify(rows);
  assert.equal(rows.filter(row => matchesPropSide(row)).length, 4);
  assert.equal(rows.filter(row => matchesPropSide(row, 'OVER')).length, 2);
  assert.equal(rows.filter(row => matchesPropSide(row, 'UNDER')).length, 1);
  assert.equal(JSON.stringify(rows), before);
});

test('historical grouped models match without overriding an explicit primary pick', () => {
  const forecasts = [{side:'OVER'}, {side:'UNDER'}];
  assert.equal(matchesPropSide({model_forecasts:forecasts}, 'OVER'), true);
  assert.equal(matchesPropSide({model_forecasts:forecasts}, 'UNDER'), true);
  assert.equal(matchesPropSide({pick:'UNDER', model_forecasts:forecasts}, 'OVER'), false);
});

test('side filtering composes with model and market filters and summary records', () => {
  const rows = [
    {pick:'OVER', model_id:'glicko2', prop_type:'points', result:'Win'},
    {pick:'UNDER', model_id:'glicko2', prop_type:'points', result:'Loss'},
    {pick:'OVER', model_id:'xgboost', prop_type:'rebounds', result:'Loss'},
  ];
  const filtered = filterPropResults(rows, {side:'OVER', model:'glicko2', market:'points'});
  assert.equal(filtered.length, 1);
  const [summary] = modelCounts(filtered, [{id:'glicko2'}]);
  assert.deepEqual([summary.wins, summary.losses, summary.win_rate], [1, 0, 100]);
  assert.deepEqual(filterPropResults(undefined, {side:'OVER'}), []);
});
