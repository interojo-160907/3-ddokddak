const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname + '/Code.gs', 'utf8'), context);
for (const [latest, installed, expected] of [
  ['2.9.6', '2.9.9', false], ['2.9.9', '2.9.9', false],
  ['v2.9.10', '2.9.9', true], ['2.9.9', 'v2.9.6', true],
  ['2.9', '2.9.0', false], ['2.10.0', '2.9.99', true],
  ['', '2.9.9', false], ['invalid', '2.9.9', false],
  ['2.9.9', 'unknown', false]
]) {
  assert.equal(context.newerVersion_(latest, installed), expected,
    `${latest} / ${installed}`);
}
console.log('PASS: numeric version comparison never requests a downgrade');
