// Synthetic DOM/API test, not live Telegram or Mongo.
const assert=require('node:assert/strict'),path=require('node:path'),{JSDOM}=require('jsdom'),{execFileSync}=require('node:child_process');
const cwd=path.resolve(__dirname,'../..'),python=process.env.PYTHON||'python3';
const html=execFileSync(python,['-c','from webapp.ui_shell import render_app_html; print(render_app_html())'],{cwd,encoding:'utf8'});
const js=execFileSync(python,['-c','from webapp.ui_client import CLIENT_JS; print(CLIENT_JS)'],{cwd,encoding:'utf8'});
const json=(body,status=200)=>({status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>body});
const flush=async()=>{for(let i=0;i<20;i++)await new Promise(r=>setTimeout(r,0))};
(async()=>{
 const dom=new JSDOM(html,{url:'https://studio.example/app',runScripts:'outside-only'}),w=dom.window,d=w.document;let reads=0,deny=false;
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.sessionStorage.setItem('videl_saas_tok','vs.synthetic');
 w.fetch=async url=>{
  if(url==='/api/v1/auth/me')return json({user:{display_name:'Synthetic',is_platform_admin:false},workspaces:[{workspace_id:'ws',name:'Example'}],settings:{},catalogs:{}});
  if(url.includes('/bots?'))return json({bots:[]});
  if(url==='/api/v1/account/legacy-bots'){reads++;return deny?json({error:'denied'},403):json({bots:[{telegram_bot_id:'1',username:'<sample>',enabled:true}],truncated:true})}
  if(url==='/api/v1/workspaces/ws')return json({workspace:{name:'Example'},my_role:'owner',my_permissions:{bot_settings:true,commands:true,cloning:true},usage:{},recent_notifications:[]});
  return json({},404)
 };
 w.eval(js);await flush();d.querySelector('[data-studio="bots"]').click();await flush();assert.equal(reads,0);
 d.getElementById('legacy-bots-load').click();await flush();assert.equal(reads,1);assert.match(d.getElementById('legacy-bots-result').textContent,/@<sample>.*enabled in registration/);assert.equal(d.getElementById('legacy-bots-result').querySelector('sample'),null);assert.match(d.getElementById('legacy-bots-result').textContent,/first 100/);
 deny=true;d.getElementById('legacy-bots-load').click();await flush();assert.match(d.getElementById('legacy-bots-result').textContent,/Sign in with your own Telegram link/);dom.window.close();console.log('Synthetic legacy Studio clone view passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
