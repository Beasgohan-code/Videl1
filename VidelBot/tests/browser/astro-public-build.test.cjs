// Browser checks against the locally built, static Astro site only.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.ASTRO_TEST_URL||'http://localhost:4321';
const paths=['/','/app/','/features/','/telegram-bot-hosting/','/cloud-hosting/','/ai-development/','/developer-tools/','/templates/','/documentation/','/api/','/pricing/','/changelog/','/faq/','/support/'];
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'}),errors=[];
   page.on('pageerror',err=>errors.push(err.message));
   for(const path of paths){
    const response=await page.goto(base+path,{waitUntil:'networkidle'});
    assert.equal(response.status(),200,`${path} status`);
    assert.ok((await page.title()).includes('Videl'),`${path} title`);
    assert.equal(await page.getByRole('main').count(),1,`${path} main landmark`);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${path} overflow at ${width}px`);
   }
   await page.goto(base+'/');
   await page.getByRole('heading',{name:/Your bots/}).waitFor();
   await page.getByRole('button',{name:'Switch to light theme'}).click();
   assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
   assert.equal(await page.evaluate(()=>localStorage.getItem('videl_public_theme')),'light');
   await page.reload();assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
   await page.getByRole('button',{name:'Switch to dark theme'}).click();
   assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
   if(width<700){await page.getByLabel('Open navigation').click();assert.equal(await page.getByRole('navigation',{name:'Mobile navigation'}).isVisible(),true);}
   await page.goto(base+'/app/');await page.getByRole('heading',{name:'Studio connection not configured.'}).waitFor();
   assert.equal(await page.locator('input[type=password]').count(),0,'standalone fallback must not pretend to sign in');
   assert.deepEqual(errors,[],`browser errors at ${width}px`);await page.close();
   console.log(`Static site routes, theme and standalone fallback passed at ${width}px`);
  }
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
