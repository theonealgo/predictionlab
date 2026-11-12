import assert from 'node:assert/strict';
import { readPropHistory } from '../odds_engine/api/prop_history.js';

const body = {
  labels: ['MIN', '3PT', 'PTS'],
  names: ['minutes', 'threePointFieldGoalsMade-threePointFieldGoalsAttempted', 'points'],
  events: {},
  seasonTypes: [{ displayName: 'Regular Season', categories: [{ events: [] }] }],
};
for (let n = 1; n <= 6; n++) {
  body.events[n] = { gameDate: `2030-11-0${n}T18:00:00Z`, gameResult: 'W' };
  body.seasonTypes[0].categories[0].events.push({ eventId: String(n), stats: ['30', '2-7', '20'] });
}
const now = new Date('2030-11-10T12:00:00Z');
assert.deepEqual(readPropHistory(body, 'threes', now).recent_form, [2, 2, 2, 2, 2, 2]);
assert.deepEqual(readPropHistory(body, 'points', now).recent_form, [20, 20, 20, 20, 20, 20]);
body.events[7] = { gameDate: '2030-11-10T01:00:00Z', gameResult: 'W' };
body.seasonTypes[0].categories[0].events.push({ eventId: '7', stats: ['30', '0-4', '0'] });
assert.equal(readPropHistory(body, 'threes', now).recent_form[0], 0);
body.events[8] = { gameDate: '2030-11-10T20:00:00Z', gameResult: 'W' };
body.seasonTypes[0].categories[0].events.push({ eventId: '8', stats: ['30', '99-100', '999'] });
assert.equal(readPropHistory(body, 'threes', now).recent_form.length, 7);
assert.equal(readPropHistory(body, 'unknown', now), null);
body.seasonTypes[0].categories[0].events[0].stats[1] = '7-2';
assert.equal(readPropHistory(body, 'threes', now).recent_form.length, 6);
console.log('6 nested-history, actual-makes, zero, causal-date and invalid-stat assertions passed');
