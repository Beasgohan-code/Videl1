// Signed synthetic launch, real local aiohttp app, in-memory mock Mongo.
const {chromium}=require('playwright-core');const crypto=require('node:crypto');const assert=require('node:assert/strict');const path=require('node:path');
function signed(user,query,offset=0){const fields={auth_date:String(Math.floor(Date.now()/1000)+offset),query_id:query,user:JSON.stringify({id:user,first_name:'New Member'})};const secret=crypto.createHmac('sha256','WebAppData').update('123456:TEST').digest();fields.hash=crypto.createHmac('sha256',secret).update(Object.keys(fields).sort().map(k=>`${k}=${fields[k]}`).join('\n')).digest('hex');return new URLSearchParams(fields).toString()}
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
const seed=crypto.randomInt(100000000,200000000);
for(const width of [320,390,768,1440]){
 const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'}),uid=seed+width;let data=signed(uid,'launch-one-'+crypto.randomBytes(6).toString('hex'));
 await page.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:`window.Telegram={WebApp:{initData:${JSON.stringify(data)}}}`}));
 const loginResponse=page.waitForResponse(r=>r.url().endsWith('/api/v1/auth/login')&&r.status()===200);
 await page.goto('http://127.0.0.1:8088/app#tgWebAppData=synthetic');await page.locator('#app-shell').waitFor({state:'visible'});await page.locator('#overview-hero-title, .cc-header h1').waitFor();
 const login=await (await loginResponse).json();assert.equal(login.user.user_id,uid);assert.equal(login.user.is_platform_admin,false);assert.equal(login.workspaces.length,1);assert.equal(login.workspaces[0].owner_id,uid);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 if(process.env.VIDEL_TEST_SCREENSHOTS_DIR&&(width===390||width===1440))await page.screenshot({path:path.join(process.env.VIDEL_TEST_SCREENSHOTS_DIR,`videl-first-run-${width}.png`),fullPage:true});
 await page.locator('[data-goto="bot_create"]').first().click();await page.locator('#studio-viewport h1').filter({hasText:/bot creation studio|connect a bot/i}).waitFor();
 if(width===390){const previous=login.workspaces[0].workspace_id;if(!await page.locator('#btn-logout').isVisible())await page.locator('#profile-menu summary').click();await page.locator('#btn-logout').click();await page.locator('#auth-screen').waitFor({state:'visible'});data=signed(uid,'launch-two-'+crypto.randomBytes(6).toString('hex'),1);const again=page.waitForResponse(r=>r.url().endsWith('/api/v1/auth/login')&&r.status()===200);await page.reload();await page.locator('#app-shell').waitFor({state:'visible'});const result=await (await again).json();assert.equal(result.workspaces[0].workspace_id,previous)}
 await page.close();console.log(`Synthetic first signup, workspace and bot setup at ${width}px passed`);
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
