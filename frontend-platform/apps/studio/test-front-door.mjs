import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';

const config=JSON.parse(await readFile(new URL('./vercel.json',import.meta.url),'utf8'));
assert.deepEqual(config.redirects,[
  {source:'/',destination:'/app',permanent:false},
  {source:'/admin',destination:'https://videl1.onrender.com/admin',permanent:false},
]);
const headers=Object.fromEntries(config.headers[0].headers.map(({key,value})=>[key.toLowerCase(),value]));
assert.equal(config.headers[0].source,'/(.*)');
assert.equal(headers['cache-control'],'no-store');
assert.equal(headers['x-content-type-options'],'nosniff');
assert.equal(headers['referrer-policy'],'no-referrer');
const inspect=`import('./next.config.mjs').then(async ({default:c})=>console.log(JSON.stringify({basePath:c.basePath,rewrites:await c.rewrites()})))`;
function load(extra){return spawnSync(process.execPath,['--input-type=module','-e',inspect],{cwd:new URL('.',import.meta.url),env:{...process.env,VERCEL:'1',VIDEL_BACKEND_ORIGIN:'',...extra},encoding:'utf8'});}
assert.notEqual(load({}).status,0,'Vercel build requires an explicit backend');
for(const origin of ['https://user:pass@example.com','https://example.com/path','ftp://example.com','http://example.com','https://example.com?token=secret']) assert.notEqual(load({VIDEL_BACKEND_ORIGIN:origin}).status,0,'Reject unsafe origins');
const result=load({VIDEL_BACKEND_ORIGIN:'https://videl1.onrender.com'});
assert.equal(result.status,0,result.stderr);
assert.deepEqual(JSON.parse(result.stdout),{basePath:'/next-preview',rewrites:[
 {source:'/api/v1/:path*',destination:'https://videl1.onrender.com/api/v1/:path*',basePath:false},
 {source:'/app',destination:'https://videl1.onrender.com/app',basePath:false},
 {source:'/app/:path*',destination:'https://videl1.onrender.com/app/:path*',basePath:false},
]});
console.log('Studio Vercel front-door config checks passed; Vercel route precedence is not tested.');
