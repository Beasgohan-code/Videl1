// Synthetic API and browser only; no real storage or MongoDB.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(p){await p.goto(base);await p.getByLabel('One-time key or link').fill('l.valid');await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByRole('link',{name:'File Store',exact:true}).click();await p.getByLabel('Choose files to upload').waitFor()}
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
 for(const width of [320,390,1440]){
  const p=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});await login(p);
  const a=`first-${width}.txt`,b=`second-${width}.txt`;
  await p.getByLabel('Choose files to upload').setInputFiles([a,b].map(name=>({name,mimeType:'text/plain',buffer:Buffer.from('fixture')})));
  await p.getByRole('heading',{name:a}).waitFor();await p.getByRole('heading',{name:b}).waitFor();
  const aButton=p.getByRole('button',{name:`Rename ${a}`}),bButton=p.getByRole('button',{name:`Rename ${b}`});
  await aButton.click();assert.equal(await p.getByLabel('New filename').evaluate(e=>document.activeElement===e),true);
  await p.getByLabel('New filename').fill('unsaved.txt');
  p.once('dialog',d=>d.dismiss());await p.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await p.getByLabel('New filename').inputValue(),'unsaved.txt');
  p.once('dialog',d=>d.dismiss());await bButton.click();assert.equal(await p.getByLabel('New filename').inputValue(),'unsaved.txt');
  p.once('dialog',d=>d.dismiss());await p.getByRole('link',{name:'Commands',exact:true}).click();assert(p.url().endsWith('/files'));assert.equal(await p.getByLabel('New filename').inputValue(),'unsaved.txt');
  p.once('dialog',d=>d.accept());await bButton.click();assert.equal(await p.getByLabel('New filename').inputValue(),b);
  await p.getByRole('button',{name:'Cancel',exact:true}).click();await bButton.waitFor();await p.waitForFunction(name=>document.activeElement?.getAttribute('aria-label')===name,`Rename ${b}`);
  await aButton.click();await p.getByLabel('New filename').fill('discarded.txt');p.once('dialog',d=>d.accept());await p.getByRole('button',{name:'Cancel',exact:true}).click();await p.waitForFunction(name=>document.activeElement?.getAttribute('aria-label')===name,`Rename ${a}`);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);
  console.log(`Rename draft and focus checks passed at ${width}px`);await p.close();
 }
 const p=await browser.newPage();await login(p);await p.getByLabel('Choose files to upload').setInputFiles({name:'pending.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});await p.getByRole('heading',{name:'pending.txt'}).waitFor();
 let release;const gate=new Promise(resolve=>{release=resolve});let requested;const requestSeen=new Promise(resolve=>{requested=resolve});
 await p.route('**/api/v1/workspaces/*/filestore/*',async route=>{if(route.request().method()==='POST'){requested();await gate;await route.fulfill({status:500,contentType:'application/json',body:'{"error":"Synthetic failure"}'})}else await route.continue()});
 await p.getByRole('button',{name:'Rename pending.txt'}).click();await p.getByLabel('New filename').fill('pending-draft.txt');await p.getByRole('button',{name:'Save filename'}).click();await requestSeen;
 assert.equal(await p.getByRole('button',{name:'Cancel',exact:true}).isDisabled(),true);assert.equal(await p.getByLabel('New filename').isDisabled(),true);
 p.once('dialog',d=>d.dismiss());await p.getByRole('link',{name:'Commands',exact:true}).click();assert(p.url().endsWith('/files'));
 release();await p.getByText('File and record changes may be partial',{exact:false}).waitFor();assert.equal(await p.getByLabel('New filename').inputValue(),'pending-draft.txt');
 p.once('dialog',d=>d.dismiss());await p.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await p.getByLabel('New filename').inputValue(),'pending-draft.txt');
 console.log('Rename pending guard and uncertain failure preserve draft');await p.close();
}finally{await browser.close()}})().catch(error=>{console.error(error);process.exitCode=1});
