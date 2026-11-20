// Intercepted synthetic responses only; does not exercise live MongoDB or storage.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});try{
 for(const width of [320,390,1440]){
  const p=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let max=4, invalid=null;const requested=[];
  await p.route('**/api/v1/workspaces/*/filestore?*',async route=>{
   if(route.request().method()!=='GET')return route.continue();
   const page=Number(new URL(route.request().url()).searchParams.get('page'));requested.push(page);
   const current=Math.min(page,max);
   const body={items:[{file_uid:'record',filename:`page-${current}.txt`,size_bytes:8,category:'document',deleted:false}],total:max,total_bytes:8,page:invalid===null?current:invalid,pages:max,sample_limit:2000,sample_truncated:false};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await p.goto(base);await p.getByLabel('One-time key or link').fill('l.valid');await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByRole('link',{name:'File Store',exact:true}).click();
  const pages=p.getByRole('navigation',{name:'File record pages'}),previous=pages.getByRole('button',{name:'Previous'}),next=pages.getByRole('button',{name:'Next'});
  await p.getByText('Page 1 of 4',{exact:true}).waitFor();assert.equal(await previous.isDisabled(),true);
  for(let n=2;n<=4;n++){await next.click();await p.getByText(`Page ${n} of 4`,{exact:true}).waitFor()}
  max=2;await p.getByRole('button',{name:'Refresh records'}).click();await p.getByText('Page 2 of 2',{exact:true}).waitFor();assert.equal(requested.at(-1),4);assert.equal(await next.isDisabled(),true);
  await previous.click();await p.getByText('Page 1 of 2',{exact:true}).waitFor();assert.equal(requested.at(-1),1);assert.equal(await previous.isDisabled(),true);
  await next.click();await p.getByText('Page 2 of 2',{exact:true}).waitFor();max=1;await p.getByRole('button',{name:'Refresh records'}).click();await p.getByText('Page 1 of 1',{exact:true}).waitFor();assert.equal(await previous.isDisabled(),true);assert.equal(await next.isDisabled(),true);
  for(const badPage of [0,1.5,2,'1']){invalid=badPage;const button=p.getByRole('button',{name:badPage===0?'Refresh records':'Try again'});const response=p.waitForResponse(r=>r.url().includes('/filestore?'));await button.click();await response;await p.getByRole('button',{name:'Try again'}).waitFor({state:'visible'});await p.getByRole('alert').getByText('The server returned an invalid file list.').waitFor();assert.equal(await pages.count(),0)}
  invalid=null;await p.getByRole('button',{name:'Try again'}).click();await p.getByText('Page 1 of 1',{exact:true}).waitFor();
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);
  console.log(`File pagination clamp and invalid-response checks passed at ${width}px`);await p.close();
 }
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
