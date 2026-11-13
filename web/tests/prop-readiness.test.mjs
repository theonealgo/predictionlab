import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
const source = readFileSync(new URL('../src/propsView.js', import.meta.url), 'utf8');
const { propsAvailabilityBadge } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('queued, building and new-day data are not presented as unavailable', () => {
  for (const status of ['queued', 'building', 'not_loaded']) {
    assert.equal(propsAvailabilityBadge([{count:null,status}]), 'Updating…');
  }
  assert.equal(propsAvailabilityBadge([]), 'Updating…');
  assert.equal(propsAvailabilityBadge([{count:400,status:'ready'}]), '(400 props)');
  assert.equal(propsAvailabilityBadge([{count:0,status:'no_fixtures'}]), '(0 props)');
  assert.equal(propsAvailabilityBadge([{count:null,status:'unsupported'}]), 'N/A');
});
