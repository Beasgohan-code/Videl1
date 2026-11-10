const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dir = path.resolve(__dirname, '../../plugins/widget/detail_assets');
const data = { kicker:'VIDEL · SYSTEM', title:'Host resources', subtitle:'Captured now', foot:'Snapshot · /sys to refresh', sections:[
  {label:'Resources',rows:[{label:'CPU',value:'12.4%',percent:12.4},{label:'RAM',value:'37.5%',percent:37.5}]},
  {label:'Capacity',rows:[{label:'RAM total',value:'100 MiB'}]},
  {label:'Runtime',rows:[{label:'Host uptime',value:'2:00:00'},{label:'Name',value:'<img src=x onerror=alert(1)>'}]}]};
const html = fs.readFileSync(path.join(dir,'shell.html'),'utf8')
  .replace('/*CSS*/',fs.readFileSync(path.join(dir,'detail.css'),'utf8'))
  .replace('/*DATA*/',JSON.stringify(data).replace(/</g,'\\u003c'))
  .replace('/*JS*/',fs.readFileSync(path.join(dir,'detail.js'),'utf8'));
let ready=0, queries=0;
const dom=new JSDOM(html,{runScripts:'dangerously',beforeParse(w){w.TelegramWidget={ready(){ready++},request(){queries++;throw Error('unexpected query')}}}});
const d=dom.window.document, tab=i=>d.querySelector(`button[data-index="${i}"]`).click();
assert.equal(ready,1);assert.equal(d.querySelector('#title').textContent,'Host resources');
assert.equal(d.querySelectorAll('.row').length,2);assert.equal(d.querySelector('.fill').style.width,'12.4%');
tab(1);assert.equal(d.querySelector('.value').textContent,'100 MiB');
tab(2);assert.equal(d.querySelectorAll('.row')[0].querySelector('.value').textContent,'2:00:00');
assert.equal(d.querySelectorAll('.row')[1].querySelector('.value').textContent,'<img src=x onerror=alert(1)>');
assert.equal(d.querySelector('#rows img'),null);assert.equal(queries,0);
assert.match(d.querySelector('#foot').textContent,/Snapshot/);dom.window.close();
console.log('detail widget: tabs, real gauges, safe text and zero queries passed');
