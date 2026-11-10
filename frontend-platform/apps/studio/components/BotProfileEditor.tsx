'use client';
import {useEffect, useRef, useState} from 'react';
import {request, type Bot, type Session} from '../lib/api';

type Profile = {display_name:string; description:string};
type Props = {bot:Bot; workspace:string; session:Session; onDirty:(dirty:boolean)=>void; onPending:(pending:boolean)=>void; onError:(error:unknown)=>boolean; onClose:()=>void; onSaved:()=>void};

// Do not retain the rest of the bot response, which can include other configuration.
function profileResponse(value:unknown, botId:string):Profile {
  if (!value || typeof value !== 'object' || !('bot' in value) || !value.bot || typeof value.bot !== 'object') throw new Error('Invalid bot record');
  const bot = value.bot as Record<string,unknown>;
  if (bot.bot_id !== botId || typeof bot.display_name !== 'string' || (bot.description != null && typeof bot.description !== 'string')) throw new Error('Invalid bot record');
  return {display_name:bot.display_name, description:typeof bot.description === 'string' ? bot.description : ''};
}

export default function BotProfileEditor({bot,workspace,session,onDirty,onPending,onError,onClose,onSaved}:Props) {
  const initial = useRef<Profile>({display_name:bot.display_name || '', description:bot.description || ''});
  const [name,setName] = useState(initial.current.display_name), [description,setDescription] = useState(initial.current.description);
  const [pending,setPending] = useState(false), [error,setError] = useState('');
  const mounted=useRef(false), busy=useRef(false);
  const dirty=name.trim()!==initial.current.display_name || description.trim()!==initial.current.description;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  function update(nextName:string,nextDescription:string) {
    setName(nextName);setDescription(nextDescription);setError('');
    onDirty(nextName.trim()!==initial.current.display_name || nextDescription.trim()!==initial.current.description);
  }
  function cancel(){if(pending)return;if(dirty&&!window.confirm('Discard unsaved bot record changes?'))return;onDirty(false);onClose()}
  async function save(event:React.FormEvent) {
    event.preventDefault();if(busy.current || !dirty)return;
    const next={display_name:name.trim(), description:description.trim()};
    if(next.display_name.length<2 || next.display_name.length>80 || next.description.length>300){setError('Use a name of 2–80 characters and a description of at most 300 characters.');return}
    const patch:Partial<Profile>={};
    if(next.display_name!==initial.current.display_name)patch.display_name=next.display_name;
    if(next.description!==initial.current.description)patch.description=next.description;
    busy.current=true;setPending(true);onPending(true);setError('');
    const endpoint=`/api/v1/workspaces/${encodeURIComponent(workspace)}/bots/${encodeURIComponent(bot.bot_id)}`;
    let attemptedWrite=false, saved=false;
    try {
      const current=profileResponse(await request(endpoint,session),bot.bot_id);
      if(!mounted.current)return;
      if((patch.display_name!==undefined && current.display_name!==initial.current.display_name) || (patch.description!==undefined && current.description!==initial.current.description)) {
        setError('This record changed since you opened it. Cancel and refresh before editing again. Your draft has not been saved.');return;
      }
      attemptedWrite=true;
      const result=profileResponse(await request(endpoint,session,{method:'PATCH',body:patch}),bot.bot_id);
      if(!mounted.current)return;
      if((patch.display_name!==undefined && result.display_name!==patch.display_name) || (patch.description!==undefined && result.description!==patch.description)) throw new Error('Saved record did not match');
      onDirty(false);saved=true;
    } catch(err) {
      if(mounted.current && !onError(err))setError(attemptedWrite?'Could not confirm the save. Refresh the bot list before retrying; the result may be uncertain.':'Could not check the current record. Your draft has not been saved.');
    } finally {busy.current=false;if(mounted.current){setPending(false);onPending(false);if(saved)onSaved()}}
  }
  return <form className="bot-profile-editor" onSubmit={save} aria-label="Edit bot record">
    <div><span className="kicker">BOT RECORD / EDIT</span><h3>Edit saved details.</h3><p>These fields change the Studio record, not the bot’s Telegram profile or running process. Only changed fields are sent. Concurrent edits can still overwrite one another.</p></div>
    <div className="bot-profile-fields"><label>Record name<input autoFocus value={name} onChange={e=>update(e.target.value,description)} minLength={2} maxLength={80} required disabled={pending}/></label><label>Record description<textarea value={description} onChange={e=>update(name,e.target.value)} maxLength={300} rows={3} disabled={pending}/></label>
      {error&&<p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button className="primary" disabled={pending||!dirty}>{pending?'Checking and saving…':'Save record'}</button><button type="button" className="secondary" disabled={pending} onClick={cancel}>Cancel editing</button></div>
    </div>
  </form>;
}
