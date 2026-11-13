'use client';
import {useEffect,useId,useState} from 'react';
import {ApiError,request,type Session} from '../lib/api';

// A closed list prevents unknown historical fields, settings and secrets from
// being rendered. Missing or malformed flags must never be inferred as off.
const features=[
  ['filestore_enabled','File Store'],['batch_links','Batch links'],['permanent_link','Permanent links'],
  ['force_subscribe','Force subscribe'],['force_sub_request_mode','Join-request mode'],['auto_delete','Auto-delete'],
  ['protect_content','Protect content'],['media_encoder','Media encoder'],['auto_watermark','Auto-watermark'],['group_moderation','Group moderation'],
] as const;
type Key=typeof features[number][0];
type Summary=Record<Key,boolean|null>;
function parse(value:unknown,botId:string):Summary {
  if(!value||typeof value!=='object'||!('summary' in value)||value.summary!=='features-v1'||!('bot_id' in value)||value.bot_id!==botId||!('features' in value)||!value.features||typeof value.features!=='object'||Array.isArray(value.features))throw new Error('Invalid feature summary');
  const fields=value.features as Record<string,unknown>,result={} as Summary;
  for(const [key] of features){const flag=fields[key];if(flag!==null&&typeof flag!=='boolean')throw new Error('Invalid feature flag');result[key]=flag}
  return result;
}
type Props={workspace:string;botId:string;session:Session;onError:(error:unknown)=>boolean;onClose:()=>void};
export default function BotFeatureSummary({workspace,botId,session,onError,onClose}:Props){
  const title=useId(),[data,setData]=useState<Summary|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[refresh,setRefresh]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();setData(null);setError('');setLoading(true);
    request(`/api/v1/workspaces/${encodeURIComponent(workspace)}/bots/${encodeURIComponent(botId)}/feature-summary`,session,{signal:controller.signal})
      .then(raw=>{if(!controller.signal.aborted)setData(parse(raw,botId))})
      .catch(err=>{if(!controller.signal.aborted&&!onError(err))setError(err instanceof ApiError&&err.status===404?'This summary is unavailable on this backend, or the bot record no longer exists.':err instanceof ApiError&&err.status===403?'You no longer have access to these bot settings.':'Could not load saved features. Try again.')})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
    return()=>controller.abort();
  },[workspace,botId,session,onError,refresh]);
  return <section className="bot-features" aria-labelledby={title}>
    <div className="bot-features-head"><div><span className="kicker">SAVED CONFIGURATION</span><h4 id={title}>Feature flags</h4></div><div className="bot-features-actions"><button className="secondary" type="button" aria-disabled={loading} style={{opacity:loading?0.55:1}} onClick={()=>{if(!loading)setRefresh(n=>n+1)}}>{error?'Try again':'Refresh features'}</button><button className="secondary" type="button" onClick={onClose}>Close features</button></div></div>
    <p>Known flags in this record only. They do not confirm deployment, permissions or a running worker. Other settings and credentials are not shown.</p>
    {loading?<p role="status">Loading saved features…</p>:error?<p className="form-error" role="alert">{error}</p>:data&&<ul className="bot-feature-list">{features.map(([key,label])=><li key={key}><span>{label}</span><strong>{data[key]===true?'Enabled':data[key]===false?'Disabled':'Unavailable'}</strong></li>)}</ul>}
  </section>;
}
