/* Synthetic gameplay tests. Instrumentation exposes closure state for test
 * drivers; production widget HTML contains no eval or test hook. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '../../plugins/game');
const shell = fs.readFileSync(path.join(root, 'shell.html'), 'utf8');
function load(key, storage) {
  const read = file => fs.readFileSync(path.join(root, file), 'utf8');
  let html;
  if (key === 'classic') {
    html = read('arcade.html').replace('/* CLASSIC_STYLES */', read('classic.css'))
                              .replace('/* CLASSIC_ENGINE */', read('classic.js'));
  } else {
    const css = read('arcade.css').replace('/* PALETTE */', '--accent:#9fd8ff') + (key === 'trio' ? read('tabs.css') : '');
    let code;
    if (key === 'trio') {
      let mounts = ['slide','mines','maze'].map((g,i)=>'function mount'+i+'(){'+read('games/'+g+'.js')+"document.getElementById('title').textContent=title;resetGame=start;start()}\n").join('');
      code = read('tabs.js').replace('/* TAB_MOUNTS */', mounts+'var mounts=[mount0,mount1,mount2];');
    } else code = read('games/' + key + '.js');
    const js = read('engine.js').replace('/* GAME_CODE */', code);
    html = (key === 'trio' ? shell.replace('<p id="status"','<div id="tabs"></div><p id="status"') : shell).replace('/* ARCADE_STYLES */', css).replace('/* ARCADE_ENGINE */', js)
                .replace('<!-- GENRE -->', 'TEST')
                .replace('<!-- ART -->', '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>');
  }
  html = html.replace(/\}\)\(\);\s*<\/script>/, 'window.__test=function(s){return eval(s)};})();</script>');
  let err = []; const vc = new VirtualConsole(); vc.on('jsdomError', e => err.push(e.message));
  let seed=123456789;
  const dom = new JSDOM(html, {runScripts:'dangerously', virtualConsole:vc, beforeParse(w){w.Math.random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296};if(key==='simon')w.setTimeout=fn=>{fn();return 1};if(storage)Object.defineProperty(w,'localStorage',{value:storage,configurable:true})}});
  assert.equal(err.length, 0, key+': '+err.join(','));
  assert.ok(dom.window.__test, key+' missing hook');
  return dom;
}
function buttons(d){return [...d.window.document.querySelectorAll('#board button')]}
function click(d,i){let b=buttons(d)[i];assert.ok(b,'button '+i);b.click()}
function info(d){return d.window.document.querySelector('#status').textContent}
function hook(d,s){return d.window.__test(s)}
function run(key, fn){let d=load(key);try{assert.ok(buttons(d).length,key);fn(d);d.window.document.querySelector('#restart').click();assert.ok(buttons(d).length,key+' restart')}finally{d.window.close()}console.log('ok',key)}
run('classic',d=>{
  click(d,0);assert.equal(buttons(d)[0].textContent,'X');
  const ends=new Set(),seen=new Set();function explore(seq){d.window.document.querySelector('#restart').click();seq.forEach(i=>click(d,i));let status=info(d),state=Array.from(hook(d,'board')).join('-');if(/You win|Bot wins|Draw/.test(status)){ends.add(status);return}if(seen.has(state))return;seen.add(state);Array.from(hook(d,'board')).forEach((v,i)=>{if(!v)explore(seq.concat(i))})}explore([]);assert.ok([...ends].some(x=>/You win/.test(x)));assert.ok([...ends].some(x=>/Bot wins/.test(x)));assert.ok([...ends].some(x=>/Draw/.test(x)));
  d.window.document.querySelector('#pairs').click();
  let pairs={};hook(d,'board').forEach((c,i)=>{(pairs[c.v]??=[]).push(i)});
  Object.values(pairs).forEach(([a,b])=>{click(d,a);click(d,b)});assert.match(info(d),/All pairs found/);
  d.window.document.querySelector('#lights').click();
  const a=Array.from(hook(d,'board'));let solution=-1;
  for(let mask=0;mask<512;mask++){let state=a.slice();for(let i=0;i<9;i++)if(mask&(1<<i))for(let j=0;j<9;j++)if(Math.abs(i%3-j%3)+Math.abs(Math.floor(i/3)-Math.floor(j/3))<=1)state[j]^=1;if(state.every(x=>!x)){solution=mask;break}}
  assert.ok(solution>=0);for(let i=0;i<9;i++)if(solution&(1<<i))click(d,i);assert.match(info(d),/All lights off/);
});
run('slide',d=>{let start=Array.from(hook(d,'a')).join(''),goal='123456780',q=[[start,[]]],seen=new Set([start]),sol;
  for(let k=0;k<q.length;k++){let [s,m]=q[k];if(s===goal){sol=m;break}let z=s.indexOf('0');for(let i=0;i<9;i++)if(Math.abs(i%3-z%3)+Math.abs(Math.floor(i/3)-Math.floor(z/3))===1){let v=s.split('');[v[i],v[z]]=[v[z],v[i]];let t=v.join('');if(!seen.has(t)){seen.add(t);q.push([t,m.concat(i)])}}}
  assert.ok(sol);sol.forEach(i=>click(d,i));assert.match(info(d),/Solved/)});
run('mines',d=>{click(d,0);let mines=Array.from(hook(d,'mines'));for(let i=1;i<16;i++)if(!mines.includes(i))click(d,i);assert.match(info(d),/cleared/);d.window.document.querySelector('#restart').click();click(d,0);click(d,hook(d,'mines[0]'));assert.match(info(d),/Mine hit/) });
run('sudoku',d=>{let a=Array.from(hook(d,'a')),fixed=Array.from(hook(d,'fixed'));function solve(i){if(i===16)return true;if(fixed[i])return solve(i+1);for(let v=1;v<=4;v++){let r=Math.floor(i/4),c=i%4,ok=true;for(let j=0;j<4;j++)if(a[r*4+j]===v||a[j*4+c]===v||a[(Math.floor(r/2)*2+Math.floor(j/2))*4+Math.floor(c/2)*2+j%2]===v)ok=false;if(ok){a[i]=v;if(solve(i+1))return true;a[i]=0}}return false}assert.ok(solve(0));for(let i=0;i<16;i++)if(!fixed[i])for(let n=0;n<a[i];n++)click(d,i);assert.match(info(d),/solved/i)});
run('maze',d=>{let paths=Array.from(hook(d,'paths')),q=[[0,[]]],seen=new Set([0]),solution;for(let k=0;k<q.length;k++){let [i,steps]=q[k];if(i===24){solution=steps;break}for(let j=0;j<25;j++)if(!seen.has(j)&&paths[Math.floor(j/5)][j%5]==='1'&&Math.abs(i%5-j%5)+Math.abs(Math.floor(i/5)-Math.floor(j/5))===1){seen.add(j);q.push([j,steps.concat(j)])}}assert.ok(solution);solution.forEach(i=>click(d,i));assert.match(info(d),/escaped/)});
run('merge',d=>{hook(d,'a=[64,64,0,0,0,0,0,0,0,0,0,0,0,0,0,0];draw()');click(d,16);assert.match(info(d),/128 reached/)});
run('connect',d=>{for(let n=0;n<42&&!hook(d,'done');n++){let a=Array.from(hook(d,'a')),c=a.slice(0,7).findIndex(v=>!v);while(hook(d,'column')!==c)click(d,2);click(d,1)}assert.ok(hook(d,'done'));assert.match(info(d),/win|draw/i)});
run('nim',d=>{click(d,0);while(!hook(d,'done')){let left=hook(d,'left');click(d,Math.min(3,left)-1)}assert.match(info(d),/win/i)});
run('rps',d=>{for(let i=0;i<100&&!hook(d,'done');i++)click(d,i%3);assert.ok(hook(d,'done'));assert.match(info(d),/wins?/i)});
run('hangman',d=>{let word=hook(d,'word');for(const c of new Set(word))click(d,'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.indexOf(c));assert.match(info(d),/Word found/)});
run('reversi',d=>{for(let i=0;i<30&&!hook(d,'done');i++){let opts=hook(d,"options('X')");assert.ok(opts.length);click(d,opts[0])}assert.ok(hook(d,'done'));assert.match(info(d),/win|draw/i)});
run('snake',d=>{click(d,3);clearInterval(hook(d,'timer'));[21,22,23,29,35].forEach((pos,i)=>{hook(d,'food='+pos);if(i===3)click(d,4);hook(d,'tick()')});assert.match(info(d),/grew to eight/)});
run('whack',d=>{click(d,9);clearInterval(hook(d,'timer'));for(let i=0;i<10;i++)click(d,hook(d,'target'));assert.match(info(d),/10 targets/);d.window.document.querySelector('#restart').click();click(d,9);clearInterval(hook(d,'timer'));for(let i=0;i<20;i++)hook(d,'tick()');assert.match(info(d),/Time up/)});
run('reaction',d=>{click(d,0);assert.match(info(d),/Too early/);d.window.document.querySelector('#restart').click();hook(d,"phase='go';when=Date.now();draw()");click(d,0);assert.match(info(d),/ms/)});
run('numbers',d=>{for(let n=1;n<=16;n++){let i=buttons(d).findIndex(b=>b.textContent===String(n));click(d,i)}assert.match(info(d),/Complete/)});
run('simon',d=>{while(!hook(d,'done')){let seq=Array.from(hook(d,'seq'));seq.forEach(i=>click(d,i))}assert.match(info(d),/completed/)});
run('math',d=>{while(!hook(d,'done')){let correct=hook(d,'correct'),i=buttons(d).findIndex(b=>b.textContent===String(correct));click(d,i)}assert.match(info(d),/You win/)});
run('scramble',d=>{while(!hook(d,'done')){let a=hook(d,'answer'),i=buttons(d).findIndex(b=>b.textContent===a);click(d,i)}assert.match(info(d),/You win/)});
run('trivia',d=>{while(!hook(d,'done')){let a=hook(d,'questions[round][1]'),i=buttons(d).findIndex(b=>b.textContent===a);click(d,i)}assert.match(info(d),/You win/)});
run('higher',d=>{for(let i=0;i<8;i++)click(d,0);assert.ok(hook(d,'done'));assert.match(info(d),/Score/)});
run('code',d=>{let secret=Array.from(hook(d,'secret'));let wrong=Array.from(secret).reverse();if(wrong.join()===secret.join())wrong=[1,2,3];wrong.forEach(x=>click(d,x-1));click(d,7);assert.match(info(d),/Last/);secret.forEach(x=>click(d,x-1));click(d,7);assert.match(info(d),/cracked/)});
run('towers',d=>{[[0,2],[0,1],[2,1],[0,2],[1,0],[1,2],[0,2]].forEach(([a,b])=>{click(d,a);click(d,b)});assert.match(info(d),/complete in 7 moves/i)});
run('flood',d=>{for(let n=0;n<20&&!hook(d,'done');n++){let b=Array.from(hook(d,'board')),r=Array.from(hook(d,'region'));let boundary=b.findIndex((v,i)=>!r[i]&&[i-4,i+4,i%4?i-1:-1,i%4<3?i+1:-1].some(j=>r[j]));assert.ok(boundary>=0);click(d,boundary)}assert.match(info(d),/Board flooded/)});
run('dots',d=>{for(let n=0;n<12&&!hook(d,'done');n++){let options=Array.from(hook(d,'candidates()'));assert.ok(options.length);click(d,options[0])}assert.ok(hook(d,'done'));assert.match(info(d),/win|draw/i)});
run('trio',d=>{let tabs=[...d.window.document.querySelectorAll('#tabs button')];assert.equal(tabs.length,3);assert.match(d.window.document.querySelector('#title').textContent,/Sliding/i);tabs[1].click();assert.match(d.window.document.querySelector('#title').textContent,/Minefield/i);click(d,0);tabs[2].click();assert.match(d.window.document.querySelector('#title').textContent,/Maze/i);d.window.document.querySelector('#restart').click();assert.match(d.window.document.querySelector('#title').textContent,/Maze/i);tabs[0].click();assert.equal(tabs[0].getAttribute('aria-pressed'),'true')});
// Personal bests are per-message/device, optional, and explicitly clearable.
const saved = new Map();
const store = {getItem:k=>saved.has(k)?saved.get(k):null, setItem:(k,v)=>saved.set(k,String(v)), removeItem:k=>saved.delete(k)};
let d=load('math',store);
while(!hook(d,'done')){let correct=hook(d,'correct'),i=buttons(d).findIndex(b=>b.textContent===String(correct));click(d,i)}
assert.equal(saved.get('videl:math'),'10');assert.match(info(d),/Personal best: 10/);d.window.close();
d=load('math',store);assert.equal(hook(d,'record'),10);
while(!hook(d,'done')){let correct=hook(d,'correct'),i=buttons(d).findIndex(b=>b.textContent!==String(correct)&&b.textContent!=='Clear best');click(d,i)}
assert.equal(saved.get('videl:math'),'10');d.window.document.getElementById('best-clear').click();assert.equal(saved.has('videl:math'),false);d.window.close();
d=load('numbers',store);assert.equal(hook(d,"saveBest('numbers',1500,true)"),1500);assert.equal(hook(d,"saveBest('numbers',2000,true)"),1500);assert.equal(hook(d,"saveBest('numbers',1000,true)"),1000);assert.equal(saved.get('videl:numbers'),'1000');d.window.close();
const blocked={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')},removeItem(){throw Error('blocked')}};
d=load('math',blocked);while(!hook(d,'done')){let correct=hook(d,'correct'),i=buttons(d).findIndex(b=>b.textContent===String(correct));click(d,i)}assert.match(info(d),/You win/);assert.doesNotMatch(info(d),/Personal best/);d.window.close();
console.log('26 unique widget games, local tabs and local-best storage: synthetic gameplay passed');
