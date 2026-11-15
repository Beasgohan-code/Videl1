// Synthetic fixture only; no real MongoDB, multi-worker deployment or Telegram.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(page){
 await page.goto(base+'/commands');await page.getByLabel('One-time key or link').fill('l.valid');
 await page.getByRole('button',{name:'Continue to workspace'}).click();
 await page.getByRole('heading',{name:/Give your bot/}).waitFor();
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});await login(page);
   const writes=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/commands'))writes.push({path:new URL(r.url()).pathname,body:r.postDataJSON()})});
   await page.getByLabel('Command name').fill('Hello');await page.getByLabel('Description',{exact:true}).fill('Original');
   await page.getByRole('button',{name:'Create record'}).click();await page.getByRole('heading',{name:'/hello'}).waitFor();
   assert.deepEqual(writes,[{path:'/api/v1/workspaces/slow/bots/slow_bot/commands/create',body:{command:'hello',description:'Original',category:'Custom',permission:'everyone',visibility:'public',enabled:true}}]);
   await page.getByLabel('Command name').fill('hello');await page.getByLabel('Description',{exact:true}).fill('Do not overwrite');
   await page.getByRole('button',{name:'Create record'}).click();await page.getByText('A command with this name already exists.',{exact:false}).waitFor();
   assert.equal(await page.getByLabel('Description',{exact:true}).inputValue(),'Do not overwrite');
   assert.equal(await page.getByText('Original',{exact:true}).count(),1);
   assert(writes.every(w=>w.path.endsWith('/commands/create')),'creation must never use legacy upsert');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
   await page.close();console.log(`Create-only command and collision checks passed at ${width}px`);
  }
  const old=await browser.newPage();
  await old.route('**/api/v1/workspaces/slow/bots/slow_bot/commands',async route=>{const response=await route.fetch();const json=await response.json();delete json.capabilities;await route.fulfill({response,json})});
  await login(old);await old.getByText('Safe command creation is unavailable',{exact:false}).waitFor();
  await old.getByLabel('Command name').fill('draft');await old.getByLabel('Description',{exact:true}).fill('Keep this draft');
  assert.equal(await old.getByRole('button',{name:'Create record'}).isDisabled(),true);
  assert.equal(await old.getByLabel('Command name').inputValue(),'draft');await old.close();
  const mixed=await browser.newPage();await login(mixed);const mixedWrites=[];mixed.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/commands'))mixedWrites.push(new URL(r.url()).pathname)});
  await mixed.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/create',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));
  await mixed.getByLabel('Command name').fill('draft');await mixed.getByLabel('Description',{exact:true}).fill('Mixed worker');
  await mixed.getByRole('button',{name:'Create record'}).click();await mixed.getByText('This server cannot safely create command records.',{exact:false}).waitFor();
  assert.deepEqual(mixedWrites,['/api/v1/workspaces/slow/bots/slow_bot/commands/create']);
  assert.equal(await mixed.getByLabel('Description',{exact:true}).inputValue(),'Mixed worker');await mixed.close();
  const uncertain=await browser.newPage();await login(uncertain);
  await uncertain.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/create',route=>route.fulfill({status:500,contentType:'application/json',body:'{"error":"failure"}'}));
  await uncertain.getByLabel('Command name').fill('maybe');await uncertain.getByLabel('Description',{exact:true}).fill('Keep draft');
  await uncertain.getByRole('button',{name:'Create record'}).click();await uncertain.getByText('result may be uncertain',{exact:false}).waitFor();
  assert.equal(await uncertain.getByLabel('Command name').inputValue(),'maybe');await uncertain.close();
  const mismatch=await browser.newPage();await login(mismatch);
  await mismatch.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/create',route=>route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({command:{command:'other',description:'Wrong',category:'Custom',permission:'everyone',visibility:'public',enabled:true,handler_bound:false}})}));
  await mismatch.getByLabel('Command name').fill('draft');await mismatch.getByLabel('Description',{exact:true}).fill('Keep this');
  await mismatch.getByRole('button',{name:'Create record'}).click();await mismatch.getByText('result may be uncertain',{exact:false}).waitFor();
  assert.equal(await mismatch.getByLabel('Command name').inputValue(),'draft');await mismatch.close();
  console.log('Older servers, mixed workers and uncertain results retain drafts without legacy create writes');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
