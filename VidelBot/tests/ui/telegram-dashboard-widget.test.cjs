const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dir = path.resolve(__dirname, '../../plugins/widget/dashboard_assets');
const data = { days: Array.from({length: 90}, (_, i) => `day-${i+1}`), active: Array(90).fill(4), new: Array(90).fill(2), jobs: Array(90).fill(15), stars: Array(90).fill(7), generated: '2026-04-01 00:00', default_range: 7 };
// Exercise a zero baseline and an increasing/decreasing recorded trend.
data.jobs.fill(5, 76, 83); data.jobs.fill(10, 83); data.new.fill(0, 76, 90);
data.active.fill(0, 76, 83); data.stars.fill(14, 76, 83);
const html = fs.readFileSync(path.join(dir, 'shell.html'), 'utf8')
  .replace('/*CSS*/', fs.readFileSync(path.join(dir, 'dashboard.css'), 'utf8'))
  .replace('/*JS*/', fs.readFileSync(path.join(dir, 'dashboard.js'), 'utf8'))
  .replace('/*DATA*/', JSON.stringify(data));
let ready = 0, queries = 0;
const dom = new JSDOM(html, { runScripts: 'dangerously', beforeParse(w) {
  w.TelegramWidget = { ready() { ready++; }, request() { queries++; throw Error('unexpected query'); } };
}});
const d = dom.window.document;
const metric = v => d.querySelector(`button[data-metric="${v}"]`).click();
const range = v => d.querySelector(`button[data-range="${v}"]`).click();
const step = v => d.querySelector(`button[data-step="${v}"]`).click();
const text = id => d.getElementById(id).textContent;
assert.equal(ready, 1); assert.equal(text('total'), '28'); assert.equal(text('peak'), '4');
assert.equal(text('compare'), 'New activity vs prior 0'); assert.match(text('detail'), /not unique people/);
assert.equal(d.querySelectorAll('.bar').length, 7); assert.equal(text('selected'), 'day-90 · 4');
step(-1); assert.equal(text('selected'), 'day-89 · 4'); step(1); assert.equal(text('selected'), 'day-90 · 4');
assert.equal(d.querySelector('button[data-step="1"]').getAttribute('aria-disabled'), 'true');
metric('jobs'); assert.equal(text('total'), '70'); assert.equal(text('compare'), 'Up 100.0% vs prior 7 days');
metric('new'); assert.equal(text('total'), '0'); assert.equal(text('compare'), 'No change vs prior 7 days');
metric('stars'); assert.equal(text('compare'), 'Down 50.0% vs prior 7 days');
metric('new');
range(90); assert.equal(d.querySelectorAll('.bar').length, 30); assert.match(text('detail'), /3 days/);
assert.equal(text('compare'), 'Prior 90 days unavailable'); assert.equal(text('selected'), 'day-88 – day-90 · 0');
for(let i=0;i<40;i++)step(-1); assert.equal(text('selected'), 'day-1 – day-3 · 6');
assert.equal(d.querySelector('button[data-step="-1"]').getAttribute('aria-disabled'),'true');
metric('stars'); range(30); assert.equal(text('total'), '259'); assert.match(text('detail'), /not profit/);
assert.equal(text('from'), 'day-61'); assert.equal(text('to'), 'day-90'); assert.match(text('stamp'), /UTC/);
assert.match(d.querySelector('.foot').textContent, /partial today/); assert.equal(queries, 0);
dom.window.close();

// Exercise the compact representation the Python sender actually emits,
// including UTC date rollover and values beyond signed 32-bit arithmetic.
function pack(nums) {
  const bytes=[];
  for(let n of nums) { while(n>=128){bytes.push((n%128)+128); n=Math.floor(n/128)} bytes.push(n); }
  return Buffer.from(bytes).toString('base64');
}
function packed(nums) {
  const buckets=[]; for(let i=0;i<90;i+=3)buckets.push(nums[i]+nums[i+1]+nums[i+2]);
  return {r:pack(nums.slice(-30)),g:pack(buckets),p:nums.slice(-60,-30).reduce((a,b)=>a+b,0),x:Math.max(...nums)};
}
const big=Array(90).fill(4294967297), compact={days:'2025-12-01',generated:'2026-03-01 00:00',default_range:90,
  active:packed(big),new:packed(Array(90).fill(0)),jobs:packed(big),stars:packed(Array(90).fill(0))};
const encoded = fs.readFileSync(path.join(dir,'shell.html'),'utf8')
  .replace('/*CSS*/',fs.readFileSync(path.join(dir,'dashboard.css'),'utf8'))
  .replace('/*JS*/',fs.readFileSync(path.join(dir,'dashboard.js'),'utf8'))
  .replace('/*DATA*/',JSON.stringify(compact));
const second=new JSDOM(encoded,{runScripts:'dangerously',beforeParse(w){w.TelegramWidget={ready(){}}}});
const sd=second.window.document;
assert.equal(sd.getElementById('total').textContent,'386,547,056,730');
assert.equal(sd.getElementById('peak').textContent,'4,294,967,297');
assert.equal(sd.getElementById('from').textContent,'2025-12-01');
assert.equal(sd.getElementById('to').textContent,'2026-02-28');
assert.equal(sd.getElementById('selected').textContent,'2026-02-26 – 2026-02-28 · 12,884,901,891');
second.window.close();

const tiny=Array(90).fill(1000000000); tiny[89]++;
const subtle={days:'2025-12-01',generated:'2026-03-01 00:00',default_range:7,
 active:packed(tiny),new:packed(tiny),jobs:packed(tiny),stars:packed(tiny)};
const tinyHtml=fs.readFileSync(path.join(dir,'shell.html'),'utf8')
 .replace('/*CSS*/',fs.readFileSync(path.join(dir,'dashboard.css'),'utf8'))
 .replace('/*JS*/',fs.readFileSync(path.join(dir,'dashboard.js'),'utf8'))
 .replace('/*DATA*/',JSON.stringify(subtle));
const third=new JSDOM(tinyHtml,{runScripts:'dangerously',beforeParse(w){w.TelegramWidget={ready(){}}}});
assert.equal(third.window.document.getElementById('compare').textContent,'Up <0.1% vs prior 7 days');
third.window.close();

console.log('telegram dashboard: trends, zero baselines, period inspection, ranges, compact data and no queries passed');
