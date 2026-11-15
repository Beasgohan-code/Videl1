'use client';

import {useEffect, useRef, useState} from 'react';
import Link from 'next/link';
import {Search, X} from 'lucide-react';

// This is deliberately a local route finder, not a search of workspace records.
const destinations = [
  {view: 'overview', href: '/', title: 'Overview', description: 'Workspace summary and recent bot records', terms: 'home dashboard'},
  {view: 'bots', href: '/bots', title: 'Bot management', description: 'Connect and manage bot records', terms: 'bot create connect'},
  {view: 'commands', href: '/commands', title: 'Commands', description: 'Browse command configuration', terms: 'command'},
  {view: 'files', href: '/files', title: 'File Store', description: 'Manage indexed workspace files', terms: 'file storage'},
  {view: 'analytics', href: '/analytics', title: 'Analytics', description: 'Explore available File Store metrics', terms: 'insights charts statistics'},
  {view: 'hosting', href: '/hosting', title: 'Hosting', description: 'View available runtime observations', terms: 'server runtime'},
  {view: 'templates', href: '/templates', title: 'Templates', description: 'Browse workspace templates', terms: 'library'},
  {view: 'notifications', href: '/notifications', title: 'Notifications', description: 'Review your workspace inbox', terms: 'inbox alerts'},
  {view: 'team', href: '/team', title: 'Team', description: 'View workspace membership', terms: 'members permissions roles'},
  {view: 'settings', href: '/settings', title: 'Personal settings', description: 'Manage account appearance preferences', terms: 'theme preferences'},
] as const;

type Props = {current:string; onNavigate:(event:React.MouseEvent<HTMLAnchorElement>, destination:string)=>boolean};

export default function NavigationPalette({current, onNavigate}:Props) {
  const dialog = useRef<HTMLDialogElement>(null), input = useRef<HTMLInputElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState('');
  function open() {
    if (dialog.current && !dialog.current.open) {dialog.current.showModal(); input.current?.focus()}
  }
  function close() { dialog.current?.close() }
  function containFocus(event:React.KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return;
    const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), a[href]'));
    if (!controls.length) return;
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus()}
    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus()}
  }
  useEffect(() => {
    function shortcut(event:KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k' && !event.isComposing) {
        event.preventDefault(); open();
      }
    }
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  const text = query.trim().toLocaleLowerCase();
  const matches = destinations.filter(item => `${item.title} ${item.description} ${item.terms}`.toLocaleLowerCase().includes(text));
  return <>
    <button ref={trigger} className="palette-trigger" type="button" onClick={open} aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K" aria-label="Find a Studio page">
      <Search size={16} aria-hidden="true"/><span>Find a page</span><kbd>Ctrl K</kbd>
    </button>
    <dialog ref={dialog} className="navigation-palette" aria-labelledby="palette-title" aria-describedby="palette-help" onKeyDown={containFocus} onClose={()=>{setQuery('');trigger.current?.focus()}}>
      <header><div><span className="kicker">VIDEL / NAVIGATION</span><h2 id="palette-title">Go somewhere.</h2></div><button type="button" className="icon-button" onClick={close} aria-label="Close page finder"><X size={17}/></button></header>
      <p id="palette-help">Find rebuilt Studio pages. This searches page names, not workspace records.</p>
      <form onSubmit={event=>{event.preventDefault();dialog.current?.querySelector<HTMLAnchorElement>('.palette-results a')?.click()}}>
        <label className="sr-only" htmlFor="palette-search">Search Studio pages</label>
        <div className="palette-input"><Search size={18} aria-hidden="true"/><input ref={input} id="palette-search" value={query} onChange={event=>setQuery(event.target.value)} maxLength={100} placeholder="Search pages…" autoComplete="off"/></div>
      </form>
      <div className="palette-results" aria-label="Matching pages">
        {matches.length ? matches.map(item=><Link href={item.href} key={item.view} aria-current={item.view===current?'page':undefined} onClick={event=>{if(onNavigate(event,item.view))close()}}><span><strong>{item.title}</strong><small>{item.description}</small></span>{item.view===current&&<span className="palette-current">Current</span>}</Link>) : <p className="palette-empty" role="status">No matching pages. Try another name.</p>}
      </div>
      <p className="palette-foot">Workspace access is checked by the server. Press Esc to close.</p>
    </dialog>
  </>;
}
