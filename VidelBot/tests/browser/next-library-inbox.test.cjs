// Local synthetic API only; never exercises real Telegram, Mongo or customer data.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});
   await page.goto(base);await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();
   await page.waitForFunction(()=>{const e=document.querySelector('#workspace');return e&&!e.disabled});
   await page.getByLabel('Select workspace').selectOption('bravo');await page.getByText('Bravo workspace bot').waitFor();
   await page.getByRole('link',{name:'Hosting',exact:true}).click();await page.getByRole('heading',{name:/A clearer view/}).waitFor();await page.getByText('Current bot records.').waitFor();
   await page.getByText('Bravo workspace bot').waitFor();assert((await page.locator('body').innerText()).includes('Provider costs'));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`hosting overflow at ${width}px`);
   await page.getByRole('link',{name:'Templates',exact:true}).click();await page.getByRole('heading',{name:'Official example'}).waitFor();
   let official=page.locator('article').filter({has:page.getByRole('heading',{name:'Official example'})});await official.getByText('Inspect JSON payload').click();await official.getByText(/"example": true/).waitFor();
   page.once('dialog',d=>d.accept());await official.getByRole('button',{name:'Copy to workspace'}).click();await page.getByRole('heading',{name:'Copy of Official example'}).waitFor();
   let copy=page.locator('article').filter({has:page.getByRole('heading',{name:'Copy of Official example'})});page.once('dialog',d=>d.accept());await copy.getByRole('button',{name:'Delete'}).click();await page.getByRole('heading',{name:'Copy of Official example'}).waitFor({state:'detached'});
   await page.getByLabel('Template name').fill('Workspace example');await page.getByLabel('JSON object').fill('{"greeting":"hello"}');
   page.once('dialog',d=>d.dismiss());await page.getByRole('link',{name:'Notifications',exact:true}).click();assert(page.url().endsWith('/templates'));
   page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Save template'}).click();await page.getByRole('heading',{name:'Workspace example'}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`template overflow at ${width}px`);await page.getByLabel('Search templates').fill('Workspace');assert.equal(await page.getByRole('heading',{name:'Official example'}).count(),0);await page.getByLabel('Search templates').fill('');
   await page.getByRole('link',{name:'Notifications',exact:true}).click();await page.getByRole('heading',{name:'Bravo workspace alert'}).waitFor();
   let notification=page.locator('article').filter({has:page.getByRole('heading',{name:'Bravo workspace alert'})});await notification.getByRole('button',{name:'Mark read'}).click();await notification.getByRole('button',{name:'Mark unread'}).waitFor();
   await page.getByLabel('Filter read status').selectOption('unread');assert.equal(await page.getByRole('heading',{name:'Bravo workspace alert'}).count(),0);await page.getByLabel('Filter read status').selectOption('all');
   await page.getByRole('button',{name:'Mark all read',exact:true}).click();await page.getByText('This affects everyone with access, including records outside the loaded list.').waitFor();await page.getByRole('button',{name:'Confirm mark all read'}).click();await page.getByRole('button',{name:'Mark unread'}).first().waitFor();
   await page.getByLabel('Filter read status').selectOption('unread');await page.getByText('No matching notifications').waitFor();await page.getByLabel('Filter read status').selectOption('all');await page.getByLabel('Select workspace').selectOption('slow');assert.equal(await page.getByRole('heading',{name:'Bravo workspace alert'}).count(),0);await page.getByRole('heading',{name:'Slow workspace alert'}).waitFor();await page.getByRole('link',{name:'Templates',exact:true}).click();await page.getByRole('heading',{name:'Official example'}).waitFor();assert.equal(await page.getByRole('heading',{name:'Workspace example'}).count(),0);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
   console.log(`Hosting, Templates and Notifications synthetic checks passed at ${width}px`);await page.close();
  }
  const page=await browser.newPage();await page.goto(base);await page.getByLabel('One-time key or link').fill('l.readonly');await page.getByRole('button',{name:'Continue to workspace'}).click();
  const requests=[];page.on('request',r=>{if(/\/templates|\/notifications/.test(new URL(r.url()).pathname)&&r.url().includes('/api/'))requests.push(r.url())});
  await page.getByRole('link',{name:'Templates',exact:true}).click();await page.getByText('Templates access required').waitFor();
  await page.getByRole('link',{name:'Notifications',exact:true}).click();await page.getByText('Logs access required').waitFor();assert.equal(requests.length,0);
  console.log('Templates and Notifications synthetic permission checks passed');await page.close();
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
