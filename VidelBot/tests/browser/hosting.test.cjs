const assert=require('node:assert/strict');const {chromium}=require('playwright-core');const path=require('node:path');const fs=require('node:fs');
(async()=>{
 if(!process.env.VIDEL_BROWSER_EXECUTABLE)throw Error('Set VIDEL_BROWSER_EXECUTABLE');
 const out=path.resolve(__dirname,'../../../docs/screenshots');fs.mkdirSync(out,{recursive:true});
 for(const width of [320,390,1440]){
  const browser=await chromium.launch({executablePath:process.env.VIDEL_BROWSER_EXECUTABLE,args:['--no-sandbox','--single-process','--no-zygote','--disable-dev-shm-usage'],headless:true});
  const ctx=await browser.newContext({viewport:{width,height:width===1440?900:844},reducedMotion:'reduce'});const page=await ctx.newPage();let errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8082/app?studio=hosting',{waitUntil:'networkidle'});await page.getByRole('heading',{name:'Hosting center'}).waitFor();
  assert(await page.getByText('Synthetic sample data only').isVisible());assert(await page.getByText('Integration unavailable').first().isVisible());assert.equal(await page.locator('.host-record').count(),2);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`hosting overflow at ${width}`);
  await page.screenshot({path:path.join(out,`hosting-synthetic-${width}.png`),fullPage:width!==1440});
  await page.getByRole('button',{name:'Open fleet & health'}).click();await page.getByRole('heading',{name:'Fleet & health'}).waitFor();
  assert.equal(errors.length,0,errors.join('\n'));await browser.close();
 }
 console.log('Chromium hosting center 320/390/1440 layout and navigation passed (synthetic read-only API).');
})().catch(e=>{console.error(e);process.exit(1)});
