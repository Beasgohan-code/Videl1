// Synthetic API fixture and intercepted responses only; no live Telegram or Mongo.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function signIn(page){await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let mode='fail',reads=0,release,start;
   const gate=new Promise(r=>release=r),seen=new Promise(r=>start=r);
   await page.route('**/api/v1/workspaces/slow/bots?*',async route=>{
    reads++;
    if(mode==='fail')return route.fulfill({status:503,contentType:'application/json',body:'{"error":"internal synthetic diagnostic"}'});
    if(mode==='delayed'){start();await gate;}
    try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({bots:[{bot_id:'safe',display_name:'Recovered bot',archived:false,updated_ts:1,connection_state:'disconnected'}]})})}catch{}
   });
   await page.route('**/api/v1/account/legacy-bots/pages',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({pagination:'id-v1',bots:[{telegram_bot_id:'42',username:'my_clone',enabled:true}],next_after:null})}));
   await page.goto(base+'/bots');await signIn(page);
   await page.getByText('Could not load workspace bot records.',{exact:false}).waitFor();
   assert.equal(await page.getByText('internal synthetic diagnostic').count(),0);assert.equal(await page.getByText('No bot records yet').count(),0);
   await page.getByRole('button',{name:'Show my Telegram clones'}).click();await page.getByText('@my_clone — enabled in registration').waitFor();
   await page.getByRole('button',{name:'Connect a bot',exact:true}).click();await page.getByLabel('Display name').fill('Unsaved record');await page.getByLabel('BotFather token').fill('123:synthetic-secret');
   mode='delayed';const retry=page.getByRole('button',{name:'Refresh workspace bots'});await retry.focus();await page.keyboard.press('Enter');await seen;
   await page.getByText('Loading workspace bot records…').waitFor();assert.equal(await retry.getAttribute('aria-disabled'),'true');const previous=reads;await page.keyboard.press('Enter');assert.equal(reads,previous);
   assert.equal(await page.getByText('@my_clone — enabled in registration').count(),1);assert.equal(await page.getByLabel('Display name').inputValue(),'Unsaved record');release();await page.getByRole('heading',{name:'Recovered bot'}).waitFor();
   assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic-secret');assert.equal(await retry.evaluate(e=>e===document.activeElement),true);assert.equal(await page.getByText('@my_clone — enabled in registration').count(),1);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow ${width}`);await page.close();console.log(`Bot outage, clones and focused retry passed at ${width}px`);
  }
  const page=await browser.newPage();await page.route('**/api/v1/workspaces/*/bots?*',r=>r.fulfill({status:503,contentType:'application/json',body:'{"error":"private"}'}));
  await page.goto(base+'/commands');await signIn(page);await page.getByText('Could not load workspace bot records.',{exact:false}).waitFor();assert.equal(await page.getByText('No bot records yet').count(),0);assert.equal(await page.getByRole('heading',{name:/Give your bot/}).count(),0);
  await page.getByRole('link',{name:'File Store',exact:true}).click();await page.getByRole('heading',{name:/All your files/}).waitFor();
  await page.getByRole('link',{name:'Analytics',exact:true}).click();await page.getByText('Could not load workspace bot records.',{exact:false}).waitFor();assert.equal(await page.getByText('private',{exact:true}).count(),0);await page.close();console.log('Unrelated tools available and bot-dependent views fail honestly');
  const race=await browser.newPage();let started,release;const seen=new Promise(r=>started=r),gate=new Promise(r=>release=r);
  await race.route('**/api/v1/workspaces/slow/bots?*',async route=>{started();await gate;try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({bots:[{bot_id:'stale',display_name:'Stale workspace bot'}]})})}catch{}});
  await race.goto(base+'/bots');await signIn(race);await seen;await race.getByRole('button',{name:'Show my Telegram clones'}).waitFor();await race.getByLabel('Select workspace').selectOption('bravo');await race.getByRole('heading',{name:'Bravo workspace bot'}).waitFor();release();await race.waitForTimeout(300);assert.equal(await race.getByText('Stale workspace bot').count(),0);
  await race.getByRole('button',{name:'Sign out'}).click();assert.equal(await race.getByText('Bravo workspace bot').count(),0);await race.close();console.log('Delayed workspace read cannot cross workspaces or sign-out');
  const invalid=await browser.newPage();await invalid.route('**/api/v1/workspaces/slow/bots?*',r=>r.fulfill({status:200,contentType:'application/json',body:'{"bots":"bad"}'}));await invalid.goto(base+'/bots');await signIn(invalid);await invalid.getByText('Could not load workspace bot records.',{exact:false}).waitFor();assert.equal(await invalid.getByText('No bot records yet').count(),0);await invalid.close();console.log('Malformed bot list is not treated as empty');
  const denied=await browser.newPage();await denied.route('**/api/v1/workspaces/slow',r=>r.fulfill({status:403,contentType:'application/json',body:'{"error":"Denied"}'}));await denied.route('**/api/v1/workspaces/slow/bots?*',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({bots:[{bot_id:'private',display_name:'Hidden record'}]})}));await denied.goto(base+'/bots');await signIn(denied);await denied.getByText('Denied',{exact:false}).waitFor();assert.equal(await denied.getByText('Hidden record').count(),0);assert.equal(await denied.getByRole('button',{name:'Show my Telegram clones'}).count(),0);await denied.close();console.log('Denied workspace details do not render bot data');
  const again=await browser.newPage();let startedOld,releaseOld,attempts=0;const oldSeen=new Promise(r=>startedOld=r),oldGate=new Promise(r=>releaseOld=r);
  await again.route('**/api/v1/workspaces/slow/bots?*',async route=>{if(++attempts===1){startedOld();await oldGate;try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({bots:[{bot_id:'old',display_name:'Previous account record'}]})})}catch{}}else await route.continue()});
  await again.goto(base+'/bots');await signIn(again);await oldSeen;await again.getByRole('button',{name:'Sign out'}).click();await signIn(again);await again.getByRole('heading',{name:'Slow workspace bot'}).waitFor();releaseOld();await again.waitForTimeout(300);assert.equal(await again.getByText('Previous account record').count(),0);await again.close();console.log('Delayed read cannot cross a sign-out and new session');
  const expired=await browser.newPage();await expired.route('**/api/v1/workspaces/slow/bots?*',r=>r.fulfill({status:401,contentType:'application/json',body:'{"error":"Unauthorized"}'}));await expired.goto(base+'/bots');await signIn(expired);await expired.getByText('Your session has expired. Sign in again.').waitFor();assert.equal(await expired.getByText('No bot records yet').count(),0);await expired.close();console.log('Bot-list session expiry clears the session');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
