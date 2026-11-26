// Real Chromium against the synthetic API fixture only; no production data.
const {chromium}=require('playwright-core'),assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function signIn(page){await page.goto(base);await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();await page.getByText('What you’re working on.').waitFor();}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});await signIn(page);
   await page.getByRole('button',{name:'New workspace'}).first().click();await page.getByLabel('Workspace name').fill('Workspace draft');
   for(const name of ['Manage bots','Manage team','View Hosting Center']){
    let question='';page.once('dialog',async dialog=>{question=dialog.message();await dialog.dismiss()});
    await page.getByRole('link',{name}).click();assert.match(question,/Discard unsaved changes/);
    assert.equal(await page.getByLabel('Workspace name').inputValue(),'Workspace draft');assert.equal(new URL(page.url()).pathname,'/next-preview');
   }
   page.once('dialog',d=>d.accept());await page.getByRole('link',{name:'Manage team'}).click();
   await page.getByRole('heading',{name:/team/i}).first().waitFor();assert.equal(new URL(page.url()).pathname,'/next-preview/team');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
   await page.close();console.log(`Overview links preserve canceled drafts at ${width}px`);
  }
  const empty=await browser.newPage();await empty.route('**/api/v1/workspaces/slow/bots?*',r=>r.fulfill({json:{bots:[]}}));await signIn(empty);
  await empty.getByRole('button',{name:'New workspace'}).first().click();await empty.getByLabel('Workspace name').fill('Empty draft');
  let prompt='';empty.once('dialog',async d=>{prompt=d.message();await d.dismiss()});await empty.getByRole('link',{name:'Connect an existing bot'}).click();
  assert.match(prompt,/Discard unsaved changes/);assert.equal(await empty.getByLabel('Workspace name').inputValue(),'Empty draft');await empty.close();console.log('Empty-state link preserves canceled draft');

  const modified=await browser.newPage();await signIn(modified);await modified.getByRole('link',{name:'Personal settings',exact:true}).click();
  await modified.getByLabel('Theme preference').selectOption('light');
  let dialogs=0;modified.on('dialog',async d=>{dialogs++;await d.dismiss()});
  // Dispatch modified clicks through React but cancel the browser default at window;
  // headless-shell does not reliably open new tabs from an automated Ctrl-click.
  for(const key of ['ctrlKey','metaKey','shiftKey','altKey']){
   await modified.getByRole('link',{name:'Bot management',exact:true}).evaluate((link,key)=>{
    const cancel=e=>e.preventDefault();window.addEventListener('click',cancel,{once:true});
    link.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,button:0,[key]:true}));
   },key);
  }
  assert.equal(dialogs,0);assert.equal(await modified.getByLabel('Theme preference').inputValue(),'light');
  await modified.getByRole('link',{name:'Team',exact:true}).click();assert.equal(dialogs,1,'modified click must not disable dirty guard');
  assert.equal(await modified.getByLabel('Theme preference').inputValue(),'light');await modified.close();console.log('Modified navigation leaves the current dirty draft guarded');

  // Keep a dirty guard active after approval while the destination is still loading.
  // A delayed synthetic RSC response models a navigation that has not yet committed.
  const delayed=await browser.newPage();let allowRoute,routeStarted;
  const routeGate=new Promise(r=>allowRoute=r),routeSeen=new Promise(r=>routeStarted=r);
  await delayed.route('**/next-preview/team*',async route=>{if(route.request().resourceType()==='fetch'){routeStarted();await routeGate}try{await route.continue()}catch{}});
  await signIn(delayed);await delayed.getByRole('link',{name:'Personal settings',exact:true}).click();
  await delayed.getByLabel('Theme preference').selectOption('light');
  delayed.once('dialog',d=>d.accept());await delayed.getByRole('link',{name:'Team',exact:true}).click();
  let routeTimeout;try{await Promise.race([routeSeen,new Promise((_,reject)=>{routeTimeout=setTimeout(()=>reject(Error('Team navigation request did not start')),30000)})])}finally{clearTimeout(routeTimeout)}
  assert.equal(new URL(delayed.url()).pathname,'/next-preview/settings');
  assert.equal(await delayed.getByLabel('Theme preference').inputValue(),'light');
  assert.equal(await delayed.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented}),true);
  let retryWarning='';delayed.once('dialog',async d=>{retryWarning=d.message();await d.dismiss()});
  await delayed.getByRole('link',{name:'Bot management',exact:true}).click();assert.match(retryWarning,/Discard unsaved changes/);
  assert.equal(await delayed.getByLabel('Theme preference').inputValue(),'light');
  allowRoute();await delayed.getByRole('heading',{name:/team/i}).first().waitFor();
  assert.equal(await delayed.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented}),false);
  await delayed.close();console.log('Approved but delayed navigation retains protection until the destination renders');

  const pending=await browser.newPage();let release,start;const gate=new Promise(r=>release=r),seen=new Promise(r=>start=r);
  await pending.route('**/api/v1/workspaces',async route=>{if(route.request().method()!=='POST')return route.continue();start();await gate;try{await route.continue()}catch{}});
  await signIn(pending);await pending.getByRole('button',{name:'New workspace'}).first().click();await pending.getByLabel('Workspace name').fill('Pending workspace');
  await pending.getByRole('button',{name:'Create workspace'}).click();await seen;
  let warning='';pending.once('dialog',async d=>{warning=d.message();await d.dismiss()});await pending.getByRole('link',{name:'Manage bots'}).click();
  assert.match(warning,/pending operation may still finish/i);assert.equal(await pending.getByLabel('Workspace name').inputValue(),'Pending workspace');
  release();await pending.getByLabel('Select workspace').locator('option',{hasText:'Pending workspace'}).waitFor({state:'attached'});await pending.close();console.log('Pending workspace creation warns without pretending to cancel');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
