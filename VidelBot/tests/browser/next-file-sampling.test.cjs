// Response interception and synthetic API; no live backend or MongoDB.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base=process.env.NEXT_PREVIEW_TEST_URL||'http://localhost:3001/next-preview';
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/tmp/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});browser.setDefaultTimeout(60000);try{
 for(const width of [320,390,1440]){
  const p=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'});let mode='old';
  await p.route('**/api/v1/workspaces/*/filestore?*',async route=>{
   if(route.request().method()!=='GET')return route.continue();
   const body={items:[{file_uid:'contract',filename:'contract.txt',size_bytes:8,category:'document',deleted:false}],total:1,total_bytes:8,page:1,pages:1,page_size:25};
   if(mode==='truncated')Object.assign(body,{sample_limit:2000,sample_truncated:true});
   if(mode==='complete')Object.assign(body,{sample_limit:2000,sample_truncated:false});
   if(mode==='invalid')Object.assign(body,{sample_limit:-1,sample_truncated:false});
   if(mode==='empty')Object.assign(body,{items:[],total:0,total_bytes:0,sample_limit:2000,sample_truncated:true});
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await p.goto(base);await p.getByLabel('One-time key or link').fill('l.valid');await p.getByRole('button',{name:'Continue to workspace'}).click();await p.getByRole('link',{name:'File Store',exact:true}).click();
  await p.getByRole('heading',{name:'contract.txt'}).waitFor();await p.getByText('Sample completeness unavailable on this server').waitFor();await p.getByText('1 matching records in sample',{exact:true}).waitFor();await p.getByText('8 B matching size in sample',{exact:true}).waitFor();
  mode='truncated';await p.getByRole('button',{name:'Refresh records'}).click();await p.getByText('Limited to 2000 sampled records before search; matches may be missing').waitFor();
  mode='complete';await p.getByRole('button',{name:'Refresh records'}).click();await p.getByText('Sample limit of 2000 was not exceeded').waitFor();
  mode='invalid';await p.getByRole('button',{name:'Refresh records'}).click();await p.getByText('Sample completeness unavailable on this server').waitFor();
  mode='empty';await p.getByRole('button',{name:'Refresh records'}).click();await p.getByRole('heading',{name:'No matching file records in sample'}).waitFor();await p.getByText('0 matching records in sample',{exact:true}).waitFor();await p.getByText('Limited to 2000 sampled records before search; matches may be missing').waitFor();
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`overflow at ${width}`);
  console.log(`File list compatibility and sample disclosure passed at ${width}px`);await p.close();
 }
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
