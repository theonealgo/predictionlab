const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/pl-install.js'), 'utf8');

test('embedded panels do not create another install action or touch the parent', () => {
  const parent = { plInstallReady: true };
  const child = { self: {}, top: parent };
  vm.runInNewContext(source, { window: child });
  assert.equal(child.plInstallReady, undefined);
  assert.deepEqual(parent, { plInstallReady: true });
});

test('repeated main-page initialization remains harmless', () => {
  const window = { plInstallReady: true };
  window.self = window.top = window;
  vm.runInNewContext(source, { window });
  assert.equal(window.plInstallReady, true);
});
