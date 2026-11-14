'use client';
import {useEffect, useRef, useState} from 'react';
import {ApiError, request, type Bot, type Session} from '../lib/api';

type Profile = {display_name:string; description:string; category:string; tags:string[]};
type RemoteProfile = Omit<Profile,'category'|'tags'> & {category:string|null; tags:string[]|null};
type Props = {bot:Bot; workspace:string; session:Session; onDirty:(dirty:boolean)=>void; onPending:(pending:boolean)=>void; onError:(error:unknown)=>boolean; onClose:()=>void; onSaved:()=>void};
const sameTags=(a:string[],b:string[])=>a.length===b.length && a.every((tag,i)=>tag===b[i]);
const sameProfile=(a:Profile,b:Profile)=>a.display_name===b.display_name&&a.description===b.description&&a.category===b.category&&sameTags(a.tags,b.tags);

// Extract only the fields needed by this editor; do not retain other configuration.
function profileResponse(value:unknown, botId:string):RemoteProfile {
  if (!value || typeof value !== 'object' || !('bot' in value) || !value.bot || typeof value.bot !== 'object') throw new Error('Invalid bot record');
  const bot = value.bot as Record<string,unknown>;
  if (bot.bot_id !== botId || typeof bot.display_name !== 'string' || (bot.description != null && typeof bot.description !== 'string')) throw new Error('Invalid bot record');
  const category=bot.category==null?'':typeof bot.category==='string'?bot.category:null;
  const tags=bot.tags==null?[]:Array.isArray(bot.tags)&&bot.tags.every((t:unknown)=>typeof t==='string')?bot.tags as string[]:null;
  return {display_name:bot.display_name, description:typeof bot.description === 'string' ? bot.description : '', category, tags};
}
function capabilities(raw:unknown):{conditional:boolean; fields:string[]} {
  if(!raw || typeof raw!=='object' || !('capabilities' in raw) || !raw.capabilities || typeof raw.capabilities!=='object')return {conditional:false,fields:[]};
  const caps=raw.capabilities as Record<string,unknown>;
  return {conditional:caps.conditional_profile_update===true, fields:Array.isArray(caps.conditional_profile_fields)?caps.conditional_profile_fields.filter((v:unknown):v is string=>typeof v==='string'):[]};
}

export default function BotProfileEditor({bot,workspace,session,onDirty,onPending,onError,onClose,onSaved}:Props) {
  const initial=useRef<Profile>({display_name:bot.display_name || '',description:bot.description || '',category:bot.category || '',tags:[...(bot.tags || [])]});
  const [draft,setDraft]=useState<Profile>(()=>({...initial.current,tags:[...initial.current.tags]}));
  const [pending,setPending]=useState(false), [error,setError]=useState('');
  const [fieldState,setFieldState]=useState<{checking:boolean; category:boolean; tags:boolean}>({checking:true,category:false,tags:false});
  const mounted=useRef(false), busy=useRef(false);
  const dirty=!sameProfile(draft,initial.current),endpoint=`/api/v1/workspaces/${encodeURIComponent(workspace)}/bots/${encodeURIComponent(bot.bot_id)}`;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  useEffect(()=>{
    const controller=new AbortController();setFieldState({checking:true,category:false,tags:false});
    request(endpoint,session,{signal:controller.signal}).then(raw=>{
      if(controller.signal.aborted)return;
      const profile=profileResponse(raw,bot.bot_id),caps=capabilities(raw);
      setFieldState({checking:false,category:caps.conditional&&caps.fields.includes('category')&&profile.category!==null&&profile.category.length<=40,
        tags:caps.conditional&&caps.fields.includes('tags')&&profile.tags!==null&&profile.tags.length<=12&&profile.tags.every(t=>t.length<=24)});
    }).catch(err=>{if(!controller.signal.aborted){if(!onError(err))setFieldState({checking:false,category:false,tags:false})}});
    return()=>controller.abort();
  },[endpoint,session,bot.bot_id,onError]);
  function update(changes:Partial<Profile>) {
    const next={...draft,...changes};setDraft(next);setError('');onDirty(!sameProfile(next,initial.current));
  }
  function cancel(){if(pending)return;if(dirty&&!window.confirm('Discard unsaved bot record changes?'))return;onDirty(false);onClose()}
  async function save(event:React.FormEvent) {
    event.preventDefault();if(busy.current || !dirty)return;
    const name=draft.display_name.trim(),description=draft.description.trim(),category=draft.category.trim();
    if(name.length<2 || name.length>80 || description.length>300 || category.length>40){setError('Use a name of 2–80 characters, a description of at most 300 and a category of at most 40.');return}
    const patch:Partial<Profile>={};
    if(draft.display_name!==initial.current.display_name && name!==initial.current.display_name)patch.display_name=name;
    if(draft.description!==initial.current.description && description!==initial.current.description)patch.description=description;
    if(draft.category!==initial.current.category && category!==initial.current.category)patch.category=category;
    if(!sameTags(draft.tags,initial.current.tags)){
      const tags=draft.tags.map(t=>t.trim());
      if(tags.length>12||tags.some(t=>!t||t.length>24)){setError('Use up to 12 non-empty tags of at most 24 characters each.');return}
      if(!sameTags(tags,initial.current.tags))patch.tags=tags;
    }
    if(!Object.keys(patch).length){onDirty(false);onClose();return}
    busy.current=true;setPending(true);onPending(true);setError('');
    let attemptedWrite=false,saved=false;
    try {
      const raw=await request(endpoint,session);
      if(!mounted.current)return;
      const current=profileResponse(raw,bot.bot_id),caps=capabilities(raw);
      if(!caps.conditional || (patch.category!==undefined&&!caps.fields.includes('category')) || (patch.tags!==undefined&&!caps.fields.includes('tags'))) {
        setError('This server does not support safe conditional edits for these fields. Your draft has not been saved.');return;
      }
      if((patch.display_name!==undefined && current.display_name!==initial.current.display_name) ||
         (patch.description!==undefined && current.description!==initial.current.description) ||
         (patch.category!==undefined && current.category!==initial.current.category) ||
         (patch.tags!==undefined && (current.tags===null || !sameTags(current.tags,initial.current.tags)))) {
        setError('This record changed since you opened it. Cancel and refresh before editing again. Your draft has not been saved.');return;
      }
      attemptedWrite=true;
      const expected:Partial<Profile>={};
      if(patch.display_name!==undefined)expected.display_name=initial.current.display_name;
      if(patch.description!==undefined)expected.description=initial.current.description;
      if(patch.category!==undefined)expected.category=initial.current.category;
      if(patch.tags!==undefined)expected.tags=initial.current.tags;
      const result=profileResponse(await request(`${endpoint}/profile`,session,{method:'PATCH',body:{...patch,expected}}),bot.bot_id);
      if(!mounted.current)return;
      if((patch.display_name!==undefined && result.display_name!==patch.display_name) ||
         (patch.description!==undefined && result.description!==patch.description) ||
         (patch.category!==undefined && result.category!==patch.category) ||
         (patch.tags!==undefined && (result.tags===null || !sameTags(result.tags,patch.tags)))) throw new Error('Saved record did not match');
      onDirty(false);saved=true;
    } catch(err) {
      if(mounted.current && !onError(err))setError(attemptedWrite && err instanceof ApiError && err.status===409?'This record changed or is no longer available. Cancel and refresh before editing again. Your draft has not been saved.':attemptedWrite && err instanceof ApiError && (err.status===404||err.status===400)?'This server could not safely apply the selected fields. Refresh and review the record before retrying.':attemptedWrite?'Could not confirm the save. Refresh the bot list before retrying; the result may be uncertain.':'Could not check the current record. Your draft has not been saved.');
    } finally {busy.current=false;if(mounted.current){setPending(false);onPending(false);if(saved)onSaved()}}
  }
  return <form className="bot-profile-editor" onSubmit={save} aria-label="Edit bot record">
    <div><span className="kicker">BOT RECORD / EDIT</span><h3>Edit saved details.</h3><p>These fields change the Studio record, not the bot’s Telegram profile or running process. Only changed fields are sent with their expected values. Older clients or workers may still make unconditional changes.</p></div>
    <div className="bot-profile-fields"><label>Record name<input autoFocus value={draft.display_name} onChange={e=>update({display_name:e.target.value})} minLength={2} maxLength={80} required disabled={pending}/></label><label>Record description<textarea value={draft.description} onChange={e=>update({description:e.target.value})} maxLength={300} rows={3} disabled={pending}/></label>
      <label>Category<input value={draft.category} onChange={e=>update({category:e.target.value})} maxLength={40} disabled={pending||!fieldState.category}/></label>
      <fieldset className="bot-tags" disabled={pending||!fieldState.tags}><legend>Tags <small>up to 12</small></legend>
        {draft.tags.map((tag,index)=><div className="bot-tag-row" key={index}><label><span className="sr-only">Tag {index+1}</span><input value={tag} onChange={e=>update({tags:draft.tags.map((t,i)=>i===index?e.target.value:t)})} maxLength={24} placeholder={`Tag ${index+1}`}/></label><button type="button" className="secondary" aria-label={`Remove tag ${index+1}`} onClick={()=>update({tags:draft.tags.filter((_,i)=>i!==index)})}>Remove</button></div>)}
        <button type="button" className="secondary" disabled={draft.tags.length>=12} onClick={()=>update({tags:[...draft.tags,'']})}>Add tag</button>
      </fieldset>
      {(!fieldState.category||!fieldState.tags)&&<p className="form-hint" role="status">{fieldState.checking?'Checking category and tag support…':'Category or tags cannot be edited here on this server or for this record. Name and description can still be edited when supported.'}</p>}
      {error&&<p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button className="primary" disabled={pending||!dirty}>{pending?'Checking and saving…':'Save record'}</button><button type="button" className="secondary" disabled={pending} onClick={cancel}>Cancel editing</button></div>
    </div>
  </form>;
}
