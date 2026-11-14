'use client';
import {useEffect,useRef,useState} from 'react';
import {Palette,ShieldCheck} from 'lucide-react';
import {personalSettings,request,type PersonalSettings as Settings,type Session} from '../lib/api';

type Props={session:Session;onError:(error:unknown)=>boolean;onDirty:(value:boolean)=>void;onPending:(value:boolean)=>void;onAppearance:(theme:Settings['theme'],fileView:Settings['fileView'],compactTables:boolean)=>void};
type Preferences=Pick<Settings,'theme'|'fileView'|'compactTables'>;
const values=(s:Settings):Preferences=>({theme:s.theme,fileView:s.fileView,compactTables:s.compactTables});
const same=(a:Preferences,b:Preferences)=>a.theme===b.theme&&a.fileView===b.fileView&&a.compactTables===b.compactTables;

export default function PersonalSettings({session,onError,onDirty,onPending,onAppearance}:Props){
 const [saved,setSaved]=useState<Settings|null>(null),[draft,setDraft]=useState<Preferences|null>(null),[loading,setLoading]=useState(false),[pending,setPending]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[refresh,setRefresh]=useState(0);
 const mounted=useRef(false),busy=useRef(false);
 const dirty=!!saved&&!!draft&&!same(saved,draft);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{onDirty(dirty)},[dirty,onDirty]);
 useEffect(()=>{
  const controller=new AbortController();setSaved(null);setDraft(null);setLoading(true);setError('');setNotice('');
  request('/api/v1/settings',session,{signal:controller.signal}).then(raw=>{if(!controller.signal.aborted){const data=personalSettings(raw);setSaved(data);setDraft(values(data));onAppearance(data.theme,data.fileView,data.compactTables)}}).catch(err=>{if(!controller.signal.aborted&&!onError(err))setError(err instanceof Error?err.message:'Could not load account settings.')}).finally(()=>{if(!controller.signal.aborted)setLoading(false)});
  return()=>controller.abort();
 },[session,onError,onAppearance,refresh]);
 function change(next:Partial<Preferences>){setDraft(previous=>previous?{...previous,...next}:previous);setNotice('');setError('')}
 function reload(){if(pending)return;if(dirty&&!window.confirm('Discard your unsaved personal settings and reload?'))return;setRefresh(n=>n+1)}
 async function save(event:React.FormEvent){
  event.preventDefault();if(!saved||!draft||!dirty||!saved.atomicFieldUpdates||busy.current)return;
  const appearance:Record<string,unknown>={},general:Record<string,unknown>={};
  if(draft.theme!==saved.theme)appearance.theme=draft.theme;
  if(draft.fileView!==saved.fileView)appearance.file_view=draft.fileView;
  if(draft.compactTables!==saved.compactTables)general.compact_tables=draft.compactTables;
  const patch:Record<string,unknown>={};if(Object.keys(appearance).length)patch.appearance=appearance;if(Object.keys(general).length)patch.general=general;
  const intended={...draft};busy.current=true;setPending(true);onPending(true);setError('');setNotice('');
  try{
   const raw=await request('/api/v1/settings',session,{method:'POST',body:patch});if(!mounted.current)return;
   // POST returns settings, while the capability was established by the GET.
   const result=personalSettings({...((raw&&typeof raw==='object'&&!Array.isArray(raw))?raw:{}),capabilities:{atomic_field_updates:true}});
   if(('theme' in appearance&&result.theme!==intended.theme)||('file_view' in appearance&&result.fileView!==intended.fileView)||('compact_tables' in general&&result.compactTables!==intended.compactTables))throw new Error('The returned preferences did not match your changes.');
   setSaved(result);setDraft(values(result));onAppearance(result.theme,result.fileView,result.compactTables);setNotice('Preferences saved for your account.');
  }catch(err){if(mounted.current&&!onError(err))setError((err instanceof Error?err.message:'Could not save preferences.')+' The result may be uncertain; reload and review before retrying.')}
  finally{busy.current=false;if(mounted.current){setPending(false);onPending(false)}}
 }
 return <section className="studio-page personal-page" aria-labelledby="personal-title">
  <header className="studio-hero"><div><span className="kicker">ACCOUNT / PREFERENCES</span><h1 id="personal-title">Make it<br/><em>yours.</em></h1><p>Set a few personal preferences for your account, across all your workspaces.</p></div><button className="secondary" type="button" onClick={reload} disabled={loading||pending}>Reload settings</button></header>
  <div className="studio-note"><ShieldCheck size={19}/><p><strong>Personal, not workspace-wide.</strong> These settings belong to your account. Changes to the same field from another session may overwrite yours; reload before editing if someone else is using the account.</p></div>
  {error&&<p className="notice error" role="alert">{error}</p>}{notice&&<p className="notice" role="status">{notice}</p>}
  {loading?<p className="loading" role="status">Loading personal settings…</p>:saved&&draft?<div className="personal-layout"><form className="personal-form" onSubmit={save}><section className="hosting-panel personal-card"><div className="personal-heading"><span className="bot-icon"><Palette size={19}/></span><div><span className="kicker">DISPLAY</span><h2>Appearance</h2><p>These values are saved in your account settings. Theme, file view and Analytics table spacing apply in this preview.</p></div></div>
   {!saved.atomicFieldUpdates&&<p className="notice">This backend has not advertised safe field-level settings updates. Editing is disabled here; your current settings remain visible.</p>}
   <div className="personal-fields"><label>Theme preference<select value={draft.theme} disabled={pending||!saved.atomicFieldUpdates} onChange={e=>change({theme:e.target.value as Preferences['theme']})}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option></select><small>Applied after saving. System follows this device’s color scheme.</small></label><label>File view preference<select value={draft.fileView} disabled={pending||!saved.atomicFieldUpdates} onChange={e=>change({fileView:e.target.value as Preferences['fileView']})}><option value="list">List</option><option value="grid">Grid</option></select><small>Applied to File Store after saving.</small></label></div>
   <label className="personal-check"><input type="checkbox" checked={draft.compactTables} disabled={pending||!saved.atomicFieldUpdates} onChange={e=>change({compactTables:e.target.checked})}/><span><strong>Compact tables</strong><small>Applied to the Analytics daily data table after saving.</small></span></label>
   <div className="personal-actions"><button className="primary" type="submit" disabled={!dirty||pending||!saved.atomicFieldUpdates}>{pending?'Saving…':'Save preferences'}</button><span aria-live="polite">{dirty?'Unsaved changes':'No unsaved changes'}</span></div>
  </section></form><aside className="hosting-panel personal-aside"><span className="kicker">GOOD TO KNOW</span><h2>One account,<br/>many workspaces.</h2><p>These preferences are personal, not shared with your team. Other account settings and reset are still available in the existing Studio.</p><a className="quiet-link" href="/app" onClick={e=>{if((dirty||pending)&&!window.confirm(pending?'A pending save may still finish. Leave this view?':'Discard unsaved personal settings?'))e.preventDefault()}}>Open existing Studio ↗</a></aside></div>:null}
 </section>;
}
