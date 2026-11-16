// Actual local aiohttp routes and mock Mongo, signed synthetic launch and temp storage.
// Run against a Next server proxied to telegram_auth_fixture.py, never production.
const {chromium}=require('playwright-core');const crypto=require('node:crypto');const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
function proof(){const fields={auth_date:String(Math.floor(Date.now()/1000)),query_id:'file-contract-fixture',user:JSON.stringify({id:87654,first_name:'Synthetic'})};const secret=crypto.createHmac('sha256','WebAppData').update('123456:TEST').digest();fields.hash=crypto.createHmac('sha256',secret).update(Object.keys(fields).sort().map(k=>`${k}=${fields[k]}`).join('\n')).digest('hex');return new URLSearchParams(fields).toString()}
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
 const p=await b.newPage({viewport:{width:390,height:900}}),signed=proof(),responses=[];
 await p.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:`window.Telegram={WebApp:{initData:${JSON.stringify(signed)}}}`}));
 p.on('response',async r=>{if(/\/filestore\?/.test(r.url())&&r.request().method()==='GET'&&r.ok())responses.push(await r.json())});
 await p.goto(base+'#tgWebAppData=synthetic');await p.getByRole('link',{name:'File Store',exact:true}).click();
 await p.getByText('Sample limit of 2000 was not exceeded').waitFor();await p.getByText('No matching file records').waitFor();
 await p.getByLabel('Choose files to upload').setInputFiles({name:'contract.txt',mimeType:'text/plain',buffer:Buffer.from('local fixture content')});
 await p.getByRole('heading',{name:'contract.txt'}).waitFor();await p.getByRole('button',{name:'Rename contract.txt'}).click();await p.getByLabel('New filename').fill('changed.txt');await p.getByRole('button',{name:'Save filename'}).click();await p.getByRole('heading',{name:'changed.txt'}).waitFor();
 await p.getByLabel('Search files').fill('changed');await p.getByRole('heading',{name:'changed.txt'}).waitFor();
 assert(responses.some(x=>x.sample_limit===2000&&x.sample_truncated===false&&x.items.some(i=>i.filename==='changed.txt')));
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await p.close();console.log('Next File Studio uses actual local backend list, upload, rename and sampling contract (mock Mongo/temp storage)');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
