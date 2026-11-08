// Synthetic SDK and API only; this does not verify a real Telegram signature.
const {chromium}=require('playwright-core');const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
for(const width of [320,390,1440]){
 const p=await b.newPage({viewport:{width,height:900}}),bodies=[];
 await p.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"synthetic-signed-launch",initDataUnsafe:{user:{id:999}}}};'}));
 p.on('request',r=>{if(r.url().endsWith('/api/v1/auth/login'))bodies.push(r.postDataJSON())});
 await p.goto(base+'#tgWebAppData=synthetic');await p.getByRole('link',{name:'Bot management',exact:true}).waitFor();
 assert.deepEqual(bodies,[{init_data:'synthetic-signed-launch'}]);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 await p.reload();await p.getByRole('link',{name:'Bot management',exact:true}).waitFor();assert.equal(bodies.length,1);
 await p.getByRole('button',{name:'Sign out'}).click();await p.getByLabel('One-time key or link').waitFor();
 await p.reload();await p.getByLabel('One-time key or link').waitFor();await p.getByRole('button',{name:'Continue with Telegram'}).waitFor();assert.equal(bodies.length,1);
 await p.getByRole('button',{name:'Continue with Telegram'}).click();await p.getByRole('link',{name:'Bot management',exact:true}).waitFor();assert.equal(bodies.length,2);
 await p.close();console.log(`Next synthetic Mini App login and sign-out passed at ${width}px`);
}
// A fresh launch after sign-out can automatically sign in in the same tab.
const fresh=await b.newPage();await fresh.addInitScript(()=>{if(!sessionStorage.getItem('videl_telegram_signed_out'))sessionStorage.setItem('videl_telegram_signed_out','100')});
await fresh.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"auth_date=101&hash=synthetic"}};'}));
await fresh.goto(base+'#tgWebAppData=synthetic');await fresh.getByRole('link',{name:'Bot management',exact:true}).waitFor();
assert.equal(await fresh.evaluate(()=>sessionStorage.getItem('videl_telegram_signed_out')),null);
await fresh.getByRole('button',{name:'Sign out'}).click();await fresh.getByLabel('One-time key or link').waitFor();
let retries=0;await fresh.route('**/api/v1/auth/login',r=>{retries++;return r.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:'already used'})})});
await fresh.getByRole('button',{name:'Continue with Telegram'}).click();await fresh.getByText('Telegram could not sign you in.',{exact:false}).waitFor();
await fresh.reload();await fresh.getByRole('button',{name:'Continue with Telegram'}).waitFor();assert.equal(retries,1);
await fresh.close();console.log('Fresh launch resumes automatically; failed explicit retry stays signed out');
const bad=await b.newPage();await bad.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"invalid",initDataUnsafe:{user:{id:1}}}};'}));
await bad.goto(base+'#tgWebAppData=synthetic');await bad.getByText('Telegram could not sign you in.',{exact:false}).waitFor();
assert.equal(await bad.getByRole('link',{name:'Bot management',exact:true}).count(),0);await bad.close();console.log('Invalid synthetic launch falls back without trusting unsafe identity');
const key=await b.newPage();const sent=[];await key.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"synthetic-signed-launch"}};'}));key.on('request',r=>{if(r.url().endsWith('/api/v1/auth/login'))sent.push(r.postDataJSON())});await key.goto(base+'#l.valid');await key.getByLabel('One-time key or link').waitFor();assert.equal(sent.length,0);assert.equal(await key.evaluate(()=>location.hash),'');await key.getByRole('button',{name:'Continue to workspace'}).click();await key.getByRole('link',{name:'Bot management',exact:true}).waitFor();assert.deepEqual(sent,[{link:'l.valid'}]);await key.close();console.log('Explicit one-time link takes priority');
const override=await b.newPage();const overrideSent=[];await override.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({status:200,contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"synthetic-signed-launch"}};'}));override.on('request',r=>{if(r.url().endsWith('/api/v1/auth/login'))overrideSent.push(r.postDataJSON())});
await override.goto(base+'#l.valid');await override.getByRole('button',{name:'Continue with Telegram'}).click();await override.getByRole('link',{name:'Bot management',exact:true}).waitFor();assert.deepEqual(overrideSent,[{init_data:'synthetic-signed-launch'}]);await override.close();console.log('Explicit Telegram choice can override a prefilled key');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
