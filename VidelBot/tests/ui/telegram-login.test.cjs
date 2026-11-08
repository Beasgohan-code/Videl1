// In-memory DOM and API. Telegram SDK and MongoDB are not contacted.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const {execFileSync}=require('node:child_process');
const cwd=path.resolve(__dirname,'../..');
const html=execFileSync(process.env.PYTHON||'python3',['-c','from webapp.ui_shell import render_app_html; print(render_app_html())'],{cwd,encoding:'utf8'});
const js=execFileSync(process.env.PYTHON||'python3',['-c','from webapp.ui_client import CLIENT_JS; print(CLIENT_JS)'],{cwd,encoding:'utf8'});
const json=(body,status=200)=>({status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>body});
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(r=>setTimeout(r,0))};
function setup(hash='',fail=false){
 const dom=new JSDOM(html,{url:'https://studio.example/app'+hash,runScripts:'outside-only'}),w=dom.window,calls=[];
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
 w.fetch=async(url,opts={})=>{
  if(url==='/api/v1/auth/login'){
   const body=JSON.parse(opts.body);calls.push(body);
   if(fail)return json({error:'Invalid or expired'},401);
   return json({session_token:'vs.synthetic',csrf_token:'csrf'});
  }
  if(url==='/api/v1/auth/me')return json({user:{display_name:'Synthetic',is_platform_admin:false},workspaces:[],settings:{},catalogs:{}});
  return json({error:'synthetic'},404);
 };
 return {dom,w,d:w.document,calls};
}
(async()=>{
 // Delayed SDK load: keep the Telegram fragment until the script has read it.
 const late=setup('#tgWebAppData=synthetic'); late.w.eval(js);
 assert.equal(late.calls.length,0);assert.equal(late.d.getElementById('auth-status').hidden,false);
 assert.equal(late.w.location.hash,'#tgWebAppData=synthetic');
 late.w.Telegram={WebApp:{initData:'signed-synthetic',initDataUnsafe:{user:{id:999}}}};
 late.d.getElementById('telegram-web-app').dispatchEvent(new late.w.Event('load'));await flush();
 assert.deepEqual(late.calls,[{init_data:'signed-synthetic'}]);assert.equal(late.w.sessionStorage.getItem('videl_saas_tok'),'vs.synthetic');
 assert.equal(late.d.getElementById('auth-screen').hidden,true);late.dom.window.close();
 // The SDK may restore launch data after loading even without a fragment.
 const restored=setup();restored.w.eval(js);await flush();assert.equal(restored.calls.length,0);
 restored.w.Telegram={WebApp:{initData:'signed-synthetic'}};
 restored.d.getElementById('telegram-web-app').dispatchEvent(new restored.w.Event('load'));await flush();
 assert.deepEqual(restored.calls,[{init_data:'signed-synthetic'}]);restored.dom.window.close();
 // Explicit sign-out prevents an automatic login on a subsequent page load.
 const signedOut=setup('#tgWebAppData=synthetic'); signedOut.w.sessionStorage.setItem('videl_telegram_signed_out','1');
 signedOut.w.Telegram={WebApp:{initData:'signed-synthetic'}};signedOut.w.eval(js);await flush();
 assert.equal(signedOut.calls.length,0);assert.equal(signedOut.d.getElementById('btn-telegram-login').hidden,false);
 signedOut.d.getElementById('btn-telegram-login').click();await flush();assert.deepEqual(signedOut.calls,[{init_data:'signed-synthetic'}]);
 assert.equal(signedOut.w.sessionStorage.getItem('videl_telegram_signed_out'),null);signedOut.dom.window.close();
 // A fresh Telegram launch in the same tab may sign in again after sign-out;
 // the same signed-out launch remains opted out. Timestamp is not identity.
 const same=setup('#tgWebAppData=synthetic');same.w.sessionStorage.setItem('videl_telegram_signed_out','100');
 same.w.Telegram={WebApp:{initData:'auth_date=100&hash=synthetic'}};same.w.eval(js);await flush();assert.equal(same.calls.length,0);same.dom.window.close();
 const fresh=setup('#tgWebAppData=synthetic');fresh.w.sessionStorage.setItem('videl_telegram_signed_out','100');
 fresh.w.Telegram={WebApp:{initData:'auth_date=101&hash=synthetic'}};fresh.w.eval(js);await flush();
 assert.deepEqual(fresh.calls,[{init_data:'auth_date=101&hash=synthetic'}]);assert.equal(fresh.w.sessionStorage.getItem('videl_telegram_signed_out'),null);fresh.dom.window.close();
 // A failed account load must not strand a newly created token in the tab.
 const broken=setup();broken.w.sessionStorage.setItem('videl_telegram_signed_out','1');broken.w.Telegram={WebApp:{initData:'signed-synthetic'}};
 const originalFetch=broken.w.fetch;broken.w.fetch=(url,opts)=>url==='/api/v1/auth/me'?Promise.resolve(json({error:'Unavailable'},503)):originalFetch(url,opts);
 broken.w.eval(js);await flush();broken.d.getElementById('btn-telegram-login').click();await flush();
 assert.equal(broken.w.sessionStorage.getItem('videl_saas_tok'),null);assert.equal(broken.w.sessionStorage.getItem('videl_telegram_signed_out'),'1');assert.equal(broken.d.getElementById('auth-screen').hidden,false);broken.dom.window.close();
 // Failed bootstrap after an automatic exchange leaves no token and stops a
 // reload from repeating the consumed launch.
 const bootFail=setup('#tgWebAppData=synthetic');bootFail.w.Telegram={WebApp:{initData:'auth_date=101&hash=synthetic'}};
 const fetchBoot=bootFail.w.fetch;bootFail.w.fetch=(url,opts)=>url==='/api/v1/auth/me'?Promise.resolve(json({error:'Unavailable'},503)):fetchBoot(url,opts);
 bootFail.w.eval(js);await flush();assert.equal(bootFail.w.sessionStorage.getItem('videl_saas_tok'),null);
 assert.equal(bootFail.w.sessionStorage.getItem('videl_telegram_signed_out'),'101');assert.match(bootFail.d.getElementById('auth-error').textContent,/workspace/i);bootFail.dom.window.close();
 // Invalid proof fails visibly, with no client-side identity or session.
 const bad=setup('#tgWebAppData=synthetic',true);bad.w.Telegram={WebApp:{initData:'invalid',initDataUnsafe:{user:{id:1}}}};bad.w.eval(js);await flush();
 assert.equal(bad.w.sessionStorage.getItem('videl_saas_tok'),null);assert.match(bad.d.getElementById('auth-error').textContent,/Reopen|reopen/);assert.equal(bad.d.getElementById('auth-screen').hidden,false);bad.dom.window.close();
 // Unsafe data without a raw signed proof is never submitted.
 const unsafe=setup();unsafe.w.Telegram={WebApp:{initData:'',initDataUnsafe:{user:{id:1}}}};unsafe.w.eval(js);await flush();assert.equal(unsafe.calls.length,0);unsafe.dom.window.close();
 // Existing one-time fragment still wins over an available Telegram proof.
 const link=setup('#l.synthetic');link.w.Telegram={WebApp:{initData:'other-proof'}};link.w.eval(js);await flush();
 assert.deepEqual(link.calls,[{link:'l.synthetic'}]);assert.equal(link.w.location.hash,'');link.dom.window.close();
 console.log('Telegram auto sign-in, delayed SDK, failure, unsafe-data rejection and one-time fallback passed (synthetic DOM/API).');
})().catch(error=>{console.error(error);process.exitCode=1});
