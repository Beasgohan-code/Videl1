// Synthetic intercepted API; no live Telegram or Mongo.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
const flags={filestore_enabled:true,batch_links:false,permanent_link:null,force_subscribe:true,force_sub_request_mode:false,auto_delete:false,protect_content:false,media_encoder:true,auto_watermark:false,group_moderation:false};
async function login(page,key='l.valid'){
 await page.goto(base+'/bots');await page.getByLabel('One-time key or link').fill(key);await page.getByRole('button',{name:'Continue to workspace'}).click();await page.getByRole('button',{name:'View saved features Slow workspace bot'}).waitFor();
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let mode='ok',reads=[];
   await page.route('**/api/v1/workspaces/*/bots/*/feature-summary',route=>{
    const req=route.request(),url=new URL(req.url());reads.push({method:req.method(),path:url.pathname});
    const respond=(status,value)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(mode==='old')return respond(404,{error:'old backend'});if(mode==='wrong')return respond(200,{summary:'features-v1',bot_id:'other_bot',features:flags});if(mode==='denied')return respond(403,{error:'private backend error'});if(mode==='bad')return respond(200,{summary:'features-v1',bot_id:'slow_bot',features:{...flags,media_encoder:'SECRET_BAD_FLAG'}});
    return respond(200,{summary:'features-v1',bot_id:'slow_bot',features:{...flags,unexpected:'SECRET_UNKNOWN_FIELD'},settings:{password:'SECRET_SETTING'},token:'SECRET_TOKEN'});
   });
   await login(page);assert.equal(reads.length,0);
   const trigger=page.getByRole('button',{name:'View saved features Slow workspace bot'});await trigger.focus();await page.keyboard.press('Enter');
   const panel=page.getByRole('region',{name:'Feature flags'});await panel.getByText('File Store',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`open panel overflow ${width}`);assert.equal(await panel.locator('li').count(),10);assert.match(await panel.locator('li').filter({hasText:'File Store'}).textContent(),/Enabled/);assert.match(await panel.locator('li').filter({hasText:'Batch links'}).textContent(),/Disabled/);assert.match(await panel.locator('li').filter({hasText:'Permanent links'}).textContent(),/Unavailable/);
   assert.equal(await page.getByText(/SECRET_/).count(),0);assert.equal(reads[0].method,'GET');assert.equal(reads[0].path,'/api/v1/workspaces/slow/bots/slow_bot/feature-summary');
   mode='bad';await panel.getByRole('button',{name:'Refresh features'}).focus();await page.keyboard.press('Enter');await panel.getByText('Could not load saved features.',{exact:false}).waitFor();assert.equal(await panel.getByRole('button',{name:'Try again'}).evaluate(e=>e===document.activeElement),true);assert.equal(await panel.locator('li').count(),0);assert.equal(await page.getByText('SECRET_BAD_FLAG').count(),0);
   mode='wrong';await panel.getByRole('button',{name:'Try again'}).click();await panel.getByText('Could not load saved features.',{exact:false}).waitFor();assert.equal(await panel.locator('li').count(),0);
   mode='old';await panel.getByRole('button',{name:'Try again'}).click();await panel.getByText('This summary is unavailable on this backend',{exact:false}).waitFor();
   mode='denied';await panel.getByRole('button',{name:'Try again'}).click();await panel.getByText('You no longer have access to these bot settings.').waitFor();assert.equal(await page.getByText('private backend error').count(),0);
   mode='ok';await panel.getByRole('button',{name:'Try again'}).click();await panel.getByText('File Store',{exact:true}).waitFor();
   await panel.getByRole('button',{name:'Close features'}).click();assert.equal(await panel.count(),0);await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='View saved features Slow workspace bot');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow ${width}`);await page.close();console.log(`Synthetic saved feature summary passed at ${width}px`);
  }
  const readonly=await browser.newPage();await readonly.goto(base+'/bots');await readonly.getByLabel('One-time key or link').fill('l.readonly');await readonly.getByRole('button',{name:'Continue to workspace'}).click();await readonly.getByRole('heading',{name:'Slow workspace bot'}).waitFor();assert.equal(await readonly.getByRole('button',{name:/View saved features/}).count(),0);await readonly.close();
  const race=await browser.newPage();let release,started;const gate=new Promise(r=>release=r),seen=new Promise(r=>started=r);
  await race.route('**/api/v1/workspaces/slow/bots/slow_bot/feature-summary',async route=>{started();await gate;try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({summary:'features-v1',bot_id:'slow_bot',features:flags})})}catch{}});
  await login(race);await race.getByRole('button',{name:'View saved features Slow workspace bot'}).click();await seen;await race.getByLabel('Select workspace').selectOption('bravo');await race.getByRole('heading',{name:'Bravo workspace bot'}).waitFor();release();await race.waitForTimeout(300);assert.equal(await race.getByRole('region',{name:'Feature flags'}).count(),0);await race.close();
  const signedOut=await browser.newPage();let releaseSession,startedSession;const sessionGate=new Promise(r=>releaseSession=r),sessionSeen=new Promise(r=>startedSession=r);
  await signedOut.route('**/api/v1/workspaces/slow/bots/slow_bot/feature-summary',async route=>{startedSession();await sessionGate;try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({summary:'features-v1',bot_id:'slow_bot',features:flags})})}catch{}});
  await login(signedOut);await signedOut.getByRole('button',{name:'View saved features Slow workspace bot'}).click();await sessionSeen;await signedOut.getByRole('button',{name:'Sign out'}).click();await signedOut.getByLabel('One-time key or link').fill('l.valid');await signedOut.getByRole('button',{name:'Continue to workspace'}).click();await signedOut.getByRole('button',{name:'View saved features Slow workspace bot'}).waitFor();releaseSession();await signedOut.waitForTimeout(300);assert.equal(await signedOut.getByRole('region',{name:'Feature flags'}).count(),0);await signedOut.close();
  console.log('Synthetic permission, stale workspace and signed-out session checks passed');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
