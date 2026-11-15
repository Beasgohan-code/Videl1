// Synthetic signed proof, real local aiohttp auth code and mock Mongo; never live Telegram.
const {chromium}=require('playwright-core');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:8088';
function proof(offset=0){
 const fields={auth_date:String(Math.floor(Date.now()/1000)+offset),query_id:'synthetic-browser-launch',user:JSON.stringify({id:87654,first_name:'Synthetic'})};
 const secret=crypto.createHmac('sha256','WebAppData').update('123456:TEST').digest();
 fields.hash=crypto.createHmac('sha256',secret).update(Object.keys(fields).sort().map(k=>`${k}=${fields[k]}`).join('\n')).digest('hex');
 return new URLSearchParams(fields).toString();
}
(async()=>{
 let signed=proof();const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await b.newPage({viewport:{width:390,height:844}}),statuses=[];
  await page.route('https://telegram.org/js/telegram-web-app.js',route=>route.fulfill({status:200,contentType:'text/javascript',body:`window.Telegram={WebApp:{initData:${JSON.stringify(signed)},initDataUnsafe:{user:{id:1}}}}`}));
  page.on('response',r=>{if(r.url().endsWith('/api/v1/auth/login'))statuses.push(r.status())});
  await page.goto(base+'/app#tgWebAppData=synthetic');await page.locator('#app-shell').waitFor({state:'visible'});
  assert.deepEqual(statuses,[200]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.reload();await page.locator('#app-shell').waitFor({state:'visible'});assert.deepEqual(statuses,[200]);
  await page.locator('#btn-logout').click();await page.locator('#auth-screen').waitFor({state:'visible'});
  await page.reload();await page.locator('#btn-telegram-login').waitFor({state:'visible'});assert.deepEqual(statuses,[200]);
  await page.locator('#btn-telegram-login').click();await page.locator('#auth-error').waitFor({state:'visible'});
  assert.match(await page.locator('#auth-error').innerText(),/reopen/i);assert.deepEqual(statuses,[200,401]);
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('videl_saas_tok')),null);
  signed=proof(1);await page.reload();await page.locator('#app-shell').waitFor({state:'visible'});assert.deepEqual(statuses,[200,401,200]);
  await page.close();console.log('Synthetic signed login, reload, logout, replay rejection and fresh launch passed against local backend + mock Mongo');
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
