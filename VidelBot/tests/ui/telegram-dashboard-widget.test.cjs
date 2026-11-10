const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dir = path.resolve(__dirname, '../../plugins/widget/dashboard_assets');
const html = fs.readFileSync(path.join(dir, 'shell.html'), 'utf8')
  .replace('/*CSS*/', fs.readFileSync(path.join(dir, 'dashboard.css'), 'utf8'))
  .replace('/*JS*/', fs.readFileSync(path.join(dir, 'dashboard.js'), 'utf8'));
let ready = 0, queries = 0;
const dom = new JSDOM(html, { runScripts: 'dangerously', beforeParse(w) {
  w.TelegramWidget = { ready() { ready++; }, request() { queries++; throw Error('unexpected query'); } };
}});
const d = dom.window.document;
const tab = v => d.querySelector(`button[data-tab="${v}"]`).click();
const key = v => { const b = [...d.querySelectorAll('button[data-key]')].find(x => x.dataset.key === v); assert.ok(b, v); b.click(); };
const action = v => d.querySelector(`button[data-action="${v}"]`).click();
const val = () => d.getElementById('value').textContent;
assert.equal(ready, 1);
for (const k of ['2', '+', '3', '*', '4']) key(k);
assert.equal(val(), '14');
key('='); key('/'); key('0'); assert.equal(val(), '—');
key('C'); for (const k of ['(', '2', '+', '3', ')', '*', '4']) key(k); assert.equal(val(), '20');
tab('bill'); for (const k of ['1', '0', '0']) key(k); action('people');
assert.equal(val(), '55.00 each'); assert.match(d.getElementById('detail').textContent, /Total 110.00/);
action('tip'); assert.equal(val(), '57.50 each');
tab('unit'); key('1'); assert.equal(val(), '0.6214 mi'); action('swap'); assert.equal(val(), '1.6093 km');
action('unit'); action('unit'); key('C'); key('3'); key('2'); action('swap'); assert.equal(val(), '0 °C');
action('sign'); assert.equal(val(), '-35.5556 °C');
tab('count'); action('plus'); action('plus'); action('minus'); assert.equal(val(), '1'); action('reset'); assert.equal(val(), '0');
tab('bill'); assert.equal(val(), '57.50 each');
tab('calc'); assert.equal(val(), '20');
assert.equal(queries, 0);
assert.match(d.querySelector('.foot').textContent, /resets when reloaded/);
dom.window.close();
console.log('telegram dashboard widget: local calculator, split, units, counter and no queries passed');
