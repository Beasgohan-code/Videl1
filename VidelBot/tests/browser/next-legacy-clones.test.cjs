// Synthetic Next UI and intercepted API, not live Telegram or Mongo.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try {for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let reads=[],mode='pages';
  await page.route('**/api/v1/account/legacy-bots**',route=>{
   const url=new URL(route.request().url());reads.push(url.pathname+url.search);
   const fulfill=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(mode==='deny')return fulfill({error:'denied'},403);
   if(url.pathname.endsWith('/legacy-bots'))return fulfill({bots:[{telegram_bot_id:'1',username:'<sample>',enabled:true}],truncated:true});
   if(mode==='old')return fulfill({},404);if(mode==='malformed')return fulfill({bots:[],pagination:'id-v1',next_after:'7'});if(mode==='failure')return fulfill({},500);
   return fulfill(url.search?{pagination:'id-v1',bots:[{telegram_bot_id:'9223372036854775807',username:'last',enabled:false}],next_after:null}:{pagination:'id-v1',bots:[{telegram_bot_id:'1',username:'<sample>',enabled:true}],next_after:'1'});
  });
  await page.goto((process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview')+'/bots');await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();await page.getByRole('heading',{name:/Your bots/}).waitFor();
  assert.equal(reads.length,0);await page.getByRole('button',{name:'Show my Telegram clones'}).click();await page.getByText('@<sample> — enabled in registration').waitFor();assert.equal(await page.locator('sample').count(),0);
  await page.getByRole('button',{name:'Next page'}).focus();await page.keyboard.press('Enter');await page.getByText('@last — disabled in registration').waitFor();assert.equal(reads.at(-1),'/api/v1/account/legacy-bots/pages?after=1');
  mode='failure';await page.getByRole('button',{name:'Previous page'}).click();await page.getByText('Could not load this page.',{exact:false}).waitFor();assert.equal(await page.getByText('@last — disabled in registration').count(),1);
  mode='pages';await page.getByRole('button',{name:'Previous page'}).click();await page.getByText('@<sample> — enabled in registration').waitFor();
  mode='malformed';await page.getByRole('button',{name:'Refresh my Telegram clones'}).click();await page.getByText('Could not load this page.',{exact:false}).waitFor();
  mode='old';await page.getByRole('button',{name:'Refresh my Telegram clones'}).click();await page.getByText('Showing the first 100 registrations.',{exact:false}).waitFor();assert.equal(await page.getByRole('button',{name:'Next page'}).count(),0);
  mode='deny';await page.getByRole('button',{name:'Refresh my Telegram clones'}).click();await page.getByText('Sign in with your own Telegram link to see your clones.').waitFor();assert.equal(await page.getByText('@<sample> — enabled in registration').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow ${width}`);
  await page.getByRole('button',{name:'Connect a bot',exact:true}).click();await page.getByLabel('Display name').fill('Example');await page.getByLabel('BotFather token').fill('123:synthetic');
  await page.route('**/api/v1/workspaces/slow/bots',route=>route.request().method()==='POST'?route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'A valid ENCRYPTION_KEY is required before storing bot credentials.'})}):route.continue());
  await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('The backend needs its valid ENCRYPTION_KEY',{exact:false}).waitFor();assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic');
  await page.close();console.log(`Synthetic clone pagination and key guidance passed at ${width}px`);
 }
 const p=await browser.newPage();let release,started;const gate=new Promise(r=>release=r),seen=new Promise(r=>started=r);
 await p.route('**/api/v1/account/legacy-bots/pages',async route=>{started();await gate;try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({pagination:'id-v1',bots:[{telegram_bot_id:'42',username:'stale_account',enabled:true}],next_after:null})})}catch{}});
 await p.goto((process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview')+'/bots');await p.getByLabel('One-time key or link').fill('l.valid');await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByRole('button',{name:'Show my Telegram clones'}).click();await seen;
 await p.getByRole('button',{name:'Sign out'}).click();await p.getByLabel('One-time key or link').fill('l.valid');await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByRole('button',{name:'Show my Telegram clones'}).waitFor();release();await p.waitForTimeout(250);assert.equal(await p.getByText('@stale_account — enabled in registration').count(),0);await p.close();console.log('Synthetic delayed response after sign-out passed.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
