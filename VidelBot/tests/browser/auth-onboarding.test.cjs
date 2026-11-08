// Actual served auth UI against the local mock-Mongo backend; no real Telegram.
const {chromium}=require('playwright-core');const assert=require('node:assert/strict');const path=require('node:path');
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
for(const width of [320,390,768,1440]){
 const p=await b.newPage({viewport:{width,height:900},reducedMotion:'reduce'}),requests=[];
 await p.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:""}};'}));
 await p.route('**/api/v1/auth/login',r=>{requests.push(r.request().postDataJSON());return r.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:'Key expired'})})});
 await p.goto('http://127.0.0.1:8088/app');await p.getByRole('heading',{name:'Welcome to Studio'}).waitFor();
 assert.match(await p.locator('#auth-screen').innerText(),/first visit/i);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 const input=p.locator('#auth-token-input');await input.fill('l.synthetic');await input.press('Enter');
 await p.locator('#auth-error').waitFor({state:'visible'});assert.deepEqual(requests,[{link:'l.synthetic'}]);
 assert.equal(await p.locator('#btn-login-submit').isEnabled(),true);assert.equal(await p.getByRole('button',{name:'Launch Demo Workspace'}).count(),0);
 if(process.env.VIDEL_TEST_SCREENSHOTS_DIR&&(width===390||width===1440))await p.screenshot({path:path.join(process.env.VIDEL_TEST_SCREENSHOTS_DIR,`videl-auth-${width}.png`),fullPage:true});
 await p.close();console.log(`Signup guidance and browser key at ${width}px passed`);
}
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
