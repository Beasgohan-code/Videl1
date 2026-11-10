// Synthetic fixture and browser only; not a real Telegram or Mongo integration test.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(page,key='l.valid'){
  await page.goto(base+'/bots'); await page.getByLabel('One-time key or link').fill(key);
  await page.getByRole('button',{name:'Continue to workspace'}).click();
  await page.getByRole('heading',{name:/Your bots/}).waitFor();
  await page.getByRole('heading',{name:'Slow workspace bot'}).waitFor();
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  for(const width of [320,390,1440]){
   const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});await login(page);
   const writes=[];page.on('request',r=>{if(r.method()==='PATCH'&&r.url().includes('/bots/'))writes.push(r.postDataJSON())});
   await page.getByRole('button',{name:'Edit record Slow workspace bot'}).click();
   await page.getByText('not the bot’s Telegram profile',{exact:false}).waitFor();
   assert.equal(await page.getByLabel('Record name').evaluate(e=>document.activeElement===e),true,'editor receives focus');
   await page.getByLabel('Record name').fill('Renamed record');
   page.once('dialog',d=>d.dismiss());await page.getByRole('link',{name:'Commands',exact:true}).click();
   assert.equal(await page.getByLabel('Record name').inputValue(),'Renamed record');
   page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Cancel editing'}).click();
   assert.equal(await page.getByLabel('Record name').inputValue(),'Renamed record');
   await page.getByRole('button',{name:'Save record'}).click();await page.getByRole('heading',{name:'Renamed record',exact:true}).waitFor();
   assert.deepEqual(writes,[{display_name:'Renamed record',expected:{display_name:'Slow workspace bot'}}]);
   assert.equal(await page.getByLabel('Record name').count(),0);
   await page.getByRole('button',{name:'Edit record Renamed record'}).click();await page.getByLabel('Record description').fill('Edited description');
   await page.getByRole('button',{name:'Save record'}).click();await page.getByText('Edited description',{exact:true}).waitFor();
   assert.deepEqual(writes[1],{description:'Edited description',expected:{description:''}});
   await page.getByRole('button',{name:'Edit record Renamed record'}).click();
   await page.getByLabel('Category').fill('Media');await page.getByRole('button',{name:'Remove tag 1'}).click();
   await page.getByRole('button',{name:'Add tag'}).click();await page.getByRole('button',{name:'Save record'}).click();
   await page.getByText('Use up to 12 non-empty tags',{exact:false}).waitFor();
   await page.getByLabel('Tag 2',{exact:true}).fill('gamma');await page.getByRole('button',{name:'Save record'}).click();
   await page.getByText('#gamma',{exact:true}).waitFor();
   assert.deepEqual(writes[2],{category:'Media',tags:['beta','gamma'],expected:{category:'Utility',tags:['alpha','beta']}});
   const editButton=page.getByRole('button',{name:'Edit record Renamed record'});await editButton.click();await page.getByRole('button',{name:'Cancel editing'}).click();await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='Edit record Renamed record');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}px`);
   await page.close();console.log(`Bot record edit, changed fields and draft guards passed at ${width}px`);
  }
  const conflict=await browser.newPage();await login(conflict);await conflict.getByRole('button',{name:'Edit record Slow workspace bot'}).click();
  await conflict.getByLabel('Record name').fill('My draft');let writes=0;conflict.on('request',r=>{if(r.method()==='PATCH')writes++});
  await conflict.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{
   if(route.request().method()==='GET'){const response=await route.fetch();const json=await response.json();json.bot.display_name='Other edit';await route.fulfill({response,json})}
   else await route.continue()
  });
  await conflict.getByRole('button',{name:'Save record'}).click();await conflict.getByText('This record changed since you opened it.',{exact:false}).waitFor();
  assert.equal(writes,0);assert.equal(await conflict.getByLabel('Record name').inputValue(),'My draft');await conflict.close();
  console.log('Changed record detected before write; draft retained');
  const failed=await browser.newPage();await login(failed);await failed.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await failed.getByLabel('Record name').fill('Unsure');
  await failed.route('**/api/v1/workspaces/slow/bots/slow_bot/profile',route=>route.fulfill({status:500,contentType:'application/json',body:'{"error":"failure"}'}));
  await failed.getByRole('button',{name:'Save record'}).click();await failed.getByText('the result may be uncertain',{exact:false}).waitFor();
  assert.equal(await failed.getByLabel('Record name').inputValue(),'Unsure');await failed.close();console.log('Uncertain write retains draft');
  const pending=await browser.newPage();await login(pending);await pending.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await pending.getByLabel('Record name').fill('Do not write');
  let release,started;const gate=new Promise(r=>release=r),startedGet=new Promise(r=>started=r);let patches=0;pending.on('request',r=>{if(r.method()==='PATCH')patches++});
  await pending.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{if(route.request().method()==='GET'){started();await gate} try{await route.continue()}catch{}});
  await pending.getByRole('button',{name:'Save record'}).click();await startedGet;
  pending.once('dialog',d=>d.accept());await pending.getByRole('link',{name:'Commands',exact:true}).click();
  await pending.getByRole('heading',{name:/Give your bot/}).waitFor();release();await pending.waitForTimeout(500);
  assert.equal(patches,0,'leaving during the preflight must not start a write');await pending.close();console.log('Leaving during preflight does not start a write');
  const casConflict=await browser.newPage();await login(casConflict);await casConflict.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await casConflict.getByLabel('Record name').fill('Race draft');
  await casConflict.route('**/api/v1/workspaces/slow/bots/slow_bot/profile',route=>route.fulfill({status:409,contentType:'application/json',body:'{"code":"profile_conflict"}'}));
  await casConflict.getByRole('button',{name:'Save record'}).click();await casConflict.getByText('This record changed or is no longer available.',{exact:false}).waitFor();assert.equal(await casConflict.getByLabel('Record name').inputValue(),'Race draft');await casConflict.close();
  const old=await browser.newPage();await login(old);await old.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await old.getByLabel('Record name').fill('Old server draft');let oldWrites=0;old.on('request',r=>{if(r.method()==='PATCH')oldWrites++});
  await old.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{const response=await route.fetch();const json=await response.json();delete json.capabilities;await route.fulfill({response,json})});
  await old.getByRole('button',{name:'Save record'}).click();await old.getByText('does not support safe conditional edits',{exact:false}).waitFor();assert.equal(oldWrites,0);await old.close();
  const mixed=await browser.newPage();await login(mixed);await mixed.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await mixed.getByLabel('Record name').fill('Mixed worker draft');const paths=[];mixed.on('request',r=>{if(r.method()==='PATCH')paths.push(new URL(r.url()).pathname)});
  await mixed.route('**/api/v1/workspaces/slow/bots/slow_bot/profile',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));
  await mixed.getByRole('button',{name:'Save record'}).click();await mixed.getByText('could not safely apply the selected fields',{exact:false}).waitFor();assert.deepEqual(paths,['/api/v1/workspaces/slow/bots/slow_bot/profile']);await mixed.close();
  console.log('CAS conflict retains draft; old and mixed workers cannot downgrade to unconditional writes');
  const olderFields=await browser.newPage();await login(olderFields);
  await olderFields.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{const response=await route.fetch();const json=await response.json();delete json.capabilities.conditional_profile_fields;await route.fulfill({response,json})});
  await olderFields.getByRole('button',{name:'Edit record Slow workspace bot'}).click();
  await olderFields.getByText('Category or tags cannot be edited here',{exact:false}).waitFor();
  assert.equal(await olderFields.getByLabel('Category').isDisabled(),true);assert.equal(await olderFields.getByRole('button',{name:'Add tag'}).isDisabled(),true);
  await olderFields.getByLabel('Record name').fill('Compatible');await olderFields.getByRole('button',{name:'Save record'}).click();
  await olderFields.getByRole('heading',{name:'Compatible',exact:true}).waitFor();await olderFields.close();
  const tagConflict=await browser.newPage();await login(tagConflict);await tagConflict.getByRole('button',{name:'Edit record Slow workspace bot'}).click();
  await tagConflict.getByLabel('Tag 1',{exact:true}).fill('draft');let tagWrites=0;tagConflict.on('request',r=>{if(r.method()==='PATCH')tagWrites++});
  await tagConflict.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{const response=await route.fetch();const json=await response.json();json.bot.tags=['other'];await route.fulfill({response,json})});
  await tagConflict.getByRole('button',{name:'Save record'}).click();await tagConflict.getByText('This record changed since you opened it.',{exact:false}).waitFor();
  assert.equal(tagWrites,0);assert.equal(await tagConflict.getByLabel('Tag 1',{exact:true}).inputValue(),'draft');await tagConflict.close();
  const mixedFields=await browser.newPage();await login(mixedFields);await mixedFields.getByRole('button',{name:'Edit record Slow workspace bot'}).click();
  await mixedFields.getByLabel('Tag 1',{exact:true}).fill('draft');const mixedWrites=[];mixedFields.on('request',r=>{if(r.method()==='PATCH')mixedWrites.push(r.postDataJSON())});
  await mixedFields.route('**/api/v1/workspaces/slow/bots/slow_bot/profile',route=>route.fulfill({status:400,contentType:'application/json',body:'{}'}));
  await mixedFields.getByRole('button',{name:'Save record'}).click();await mixedFields.getByText('could not safely apply the selected fields',{exact:false}).waitFor();
  assert.deepEqual(mixedWrites,[{tags:['draft','beta'],expected:{tags:['alpha','beta']}}]);assert.equal(await mixedFields.getByLabel('Tag 1',{exact:true}).inputValue(),'draft');await mixedFields.close();
  console.log('Old field capabilities, tag conflicts and mixed field workers fail safely');
  const malformed=await browser.newPage();await login(malformed);
  await malformed.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{const response=await route.fetch();const json=await response.json();json.bot.tags=['valid',42];await route.fulfill({response,json})});
  await malformed.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await malformed.getByText('Category or tags cannot be edited here',{exact:false}).waitFor();
  assert.equal(await malformed.getByRole('button',{name:'Add tag'}).isDisabled(),true);assert.equal(await malformed.getByLabel('Category').isEnabled(),true);await malformed.close();
  const unchanged=await browser.newPage();
  await unchanged.route('**/api/v1/workspaces/slow/bots',async route=>{const response=await route.fetch();const json=await response.json();json.bots[0].description='  retained  ';await route.fulfill({response,json})});
  await unchanged.route('**/api/v1/workspaces/slow/bots/slow_bot',async route=>{const response=await route.fetch();const json=await response.json();json.bot.description='  retained  ';await route.fulfill({response,json})});
  await login(unchanged);const unchangedWrites=[];unchanged.on('request',r=>{if(r.method()==='PATCH')unchangedWrites.push(r.postDataJSON())});
  await unchanged.getByRole('button',{name:'Edit record Slow workspace bot'}).click();await unchanged.getByLabel('Category').fill('Changed');await unchanged.getByRole('button',{name:'Save record'}).click();
  await unchanged.getByText('Changed',{exact:true}).waitFor();assert.deepEqual(unchangedWrites,[{category:'Changed',expected:{category:'Utility'}}]);await unchanged.close();
  console.log('Malformed tags are disabled and untouched fields are not normalized into a write');
  const readonly=await browser.newPage();await login(readonly,'l.readonly');assert.equal(await readonly.getByRole('button',{name:/Edit record/}).count(),0);await readonly.close();
  console.log('Read-only workspace does not expose record editing');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
