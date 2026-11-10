(()=>{'use strict';
const $=id=>document.getElementById(id),labels={active:'Active user-days',new:'New users',jobs:'Completed jobs',stars:'Stars received'};
const allDays=typeof DATA.days==='string'?Array.from({length:90},(_,i)=>new Date(Date.parse(DATA.days+'T00:00:00Z')+i*86400000).toISOString().slice(0,10)):DATA.days;
function unpack(encoded){const bytes=atob(encoded),out=[];let n=0,m=1;for(let i=0;i<bytes.length;i++){const b=bytes.charCodeAt(i);n+=(b&127)*m;if(b<128){out.push(n);n=0;m=1}else m*=128}return out}
const series={};['active','new','jobs','stars'].forEach(k=>{const v=DATA[k];if(Array.isArray(v)){const g=[];for(let i=0;i<90;i+=3)g.push(v.slice(i,i+3).reduce((a,b)=>a+b,0));series[k]={r:v.slice(-30),g,p:v.slice(-60,-30).reduce((a,b)=>a+b,0),x:Math.max(...v,0)}}else series[k]={r:unpack(v.r),g:unpack(v.g),p:v.p,x:v.x}});
let metric='active',range=[7,30,90].includes(DATA.default_range)?DATA.default_range:30,selected=-1;
const fmt=n=>n.toLocaleString('en-US');
function render(){
 document.querySelectorAll('[data-metric]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.metric===metric)));
 document.querySelectorAll('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.range)===range)));
 const source=series[metric],dates=allDays.slice(-range),vals=range===90?source.g:source.r.slice(-range),sum=vals.reduce((a,b)=>a+b,0),peak=range===90?source.x:Math.max(...vals,0);
 $('metric-label').textContent=labels[metric];$('total').textContent=fmt(sum);$('peak').textContent=fmt(peak);$('from').textContent=dates[0];$('to').textContent=dates[dates.length-1];$('stamp').textContent='As of '+DATA.generated+' UTC';
 if(range*2>allDays.length)$('compare').textContent='Prior '+range+' days unavailable';
 else{const prior=range===30?source.p:source.r.slice(-14,-7).reduce((a,b)=>a+b,0),diff=sum-prior;
  const pct=prior?Math.abs(diff/prior*100):0,change=pct<0.05?'<0.1':pct.toFixed(1);
  $('compare').textContent=prior===0?(sum===0?'No change vs prior '+range+' days':'New activity vs prior 0'):(diff===0?'No change vs prior '+range+' days':(diff>0?'Up ':'Down ')+change+'% vs prior '+range+' days');}
 const bucket=range===90?3:1,groups=vals.map((value,i)=>({start:dates[i*bucket],end:dates[Math.min((i+1)*bucket,dates.length)-1],value}));
 if(selected<0||selected>=groups.length)selected=groups.length-1;
 const max=Math.max(...groups.map(g=>g.value),1);$('chart').replaceChildren();groups.forEach((g,i)=>{const bar=document.createElement('div');bar.className='bar'+(i===selected?' selected':'');bar.style.height=(g.value?Math.max(2,Math.round(g.value/max*100)):0)+'%';$('chart').appendChild(bar)});
 $('chart').setAttribute('aria-label',labels[metric]+' chart, '+range+' days, total '+sum);
 const g=groups[selected];$('selected').textContent=(g.start===g.end?g.start:g.start+' – '+g.end)+' · '+fmt(g.value);
 document.querySelectorAll('[data-step]').forEach(b=>{const dir=Number(b.dataset.step),edge=dir<0?selected===0:selected===groups.length-1;b.setAttribute('aria-disabled',String(edge));b.textContent=edge?(dir<0?'First':'Latest'):(dir<0?'‹ Prev':'Next ›')});
 $('detail').textContent=(metric==='active'?'User-days, not unique people. ':metric==='stars'?'Non-refunded Stars, not profit. ':'Recorded bot-wide totals. ')+(bucket===3?'Bars group 3 days.':'Each bar is 1 day.');
}
document.querySelector('.metrics').addEventListener('click',e=>{const b=e.target.closest('button[data-metric]');if(b){metric=b.dataset.metric;selected=-1;render()}});
document.querySelector('.periods').addEventListener('click',e=>{const b=e.target.closest('button[data-range]');if(b){range=Number(b.dataset.range);selected=-1;render()}});
document.querySelector('.inspect').addEventListener('click',e=>{const b=e.target.closest('button[data-step]');if(b&&b.getAttribute('aria-disabled')!=='true'){selected+=Number(b.dataset.step);render()}});
render();if(window.TelegramWidget){try{window.TelegramWidget.ready()}catch(_){}}
})();
