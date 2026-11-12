import assert from 'node:assert/strict';
import { selectLiveStripItems } from './src/liveStripItems.js';
const game = (sport, n, live = false, group = sport) => ({ sport, group, home: `${sport}${n}`, live });
const rows = [...Array.from({ length: 30 }, (_, n) => game('NCAAB', n)),
  ...Array.from({ length: 10 }, (_, n) => game('NBA', n)),
  ...Array.from({ length: 6 }, (_, n) => game('NHL', n, true)),
  ...Array.from({ length: 5 }, (_, n) => game('NHL', 20+n)),
  game('NBA', 99, true),
  ...Array.from({ length: 10 }, (_, n) => game('Tennis', n, false, `Tournament${n}`))];
const selected = selectLiveStripItems(rows);
assert.equal(selected.filter(row => row.sport === 'NCAAB').length, 4);
assert.equal(selected.filter(row => row.sport === 'NBA').length, 4);
assert.equal(selected.filter(row => row.sport === 'NHL').length, 6);
assert.equal(selected.filter(row => row.sport === 'Tennis').length, 4);
assert.equal(selected.filter(row => row.live).length, 7);
assert.equal(selected[0].live, true);
assert.equal(rows.length, 62);
assert.deepEqual(selectLiveStripItems(null), []);
assert.equal(selectLiveStripItems([game('NFL', 1)]).length, 1);
console.log('PASS: maximum four previews per sport across groups, all live games kept, live first, sparse/empty feeds safe');
