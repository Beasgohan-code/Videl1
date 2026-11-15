// Synthetic fixture and intercepted errors; no real Mongo or Telegram.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try {for(const width of [320,390,1440]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
  await page.goto(base+'/bots');await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();
  const card=page.locator('article').filter({has:page.getByRole('heading',{name:'Slow workspace bot'})});await card.waitFor();
  const paths=[];let seen;
  page.on('request',r=>{if(['POST','PATCH','DELETE'].includes(r.method())&&r.url().includes('/bots/'))paths.push(new URL(r.url()).pathname)});
  await page.route('**/api/v1/workspaces/slow/bots/*/lifecycle',route=>{seen=route.request().postDataJSON();return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'The record changed or is no longer available.',code:'lifecycle_conflict'})})});
  await card.getByRole('button',{name:'Delete',exact:true}).click();await card.getByText('Related data or other credentials may remain',{exact:false}).waitFor();await card.getByRole('button',{name:'Confirm delete'}).click();
  await page.getByText('This record changed or is no longer available.',{exact:false}).waitFor();assert.deepEqual(seen,{operation:'delete',expected_updated_ts:1,expected_archived:false});assert.equal(await card.getByRole('button',{name:'Confirm delete'}).count(),0);assert(paths.every(path=>path.endsWith('/lifecycle')));
  await card.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await page.getByLabel('Record name').fill('Unsaved draft');page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Refresh bot list'}).click();assert.equal(await page.getByLabel('Record name').inputValue(),'Unsaved draft');page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Refresh bot list'}).click();await card.waitFor();
  await page.unroute('**/api/v1/workspaces/slow/bots/*/lifecycle');
  await page.route('**/api/v1/workspaces/slow/bots/*/lifecycle',route=>route.fulfill({status:404,contentType:'application/json',body:'{"error":"Not found"}'}));
  await card.getByRole('button',{name:'Archive',exact:true}).click();await page.getByText('This backend does not support reviewed record changes.',{exact:false}).waitFor();assert(paths.every(path=>path.endsWith('/lifecycle')));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);await page.close();console.log(`Synthetic bot lifecycle conflicts and old backend: ${width}px`);
 }}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
