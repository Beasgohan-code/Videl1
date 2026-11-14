// Synthetic Next API only; not real MongoDB or Telegram.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(page){await page.goto(base+'/commands');await page.getByLabel('One-time key or link').fill('l.valid');await page.getByRole('button',{name:'Continue to workspace'}).click();await page.getByRole('heading',{name:/Give your bot/}).waitFor()}
async function create(page){await page.getByLabel('Command name').fill('hello');await page.getByLabel('Description',{exact:true}).fill('Original');await page.getByRole('button',{name:'Create record'}).click();await page.getByRole('heading',{name:'/hello'}).waitFor()}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});await login(page);await create(page);
   const writes=[];page.on('request',r=>{if(['PATCH','DELETE'].includes(r.method())&&r.url().includes('/commands'))writes.push({method:r.method(),path:new URL(r.url()).pathname,body:r.postDataJSON()})});
   await page.getByRole('button',{name:'Edit /hello'}).click();await page.getByLabel('Description',{exact:true}).fill('Edited');await page.getByRole('button',{name:'Update record'}).click();await page.getByText('Edited',{exact:true}).waitFor();
   assert.deepEqual(writes[0],{method:'PATCH',path:'/api/v1/workspaces/slow/bots/slow_bot/commands/conditional',body:{command:'hello',expected:{description:'Original',category:'Custom',permission:'everyone',visibility:'public',enabled:true,custom_reply_text:'',handler_bound:false},changes:{description:'Edited'}}});
   await page.getByRole('button',{name:'Disable /hello'}).click();await page.getByRole('button',{name:'Enable /hello'}).waitFor();
   assert.deepEqual(writes[1].body.changes,{enabled:false});assert.equal(writes[1].body.expected.description,'Edited');
   await page.getByRole('button',{name:'Delete /hello'}).click();await page.getByRole('button',{name:'Confirm delete'}).click();await page.getByRole('heading',{name:'/hello'}).waitFor({state:'detached'});
   assert.equal(writes[2].method,'DELETE');assert.equal(writes[2].body.expected.enabled,false);assert(writes.every(w=>w.path.endsWith('/commands/conditional')));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
   await page.close();console.log(`Conditional edit, toggle and delete passed at ${width}px`);
  }
  const stale=await browser.newPage();await login(stale);await create(stale);await stale.getByRole('button',{name:'Edit /hello'}).click();await stale.getByLabel('Description',{exact:true}).fill('My draft');
  await stale.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/conditional',route=>route.fulfill({status:409,contentType:'application/json',body:'{"code":"command_conflict"}'}));
  await stale.getByRole('button',{name:'Update record'}).click();await stale.getByText('This command changed or is no longer available.',{exact:false}).waitFor();assert.equal(await stale.getByLabel('Description',{exact:true}).inputValue(),'My draft');await stale.close();
  const older=await browser.newPage();await older.route('**/api/v1/workspaces/slow/bots/slow_bot/commands',async route=>{const response=await route.fetch();const json=await response.json();delete json.capabilities.command_conditional_mutations;await route.fulfill({response,json})});
  await login(older);await create(older);await older.getByText('Safe edits, state changes and deletion are unavailable',{exact:false}).waitFor();assert.equal(await older.getByRole('button',{name:'Edit /hello'}).isDisabled(),true);assert.equal(await older.getByRole('button',{name:'Delete /hello'}).isDisabled(),true);await older.close();
  const mixed=await browser.newPage();await login(mixed);await create(mixed);await mixed.getByRole('button',{name:'Edit /hello'}).click();await mixed.getByLabel('Description',{exact:true}).fill('Draft');const paths=[];mixed.on('request',r=>{if(['POST','PATCH','DELETE'].includes(r.method())&&r.url().includes('/commands'))paths.push(new URL(r.url()).pathname)});
  await mixed.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/conditional',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));
  await mixed.getByRole('button',{name:'Update record'}).click();await mixed.getByText('This server cannot safely apply this command change.',{exact:false}).waitFor();assert.deepEqual(paths,['/api/v1/workspaces/slow/bots/slow_bot/commands/conditional']);assert.equal(await mixed.getByLabel('Description',{exact:true}).inputValue(),'Draft');await mixed.close();
  const confirm=await browser.newPage();await login(confirm);await create(confirm);await confirm.getByRole('button',{name:'Delete /hello'}).click();let expected;
  await confirm.route('**/api/v1/workspaces/slow/bots/slow_bot/commands',async route=>{const response=await route.fetch();const json=await response.json();json.commands[0].description='Other edit';await route.fulfill({response,json})});
  await confirm.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/conditional',route=>{expected=route.request().postDataJSON().expected;return route.fulfill({status:409,contentType:'application/json',body:'{}'})});
  await confirm.getByRole('button',{name:'Refresh records'}).click();await confirm.getByText('Other edit',{exact:true}).waitFor();await confirm.getByRole('button',{name:'Confirm delete'}).click();await confirm.getByText('This command changed or is no longer available.',{exact:false}).waitFor();assert.equal(expected.description,'Original');await confirm.close();
  const uncertain=await browser.newPage();await login(uncertain);await create(uncertain);await uncertain.getByRole('button',{name:'Delete /hello'}).click();
  await uncertain.route('**/api/v1/workspaces/slow/bots/slow_bot/commands/conditional',route=>route.fulfill({status:500,contentType:'application/json',body:'{}'}));
  await uncertain.getByRole('button',{name:'Confirm delete'}).click();await uncertain.getByText('result may be uncertain',{exact:false}).waitFor();
  assert.equal(await uncertain.getByRole('button',{name:'Confirm delete'}).count(),1);await uncertain.close();
  console.log('Stale drafts, old/mixed workers and captured deletion confirmation fail safely');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
