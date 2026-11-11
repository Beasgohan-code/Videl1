// Synthetic Next UI only: requests are intercepted; no live Telegram or Mongo.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try {for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let reads=0;
  await page.route('**/api/v1/account/legacy-bots',route=>{reads++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({bots:[{telegram_bot_id:'501',username:'sample_bot',enabled:true},{telegram_bot_id:'502',username:'',enabled:false}],truncated:true})})});
  await page.goto((process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview')+'/bots');await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();await page.getByRole('heading',{name:/Your bots/}).waitFor();
  assert.equal(reads,0);await page.getByRole('button',{name:'Show my Telegram clones'}).click();await page.getByText('@sample_bot — enabled in registration').waitFor();await page.getByText('Showing the first 100 registrations.').waitFor();assert.equal(reads,1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow ${width}`);
  await page.unroute('**/api/v1/account/legacy-bots');await page.route('**/api/v1/account/legacy-bots',route=>route.fulfill({status:403,contentType:'application/json',body:'{"error":"denied"}'}));
  await page.getByRole('button',{name:'Show my Telegram clones'}).click();await page.getByText('Sign in with your own Telegram link to see your clones.').waitFor();assert.equal(await page.getByText('@sample_bot — enabled in registration').count(),0);
  await page.getByRole('button',{name:'Connect a bot',exact:true}).click();await page.getByLabel('Display name').fill('Example');await page.getByLabel('BotFather token').fill('123:synthetic');
  await page.route('**/api/v1/workspaces/slow/bots',route=>route.request().method()==='POST'?route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'A valid ENCRYPTION_KEY is required before storing bot credentials.'})}):route.continue());
  await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('The backend needs its valid ENCRYPTION_KEY',{exact:false}).waitFor();assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic');
  await page.close();console.log(`Synthetic clone view and key guidance passed at ${width}px`);
 }}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
