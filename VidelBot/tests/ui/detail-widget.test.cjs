const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dir = path.resolve(__dirname, '../../plugins/widget/detail_assets');
const data = { kicker:'VIDEL · GROUP', title:'Example', subtitle:'Verified now', foot:'Snapshot · /info group to refresh', sections:[
  {label:'Overview',rows:[{label:'Group ID',value:'-10055',copy:true},{label:'CPU',value:'12.4%',percent:12.4}]},
  {label:'Capacity',rows:[{label:'RAM total',value:'100 MiB'}]},
  {label:'About',rows:[{label:'Description',value:'first',pages:['first','<img src=x onerror=alert(1)>','third']}]}]};
const html = fs.readFileSync(path.join(dir,'shell.html'),'utf8')
  .replace('/*CSS*/',fs.readFileSync(path.join(dir,'detail.css'),'utf8'))
  .replace('/*JS*/',fs.readFileSync(path.join(dir,'detail.js'),'utf8'))
  .replace('/*DATA*/',JSON.stringify(data).replace(/</g,'\\u003c'));
let ready=0, queries=0, copied=[];
const dom=new JSDOM(html,{runScripts:'dangerously',beforeParse(w){w.TelegramWidget={ready(){ready++},request(){queries++;throw Error('unexpected query')},copyText(v){copied.push(v)}}}});
const d=dom.window.document, tab=i=>d.querySelector(`button[data-index="${i}"]`).click();
assert.equal(ready,1);assert.equal(d.querySelector('#title').textContent,'Example');
assert.equal(d.querySelectorAll('.row').length,2);assert.equal(d.querySelector('.fill').style.width,'12.4%');
d.querySelector('button[data-copy="0"]').click();assert.deepEqual(copied,['-10055']);assert.match(d.querySelector('#notice').textContent,/requested/);
tab(1);assert.equal(d.querySelector('.value').textContent,'100 MiB');
tab(2);assert.equal(d.querySelector('.value').textContent,'first');assert.equal(d.querySelector('.pager span').textContent,'1 / 3');
d.querySelector('button[data-page="1"]').click();assert.equal(d.querySelector('.value').textContent,'<img src=x onerror=alert(1)>');
assert.equal(d.querySelector('#rows img'),null);d.querySelector('button[data-page="1"]').click();assert.equal(d.querySelector('.value').textContent,'third');
assert.equal(d.querySelector('button[data-page="1"]').getAttribute('aria-disabled'),'true');
d.querySelector('button[data-page="1"]').click();assert.equal(d.querySelector('.value').textContent,'third');
tab(0);tab(2);assert.equal(d.querySelector('.value').textContent,'first');
assert.equal(queries,0);assert.match(d.querySelector('#foot').textContent,/Snapshot/);dom.window.close();
const unsupported=new JSDOM(html,{runScripts:'dangerously',beforeParse(w){w.TelegramWidget={ready(){}}}});
unsupported.window.document.querySelector('button[data-copy="0"]').click();
assert.match(unsupported.window.document.querySelector('#notice').textContent,/unavailable/);unsupported.window.close();
console.log('detail widget: tabs, gauges, paged safe text, copy request and zero queries passed');
