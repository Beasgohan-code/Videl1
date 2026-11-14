// Synthetic intercepted responses; no live Telegram or Mongo.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let response='This bot is already registered in a workspace.',status=400;
  await page.route('**/api/v1/workspaces/slow/bots',route=>route.request().method()==='POST'?route.fulfill({status,contentType:'application/json',body:JSON.stringify({error:response})}):route.continue());
  await page.goto(base+'/bots');await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();
  await page.getByRole('button',{name:'Connect a bot',exact:true}).click();await page.getByLabel('Display name').fill('My record');await page.getByLabel('BotFather token').fill('123:synthetic-secret');
  await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('This bot is already registered.',{exact:false}).waitFor();
  assert.equal(await page.getByLabel('Display name').inputValue(),'My record');assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic-secret');
  response='Unable to encrypt credentials. This request did not save the bot; ask the operator to check the original ENCRYPTION_KEY.';status=400;await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('The backend could not safely encrypt this token.',{exact:false}).waitFor();assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic-secret');
  response='private backend secret';status=500;await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('The result may be uncertain',{exact:false}).waitFor();assert.equal(await page.getByText('private backend secret').count(),0);
  response='This Telegram bot is already registered in a workspace.';status=400;await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('This bot is already registered.',{exact:false}).waitFor();
  response='The backend cannot verify safe bot registration. Ask the operator to check database indexes. No record was created by this request.';status=503;await page.getByRole('button',{name:'Register bot',exact:true}).click();await page.getByText('Ask the operator to check database indexes.',{exact:false}).waitFor();assert.equal(await page.getByLabel('BotFather token').inputValue(),'123:synthetic-secret');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);await page.close();console.log(`Synthetic duplicate and uncertain bot registration passed at ${width}px`);
 }}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
