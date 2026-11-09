const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync(path.resolve(__dirname, '../../plugins/widget/calculator.html'), 'utf8');
const requests = [];
let publicChange;
const telegramWidget = {
  canQuery: true,
  publicData: null,
  ready() {},
  watchPublicData() {},
  onEvent(type, callback) { if (type === 'publicDataChanged') publicChange = callback; },
  async request(data) {
    requests.push(data);
    const expression = data.slice(2);
    const result = expression === '2+3*4' ? '14' : expression === '' ? '0' : '4';
    const state = { ok: true, expression, result };
    this.publicData = JSON.stringify({ expression, result, revision: String(requests.length) });
    return { result: JSON.stringify(state) };
  },
};

async function main() {
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    beforeParse(window) { window.TelegramWidget = telegramWidget; },
  });
  const { document } = dom.window;
  const press = key => document.querySelector(`button[data-key="${key}"]`).click();
  for (const key of ['2', '+', '3', '*', '4']) press(key);
  assert.equal(document.getElementById('expr').textContent, '2+3*4');
  assert.equal(document.getElementById('value').textContent, '14');
  press('=');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(requests, ['c:2+3*4']);
  assert.equal(document.getElementById('value').textContent, '14');
  assert.equal(document.getElementById('status').textContent, 'Shared result updated');

  telegramWidget.publicData = JSON.stringify({ expression: '8/2', result: '4', revision: 'other' });
  publicChange();
  assert.equal(document.getElementById('expr').textContent, '8/2');
  assert.equal(document.getElementById('value').textContent, '4');
  press('C');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(requests.at(-1), 'c:');
  assert.equal(document.getElementById('value').textContent, '0');
  dom.window.close();
  console.log('calculator widget: local arithmetic, query sync and remote shared-state refresh passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
