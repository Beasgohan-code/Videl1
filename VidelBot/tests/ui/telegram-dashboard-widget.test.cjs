const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dir = path.resolve(__dirname, '../../plugins/widget/dashboard_assets');
const data = { days: Array.from({length: 90}, (_, i) => `day-${i+1}`), active: Array(90).fill(4), new: Array(90).fill(2), jobs: Array(90).fill(15), stars: Array(90).fill(7), generated: '2026-04-01 00:00' };
const html = fs.readFileSync(path.join(dir, 'shell.html'), 'utf8')
  .replace('/*CSS*/', fs.readFileSync(path.join(dir, 'dashboard.css'), 'utf8'))
  .replace('/*DATA*/', JSON.stringify(data))
  .replace('/*JS*/', fs.readFileSync(path.join(dir, 'dashboard.js'), 'utf8'));
let ready = 0, queries = 0;
const dom = new JSDOM(html, { runScripts: 'dangerously', beforeParse(w) {
  w.TelegramWidget = { ready() { ready++; }, request() { queries++; throw Error('unexpected query'); } };
}});
const d = dom.window.document;
const metric = v => d.querySelector(`button[data-metric="${v}"]`).click();
const range = v => d.querySelector(`button[data-range="${v}"]`).click();
const text = id => d.getElementById(id).textContent;
assert.equal(ready, 1); assert.equal(text('total'), '120'); assert.equal(text('peak'), '4');
assert.match(text('detail'), /not unique people/); assert.equal(d.querySelectorAll('.bar').length, 30);
metric('new'); range(7); assert.equal(text('total'), '14'); assert.equal(d.querySelectorAll('.bar').length, 7);
metric('jobs'); assert.equal(text('total'), '105'); range(90); assert.equal(text('total'), '1,350');
assert.equal(d.querySelectorAll('.bar').length, 30); assert.match(text('detail'), /3 days/);
metric('stars'); range(30); assert.equal(text('total'), '210'); assert.match(text('detail'), /not profit/);
assert.equal(text('from'), 'day-61'); assert.equal(text('to'), 'day-90'); assert.match(text('stamp'), /UTC/);
assert.match(d.querySelector('.foot').textContent, /snapshot/i); assert.equal(queries, 0);
dom.window.close();
console.log('telegram dashboard widget: real snapshot metrics, ranges, graphs and no queries passed');
