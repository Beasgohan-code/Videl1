// Local synthetic API only; never exercises real Telegram or customer data.
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
   await page.getByRole('link',{name:'Bot management',exact:true}).click();await page.getByRole('heading',{name:/Your bots/}).waitFor();
   assert.equal(await page.locator('#workspace option:checked').textContent(),'Bravo workspace');
   await page.getByLabel('Search bots').fill('not found');await page.getByText('No matching records').waitFor();await page.getByLabel('Search bots').fill('');
   await page.getByRole('button',{name:'Connect a bot'}).click();
   await page.getByLabel('BotFather token').fill('fixture-token');await page.getByRole('button',{name:'Verify token'}).click();await page.getByText('Telegram verified these credentials.').waitFor();
   assert(!page.url().includes('fixture-token'));assert(!(await page.locator('body').innerText()).includes('fixture-token'));
   page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Close setup'}).click();await page.getByRole('button',{name:'Connect a bot'}).click();assert.equal(await page.getByLabel('BotFather token').inputValue(),'');
   await page.getByLabel('Display name').fill('New test bot');await page.getByRole('button',{name:'Save draft'}).click();
   await page.getByRole('heading',{name:'New test bot'}).waitFor();
   let card=page.locator('article').filter({has:page.getByRole('heading',{name:'New test bot'})});await card.getByRole('button',{name:'Archive',exact:true}).click();await card.getByText('Archived',{exact:true}).waitFor();
   await card.getByRole('button',{name:'Delete',exact:true}).click();await card.getByRole('button',{name:'Confirm delete'}).click();await page.getByRole('heading',{name:'New test bot'}).waitFor({state:'detached'});
   await page.getByRole('link',{name:'Commands',exact:true}).click();await page.getByRole('heading',{name:/Give your bot/}).waitFor();
   await page.getByLabel('Command name').fill('hello');await page.getByLabel('Description',{exact:true}).fill('Say hello');await page.getByRole('button',{name:'Create record'}).click();
   await page.getByRole('heading',{name:'/hello'}).waitFor();
   await page.getByRole('button',{name:'Edit /hello'}).click();await page.getByLabel('Description',{exact:true}).fill('Say hello again');await page.getByRole('button',{name:'Update record'}).click();
   await page.getByText('Say hello again').waitFor();
   await page.getByRole('button',{name:'Disable /hello'}).click();await page.getByRole('button',{name:'Enable /hello'}).waitFor();
   await page.getByRole('button',{name:'Enable /hello'}).click();await page.getByRole('button',{name:'Disable /hello'}).waitFor();
   await page.getByRole('button',{name:'Delete /hello'}).click();await page.getByRole('button',{name:'Confirm delete'}).click();await page.getByRole('heading',{name:'/hello'}).waitFor({state:'detached'});
   await page.getByLabel('Command name').fill('draft');
   page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('link',{name:'File Store',exact:true}).click();
   assert(page.url().endsWith('/commands'),'navigation must stay put when unsaved edits are kept');
   page.once('dialog',dialog=>dialog.accept());await page.getByRole('link',{name:'File Store',exact:true}).click();
   await page.getByRole('heading',{name:/All your files/}).waitFor();
   await page.getByLabel('Choose files to upload').setInputFiles({name:'fixture.txt',mimeType:'text/plain',buffer:Buffer.from('synthetic')});
   await page.getByRole('heading',{name:'fixture.txt'}).waitFor();
   let file=page.locator('article').filter({has:page.getByRole('heading',{name:'fixture.txt'})});
   await file.getByRole('button',{name:'Rename'}).click();await file.getByLabel('New filename').fill('renamed.txt');await file.getByRole('button',{name:'Save filename'}).click();
   await page.getByRole('heading',{name:'renamed.txt'}).waitFor();file=page.locator('article').filter({has:page.getByRole('heading',{name:'renamed.txt'})});
   await file.getByRole('button',{name:'Trash'}).click();await file.getByRole('button',{name:'Confirm trash'}).click();await file.getByText('In trash',{exact:false}).waitFor();
   await file.getByRole('button',{name:'Restore'}).click();await file.getByText('Active record',{exact:false}).waitFor();
   await file.getByRole('button',{name:'Trash'}).click();await file.getByRole('button',{name:'Confirm trash'}).click();await file.getByRole('button',{name:'Purge'}).click();await file.getByRole('button',{name:'Confirm purge'}).click();await page.getByRole('heading',{name:'renamed.txt'}).waitFor({state:'detached'});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
   console.log(`Bot, Command and File Studio synthetic CRUD checks passed at ${width}px`);await page.close();
  }
  const page=await browser.newPage();await page.goto(base);await page.getByLabel('One-time key or link').fill('l.readonly');await page.getByRole('button',{name:'Continue to workspace'}).click();
  await page.getByRole('link',{name:'Bot management',exact:true}).click();await page.getByText('Connecting and modifying records requires bot settings access.').waitFor();
  assert.equal(await page.getByRole('button',{name:'Connect a bot'}).count(),0);
  await page.getByRole('link',{name:'Commands',exact:true}).click();await page.getByText('Commands access required').waitFor();
  await page.getByRole('link',{name:'File Store',exact:true}).click();await page.getByText('File Store access required').waitFor();
  console.log('Bot, Command and File Studio synthetic permission checks passed');await page.close();
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
