// In-memory synthetic API and Chromium: not a real Mongo, Telegram or device test.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(page){
 await page.goto(base+'/settings');
 await page.getByLabel('One-time key or link').fill('l.valid');
 await page.getByRole('button',{name:'Continue to workspace'}).click();
 await page.getByLabel('Compact tables').waitFor();
}
async function density(page,expected){
 await page.waitForFunction(value=>document.querySelector('.shell')?.dataset.compactTables===value,expected?'true':'false');
}
async function openTable(page){
 await page.getByRole('link',{name:'Analytics',exact:true}).click();
 await page.getByText('View daily data').click();
 await page.getByRole('cell',{name:'2026-10-01'}).waitFor();
 return page.locator('.analytics-details td').first();
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900}});
   await login(page); await density(page,false);
   await page.getByLabel('Compact tables').check(); await density(page,false); // Draft is not active.
   await page.getByRole('button',{name:'Save preferences'}).click();
   await page.getByText('Preferences saved for your account.').waitFor(); await density(page,true);
   let cell=await openTable(page);
   assert.equal(await cell.evaluate(el=>getComputedStyle(el).paddingTop),'5px');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);
   await page.getByLabel('Select workspace').selectOption('bravo');
   await page.getByText('84',{exact:true}).waitFor(); await density(page,true);
   await page.reload(); await page.getByText('84',{exact:true}).waitFor(); await density(page,true);
   await page.getByRole('link',{name:'Personal settings',exact:true}).click();
   await page.getByLabel('Compact tables').uncheck(); await density(page,true);
   await page.getByRole('button',{name:'Save preferences'}).click();
   await page.getByText('Preferences saved for your account.').waitFor(); await density(page,false);
   cell=await openTable(page);
   assert.equal(await cell.evaluate(el=>getComputedStyle(el).paddingTop),'10px');
   await page.getByRole('button',{name:'Sign out'}).click();
   await page.getByLabel('One-time key or link').fill('l.valid');
   await page.getByRole('button',{name:'Continue to workspace'}).click();
   await page.getByRole('link',{name:'Analytics',exact:true}).waitFor(); await density(page,false);
   await page.close(); console.log(`Compact table preference passed at ${width}px`);
  }
  const failed=await browser.newPage(); await login(failed);
  await failed.route('**/api/v1/settings',r=>r.request().method()==='POST'?r.fulfill({status:500,contentType:'application/json',body:'{"error":"Synthetic failure"}'}):r.continue());
  await failed.getByLabel('Compact tables').check(); await failed.getByRole('button',{name:'Save preferences'}).click();
  await failed.getByText('The result may be uncertain',{exact:false}).waitFor(); await density(failed,false);
  failed.once('dialog',d=>d.accept()); const failedCell=await openTable(failed);
  assert.equal(await failedCell.evaluate(el=>getComputedStyle(el).paddingTop),'10px'); await failed.close();
  console.log('Failed saves do not apply the compact draft');
  // Hold a pre-save shell read until after the confirmed update. Either first
  // GET can be the shell request depending on React effect ordering.
  let raced=false;
  for(const held of [1,2]){
   const page=await browser.newPage(); let count=0,release,finished;
   const gate=new Promise(resolve=>release=resolve), done=new Promise(resolve=>finished=resolve);
   await page.route('**/api/v1/settings',async route=>{
    if(route.request().method()!=='GET'){await route.continue();return}
    const response=await route.fetch(),json=await response.json();
    if(++count===held){await gate;try{await route.fulfill({response,json})}catch{}finished()}
    else await route.fulfill({response,json});
   });
   await page.goto(base+'/settings'); await page.getByLabel('One-time key or link').fill('l.valid');
   await page.getByRole('button',{name:'Continue to workspace'}).click();
   let ready=true; try{await page.getByLabel('Compact tables').waitFor({timeout:5000})}catch{ready=false}
   if(ready&&count>=2){
    await page.getByLabel('Compact tables').check(); await page.getByRole('button',{name:'Save preferences'}).click();
    await page.getByText('Preferences saved for your account.').waitFor(); await density(page,true);
    release(); await done; await page.waitForTimeout(200); await density(page,true); raced=true;
   }else release();
   await page.close(); if(raced)break;
  }
  assert(raced,'could not exercise a delayed shell settings read');
  console.log('Delayed settings read does not overwrite saved table density');
  const missing=await browser.newPage();
  await missing.route('**/api/v1/settings',async route=>{const response=await route.fetch();const json=await response.json();delete json.settings.general.compact_tables;await route.fulfill({response,json})});
  await missing.goto(base); await missing.getByLabel('One-time key or link').fill('l.valid');
  await missing.getByRole('button',{name:'Continue to workspace'}).click();
  const defaultCell=await openTable(missing); await density(missing,false);
  assert.equal(await defaultCell.evaluate(el=>getComputedStyle(el).paddingTop),'10px');
  await missing.close(); console.log('Missing settings use standard table spacing');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
