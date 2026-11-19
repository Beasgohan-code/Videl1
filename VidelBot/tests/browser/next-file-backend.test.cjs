// Actual local aiohttp routes and mock Mongo, signed synthetic launch and temp storage.
// Run against a Next server proxied to telegram_auth_fixture.py, never production.
const {chromium}=require('playwright-core');const crypto=require('node:crypto');const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
function proof(){const fields={auth_date:String(Math.floor(Date.now()/1000)),query_id:'file-contract-fixture',user:JSON.stringify({id:87654,first_name:'Synthetic'})};const secret=crypto.createHmac('sha256','WebAppData').update('123456:TEST').digest();fields.hash=crypto.createHmac('sha256',secret).update(Object.keys(fields).sort().map(k=>`${k}=${fields[k]}`).join('\n')).digest('hex');return new URLSearchParams(fields).toString()}
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
 const p=await b.newPage({viewport:{width:390,height:900},acceptDownloads:true}),signed=proof(),responses=[];
 await p.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:`window.Telegram={WebApp:{initData:${JSON.stringify(signed)}}}`}));
 p.on('response',async r=>{if(/\/filestore\?/.test(r.url())&&r.request().method()==='GET'&&r.ok())responses.push(await r.json())});
 await p.goto(base+'#tgWebAppData=synthetic');await p.getByRole('link',{name:'File Store',exact:true}).click();
 await p.getByText('Sample limit of 2000 was not exceeded').waitFor();await p.getByText('No matching file records').waitFor();
 await p.getByLabel('Choose files to upload').setInputFiles({name:'contract.txt',mimeType:'text/plain',buffer:Buffer.from('local fixture content')});
 await p.getByRole('heading',{name:'contract.txt'}).waitFor();await p.getByRole('button',{name:'Rename contract.txt'}).click();await p.getByLabel('New filename').fill('changed.txt');await p.getByRole('button',{name:'Save filename'}).click();await p.getByRole('heading',{name:'changed.txt'}).waitFor();
 await p.getByLabel('Search files').fill('changed');await p.getByRole('heading',{name:'changed.txt'}).waitFor();
 const listed=responses.find(x=>x.sample_limit===2000&&x.sample_truncated===false&&x.items.some(i=>i.filename==='changed.txt'));assert(listed);
 const uploadedDate=new Date(listed.items.find(i=>i.filename==='changed.txt').uploaded_ts*1000).toISOString().slice(0,10);
 await p.getByRole('link',{name:'Analytics',exact:true}).click();await p.getByText('View daily data').waitFor();
 const stats=await p.evaluate(async()=>{const ws=sessionStorage.getItem('videl_next_workspace'),token=sessionStorage.getItem('videl_saas_tok');const r=await fetch(`/api/v1/workspaces/${encodeURIComponent(ws)}/analytics?days=30&bot_id=all`,{headers:{Authorization:`Bearer ${token}`}});if(!r.ok)throw new Error('Analytics request failed');return r.json()});
 assert.equal(stats.complete_utc_buckets,true);assert.equal(stats.totals.period_uploads,1);assert.equal(stats.series.uploads.reduce((a,b)=>a+b,0),1);
 assert.equal(stats.series.uploads[stats.series.days.indexOf(uploadedDate)],1);
 await p.getByText('View daily data').click();await p.getByRole('cell',{name:uploadedDate}).waitFor();
 const [download]=await Promise.all([p.waitForEvent('download'),p.getByRole('button',{name:'Export CSV'}).click()]);
 const csv=require('node:fs').readFileSync(await download.path(),'utf8');assert(csv.includes(`${uploadedDate},1,,`));
 await p.getByLabel('Start date').fill(uploadedDate);await p.getByLabel('End date').fill(uploadedDate);await p.getByRole('button',{name:'Apply dates'}).click();await p.getByText(`${uploadedDate} to ${uploadedDate} UTC`,{exact:false}).waitFor();
 const [custom]=await Promise.all([p.waitForEvent('download'),p.getByRole('button',{name:'Export CSV'}).click()]);assert.equal(custom.suggestedFilename(),`videl_analytics_${uploadedDate}_${uploadedDate}.csv`);assert(require('node:fs').readFileSync(await custom.path(),'utf8').includes(`${uploadedDate},1,,`));
 const teamStatus=await p.evaluate(async()=>{const ws=sessionStorage.getItem('videl_next_workspace'),token=sessionStorage.getItem('videl_saas_tok'),csrf=sessionStorage.getItem('videl_saas_csrf');const response=await fetch(`/api/v1/workspaces/${encodeURIComponent(ws)}/team`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'X-CSRF-Token':csrf,'Content-Type':'application/json'},body:JSON.stringify({user_id:87655,role:'viewer'})});return response.status});assert.equal(teamStatus,201);
 await p.getByRole('link',{name:'Notifications',exact:true}).click();await p.getByRole('heading',{name:'Team member added'}).waitFor();await p.getByRole('button',{name:'Mark read Team member added'}).click();await p.getByRole('button',{name:'Mark unread Team member added'}).waitFor();
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await p.close();console.log('Next File Store, analytics and inbox use actual local backend routes (mock Mongo/temp storage)');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
