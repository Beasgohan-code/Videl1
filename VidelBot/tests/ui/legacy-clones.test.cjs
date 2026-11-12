// Synthetic DOM and API; no real Telegram or Mongo.
const assert=require('node:assert/strict'),path=require('node:path'),{JSDOM}=require('jsdom'),{execFileSync}=require('node:child_process');
const cwd=path.resolve(__dirname,'../..'),python=process.env.PYTHON||'python3';
const html=execFileSync(python,['-c','from webapp.ui_shell import render_app_html; print(render_app_html())'],{cwd,encoding:'utf8'});
const js=execFileSync(python,['-c','from webapp.ui_client import CLIENT_JS; print(CLIENT_JS)'],{cwd,encoding:'utf8'});
const json=(body,status=200)=>({status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>body});
const flush=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setTimeout(r,0))};
(async()=>{
 const dom=new JSDOM(html,{url:'https://studio.example/app',runScripts:'outside-only'}),w=dom.window,d=w.document;let reads=[],mode='pages',releaseDelay;const gate=new Promise(r=>releaseDelay=r);
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.sessionStorage.setItem('videl_saas_tok','vs.synthetic');
 w.fetch=async url=>{
  if(url==='/api/v1/auth/me')return json({user:{display_name:'Synthetic',is_platform_admin:false},workspaces:[{workspace_id:'ws',name:'Example'}],settings:{},catalogs:{}});
  if(url.includes('/bots?'))return json({bots:[]});
  if(url.startsWith('/api/v1/account/legacy-bots')){
   reads.push(url);if(mode==='deny')return json({error:'denied'},403);
   if(url==='/api/v1/account/legacy-bots')return json({bots:[{telegram_bot_id:'1',username:'<sample>',enabled:true}],truncated:true});
   if(mode==='old')return json({},404);if(mode==='malformed')return json({bots:[],pagination:'id-v1',next_after:'7'});
   if(mode==='failure')return json({},500);if(mode==='delay')await gate;
   return json(url.includes('?after=')?{bots:[{telegram_bot_id:'9223372036854775807',username:'last',enabled:false}],pagination:'id-v1',next_after:null}:{bots:[{telegram_bot_id:'1',username:'<sample>',enabled:true}],pagination:'id-v1',next_after:'1'});
  }
  if(url==='/api/v1/workspaces/ws')return json({workspace:{name:'Example'},my_role:'owner',my_permissions:{bot_settings:true,commands:true,cloning:true},usage:{},recent_notifications:[]});
  return json({},404)
 };
 w.eval(js);await flush();d.querySelector('[data-studio="bots"]').click();await flush();assert.equal(reads.length,0);
 const result=()=>d.getElementById('legacy-bots-result'), pages=()=>d.getElementById('legacy-bots-pages'), nav=dir=>pages().querySelector(`[data-page="${dir}"]`);const refresh=()=>d.getElementById('legacy-bots-load').click();
 refresh();await flush();assert.match(result().textContent,/@<sample>.*enabled in registration/);assert.equal(result().querySelector('sample'),null);
 assert.equal(nav('previous').getAttribute('aria-disabled'),'true');let count=reads.length;nav('previous').click();await flush();assert.equal(reads.length,count);
 nav('next').focus();nav('next').click();await flush();assert.equal(reads.at(-1),'/api/v1/account/legacy-bots/pages?after=1');assert.match(result().textContent,/@last.*disabled/);assert.equal(d.activeElement,nav('next'));assert.equal(nav('next').getAttribute('aria-disabled'),'true');
 count=reads.length;nav('next').click();await flush();assert.equal(reads.length,count);
 mode='failure';nav('previous').focus();nav('previous').click();await flush();assert.match(result().textContent,/Could not load this page/);assert.match(result().textContent,/@last/);assert.equal(d.activeElement,nav('previous'));
 mode='pages';nav('previous').click();await flush();assert.match(result().textContent,/@<sample>/);assert.equal(d.activeElement,nav('previous'));assert.equal(nav('previous').getAttribute('aria-disabled'),'true');
 mode='delay';nav('next').focus();nav('next').click();d.getElementById('bot-search').focus();releaseDelay();await flush();assert.equal(d.activeElement,d.getElementById('bot-search'));assert.match(result().textContent,/@last/);
 mode='pages';nav('previous').click();await flush();
 mode='deny';nav('next').focus();nav('next').click();await flush();assert.match(result().textContent,/Sign in with your own Telegram link/);assert.equal(d.activeElement,d.getElementById('legacy-bots-load'));assert.equal(pages().hidden,true);
 mode='pages';refresh();await flush();
 mode='malformed';refresh();await flush();assert.match(result().textContent,/Could not load this page/);
 mode='old';refresh();await flush();assert.match(result().textContent,/first 100 registrations/);assert.equal(pages().hidden,true);
 mode='deny';refresh();await flush();assert.match(result().textContent,/Sign in with your own Telegram link/);assert.doesNotMatch(result().textContent,/@<sample>/);
 dom.window.close();console.log('Synthetic legacy Studio pagination passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
