'use client';

export type BotListState = 'loading'|'ready'|'error';

type Props = {state:BotListState; onRetry:()=>void};

/** A failed read is not an empty directory; don't surface server diagnostics. */
export default function BotListAvailability({state,onRetry}:Props){
  return <div className="bot-list-availability" aria-live="polite">
    {state==='loading'&&<p role="status">Loading workspace bot records…</p>}
    {state==='error'&&<p role="alert">Could not load workspace bot records. Other workspace tools may still be available.</p>}
    <button type="button" className="secondary" aria-disabled={state==='loading'} onClick={()=>{if(state!=='loading')onRetry()}}>Refresh workspace bots</button>
  </div>;
}
