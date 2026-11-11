'use client';
import {useEffect,useRef,useState} from 'react';
import {ApiError,request,type Session} from '../lib/api';

type Item={telegram_bot_id:string;username:string;enabled:boolean};
type Page={bots:Item[];next:string|null;legacy:boolean;truncated:boolean};
const maxId=9223372036854775807n;
function validId(value:unknown):value is string {
  return typeof value==='string'&&/^[1-9][0-9]{0,18}$/.test(value)&&BigInt(value)<=maxId;
}
function parsePage(value:unknown,after:string|null,legacy:boolean):Page {
  if(typeof value!=='object'||value===null||!('bots' in value)||!Array.isArray(value.bots)||value.bots.length>100)throw new Error('Invalid page');
  const bots:Item[]=value.bots.map((item:unknown)=>{
    if(typeof item!=='object'||item===null||!('telegram_bot_id' in item)||!validId(item.telegram_bot_id)||!('username' in item)||typeof item.username!=='string'||!('enabled' in item)||typeof item.enabled!=='boolean')throw new Error('Invalid page');
    return {telegram_bot_id:item.telegram_bot_id,username:item.username,enabled:item.enabled};
  });
  if(legacy){if(!('truncated' in value)||typeof value.truncated!=='boolean')throw new Error('Invalid page');return {bots,next:null,legacy:true,truncated:value.truncated};}
  if(!('pagination' in value)||value.pagination!=='id-v1'||!('next_after' in value)||(value.next_after!==null&&!validId(value.next_after)))throw new Error('Invalid page');
  let last=after?BigInt(after):0n;
  for(const bot of bots){const id=BigInt(bot.telegram_bot_id);if(id<=last)throw new Error('Invalid order');last=id;}
  if(value.next_after!==null&&(!bots.length||value.next_after!==bots[bots.length-1].telegram_bot_id))throw new Error('Invalid cursor');
  return {bots,next:value.next_after,legacy:false,truncated:false};
}

type Props={session:Session;onError:(error:unknown)=>boolean};
export default function LegacyCloneDirectory({session,onError}:Props){
  const [page,setPage]=useState<Page|null>(null),[cursor,setCursor]=useState<string|null>(null),[history,setHistory]=useState<(string|null)[]>([]);
  const [error,setError]=useState(''),[loading,setLoading]=useState(false);
  const busy=useRef(false),mounted=useRef(false);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  async function load(after:string|null,kind:'start'|'next'|'previous'){
    if(busy.current)return;busy.current=true;setLoading(true);setError('');
    try{
      let result:Page;
      try{const raw=await request('/api/v1/account/legacy-bots/pages'+(after?'?after='+encodeURIComponent(after):''),session);result=parsePage(raw,after,false)}
      catch(e){if(kind!=='start'||!(e instanceof ApiError)||e.status!==404)throw e;result=parsePage(await request('/api/v1/account/legacy-bots',session),null,true)}
      if(!mounted.current)return;
      setPage(result);setCursor(after);
      setHistory(kind==='start'?[]:kind==='next'?[...history,cursor]:history.slice(0,-1));
    }catch(e){if(mounted.current&&!onError(e)){
      if(e instanceof ApiError&&e.status===403){setPage(null);setHistory([]);setCursor(null);setError('Sign in with your own Telegram link to see your clones.')}
      else setError('Could not load this page. The backend may be unavailable or not yet updated; try again.');
    }}finally{busy.current=false;if(mounted.current)setLoading(false)}
  }
  return <div className="studio-note"><div><strong>Telegram /clone registrations</strong><p>These belong to your Telegram account, not this workspace. This read-only list does not import or change bots. Sign in with your own Telegram link. Registrations can change between pages; refresh to start again.</p>
    <button className="secondary" type="button" disabled={loading} onClick={()=>load(null,'start')}>{loading?'Loading…':page?'Refresh my Telegram clones':'Show my Telegram clones'}</button>
    <div role="status" aria-live="polite">{error&&<p>{error}</p>}{page&&(page.bots.length?<><p>Page {history.length+1} · {page.bots.length} registrations</p><ul>{page.bots.map(b=><li key={b.telegram_bot_id}>{b.username?'@'+b.username:'Bot '+b.telegram_bot_id} — {b.enabled?'enabled in registration':'disabled in registration'}</li>)}</ul>{page.legacy&&page.truncated&&<p>Showing the first 100 registrations. More pages require an updated backend.</p>}</>:<p>{history.length?'No registrations remain on this page. Refresh to update the list.':'No non-deleted registrations found for this account on this backend. They may be on another database.'}</p>)}</div>
    {page&&!page.legacy&&<div className="form-actions">{history.length>0&&<button className="secondary" type="button" disabled={loading} onClick={()=>load(history[history.length-1],'previous')}>Previous page</button>}{page.next&&<button className="secondary" type="button" disabled={loading} onClick={()=>load(page.next,'next')}>Next page</button>}</div>}
  </div></div>;
}
