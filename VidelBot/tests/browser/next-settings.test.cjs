// Synthetic API/Chromium only; not real Mongo, multi-worker or Telegram evidence.
const {chromium}=require('playwright-core');const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
async function login(p,key='l.valid'){await p.goto(base+'/settings');await p.getByLabel('One-time key or link').fill(key);await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByLabel('Theme preference').waitFor()}
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});b.setDefaultTimeout(60000);try{
for(const width of [320,390,1440]){const p=await b.newPage({viewport:{width,height:900}});const writes=[];p.on('request',r=>{if(r.url().endsWith('/api/v1/settings')&&r.method()==='POST')writes.push(r.postDataJSON())});await login(p);await p.getByLabel('Theme preference').selectOption('light');await p.getByRole('button',{name:'Save preferences'}).click();await p.getByText('Preferences saved for your account.').waitFor();assert.deepEqual(writes,[{appearance:{theme:'light'}}]);await p.getByLabel('Select workspace').selectOption('bravo');await p.getByLabel('Theme preference').waitFor();assert.equal(await p.getByLabel('Theme preference').inputValue(),'light');await p.getByLabel('Compact tables').check();p.once('dialog',d=>d.dismiss());await p.getByRole('link',{name:'Bot management',exact:true}).click();assert.equal(await p.getByLabel('Compact tables').isChecked(),true);await p.getByRole('button',{name:'Save preferences'}).click();await p.getByText('Preferences saved for your account.').waitFor();assert.deepEqual(writes[1],{general:{compact_tables:true}});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);console.log(`Settings save, workspace scope and guard passed at ${width}px`);await p.close()}
// Preferences are account-scoped: switching workspace keeps the draft AND its guard.
for(const width of [320,390,1440]){
 const p=await b.newPage({viewport:{width,height:900}});await login(p);
 await p.getByLabel('Theme preference').selectOption('light');
 let unexpected=0;p.on('dialog',async d=>{unexpected++;await d.dismiss()});
 await p.getByLabel('Select workspace').selectOption('bravo');
 assert.equal(await p.getByLabel('Theme preference').inputValue(),'light');
 assert.equal(unexpected,0,'workspace switching should not discard account settings');
 await p.getByRole('link',{name:'Bot management',exact:true}).click();
 assert.equal(unexpected,1,'retained draft must still warn before navigation');
 assert.equal(await p.getByLabel('Theme preference').inputValue(),'light');
 await p.getByRole('button',{name:'Save preferences'}).click();
 await p.getByText('Preferences saved for your account.').waitFor();
 await p.close();console.log(`Account draft survives workspace switch at ${width}px`);
}
const pendingSwitch=await b.newPage();await login(pendingSwitch);let releaseSwitch,seenSwitch;
const switchGate=new Promise(r=>releaseSwitch=r),switchStarted=new Promise(r=>seenSwitch=r);
await pendingSwitch.route('**/api/v1/settings',async route=>{if(route.request().method()==='POST'){seenSwitch();await switchGate}await route.continue()});
await pendingSwitch.getByLabel('Theme preference').selectOption('light');
await pendingSwitch.getByRole('button',{name:'Save preferences'}).click();await switchStarted;
await pendingSwitch.getByLabel('Select workspace').selectOption('bravo');
let switchWarning='';pendingSwitch.once('dialog',async d=>{switchWarning=d.message();await d.dismiss()});
await pendingSwitch.getByRole('link',{name:'Bot management',exact:true}).click();
assert.match(switchWarning,/pending operation may still finish/i);
assert.equal(await pendingSwitch.getByRole('button',{name:'Saving…'}).isDisabled(),true);
releaseSwitch();await pendingSwitch.getByText('Preferences saved for your account.').waitFor();await pendingSwitch.close();
console.log('Pending account save stays guarded across workspace switch');
const overlap=await b.newPage();await login(overlap);
await overlap.getByRole('button',{name:'New workspace'}).click();
await overlap.getByLabel('Workspace name').fill('Draft overlap');
await overlap.getByLabel('Theme preference').selectOption('light');
assert.equal(await overlap.getByRole('button',{name:'Create workspace'}).isDisabled(),true);
await overlap.getByText('Finish or discard the current changes before creating a workspace.').waitFor();
await overlap.getByLabel('Theme preference').selectOption('dark');
assert.equal(await overlap.getByRole('button',{name:'Create workspace'}).isEnabled(),true);
let allowCreate,beginCreate;const createGate=new Promise(r=>allowCreate=r),createStarted=new Promise(r=>beginCreate=r);
await overlap.route('**/api/v1/workspaces',async route=>{if(route.request().method()==='POST'){beginCreate();await createGate}await route.continue()});
await overlap.getByRole('button',{name:'Create workspace'}).click();await createStarted;
assert.equal(await overlap.getByLabel('Theme preference').isDisabled(),true);
await overlap.getByText('Wait for workspace creation to finish before editing account preferences.').waitFor();
allowCreate();await overlap.getByLabel('Select workspace').locator('option',{hasText:'Draft overlap'}).waitFor({state:'attached'});
await overlap.getByLabel('Theme preference').waitFor();assert.equal(await overlap.getByLabel('Theme preference').isEnabled(),true);
await overlap.close();console.log('Workspace creation and account drafts cannot overlap');
const empty=await b.newPage();await login(empty,'l.empty');await empty.getByLabel('File view preference').selectOption('grid');await empty.getByRole('button',{name:'Save preferences'}).click();await empty.getByText('Preferences saved for your account.').waitFor();await empty.close();console.log('Settings no-workspace account passed');
const pending=await b.newPage();await login(pending);let release;const gate=new Promise(resolve=>release=resolve);let started;const startedPost=new Promise(resolve=>started=resolve);await pending.route('**/api/v1/settings',async route=>{if(route.request().method()==='POST'){started();await gate}await route.continue()});await pending.getByLabel('Theme preference').selectOption('light');await pending.getByRole('button',{name:'Save preferences'}).click();await startedPost;pending.once('dialog',d=>d.dismiss());await pending.getByRole('link',{name:'Bot management',exact:true}).click();assert.equal(await pending.getByRole('button',{name:'Saving…'}).isDisabled(),true);release();await pending.getByText('Preferences saved for your account.').waitFor();await pending.close();console.log('Settings pending-save navigation guard passed');
const legacy=await b.newPage();await legacy.route('**/api/v1/settings',async route=>{const response=await route.fetch();const json=await response.json();delete json.capabilities;await route.fulfill({response,json})});await login(legacy);assert.equal(await legacy.getByLabel('Theme preference').isDisabled(),true);await legacy.getByText('Editing is disabled here',{exact:false}).waitFor();await legacy.close();console.log('Settings older backend gate passed');
const failure=await b.newPage();await login(failure);await failure.route('**/api/v1/settings',async route=>{if(route.request().method()==='POST')await route.fulfill({status:500,contentType:'application/json',body:'{"error":"Synthetic failure"}'});else await route.continue()});await failure.getByLabel('Theme preference').selectOption('system');await failure.getByRole('button',{name:'Save preferences'}).click();await failure.getByText('The result may be uncertain',{exact:false}).waitFor();assert.equal(await failure.getByLabel('Theme preference').inputValue(),'system');assert.equal(await failure.getByRole('button',{name:'Save preferences'}).isEnabled(),true);await failure.close();console.log('Settings uncertain result preserves draft');
const mismatch=await b.newPage();await login(mismatch);await mismatch.route('**/api/v1/settings',async route=>{if(route.request().method()==='POST')await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({settings:{appearance:{theme:'dark',file_view:'list'},general:{compact_tables:false}}})});else await route.continue()});await mismatch.getByLabel('Theme preference').selectOption('light');await mismatch.getByRole('button',{name:'Save preferences'}).click();await mismatch.getByText('did not match your changes',{exact:false}).waitFor();assert.equal(await mismatch.getByLabel('Theme preference').inputValue(),'light');await mismatch.close();console.log('Settings mismatched response preserves draft');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
