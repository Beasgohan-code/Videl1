(()=>{'use strict';
const $=id=>document.getElementById(id),tabs=document.querySelectorAll('[data-tab]');
let tab='tip',input='0',tip=10,people=1,count=0,unit=0,reverse=false;
const units=[['km','mi',v=>v*0.6213711922,v=>v/0.6213711922],['kg','lb',v=>v*2.2046226218,v=>v/2.2046226218],['°C','°F',v=>v*9/5+32,v=>(v-32)*5/9],['cm','in',v=>v/2.54,v=>v*2.54],['L','US gal',v=>v/3.785411784,v=>v*3.785411784]];
const number=v=>Number(Number(v).toFixed(4)).toString(),money=v=>Number(v).toFixed(2);
function button(text,action){const b=document.createElement('button');b.textContent=text;b.dataset.action=action;$('controls').appendChild(b)}
function render(){tabs.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tab===tab)));$('controls').replaceChildren();$('keys').style.display=tab==='count'?'none':'grid';
if(tab==='tip'){const amount=Number(input),total=amount*(1+tip/100);$('label').textContent='Bill amount · '+input;$('value').textContent=money(total/people)+' each';$('detail').textContent='Total '+money(total)+' · tip '+money(total-amount);button('Tip '+tip+'%','tip');button('People '+people,'people')}
else if(tab==='convert'){const u=units[unit],v=Number(input);$('label').textContent=input+' '+(reverse?u[1]:u[0]);$('value').textContent=number(reverse?u[3](v):u[2](v))+' '+(reverse?u[0]:u[1]);$('detail').textContent='Conversion is approximate';button('Units','unit');button('Swap','swap');button('+ / −','sign')}
else{$('label').textContent='Tap to count';$('value').textContent=String(count);$('detail').textContent='Count stays on this device';button('− 1','minus');button('Reset','reset');button('+ 1','plus')}}
document.querySelector('nav').addEventListener('click',e=>{const b=e.target.closest('button[data-tab]');if(!b)return;tab=b.dataset.tab;input='0';render()});
$('controls').addEventListener('click',e=>{const b=e.target.closest('button[data-action]');if(!b)return;switch(b.dataset.action){case'tip':tip=tip===25?0:tip+5;break;case'people':people=people===10?1:people+1;break;case'unit':unit=(unit+1)%units.length;reverse=false;break;case'swap':reverse=!reverse;break;case'sign':input=input[0]==='-'?input.slice(1):'-'+input;break;case'minus':count=Math.max(-999999,count-1);break;case'plus':count=Math.min(999999,count+1);break;case'reset':count=0;break}render()});
$('keys').addEventListener('click',e=>{const b=e.target.closest('button[data-key]');if(!b)return;const k=b.dataset.key;if(k==='clear')input='0';else if(k==='dot'){if(!input.includes('.'))input+='.'}else if(input.length<9)input=input==='0'?k:input==='-0'?'-'+k:input+k;render()});
render();if(window.TelegramWidget){try{window.TelegramWidget.ready()}catch(_){}}
})();
