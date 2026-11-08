
(function(){
var area=document.getElementById('board'),status=document.getElementById('status'),epoch=0;
function say(s){status.textContent=s}function rnd(n){return Math.floor(Math.random()*n)}
// Storage belongs to this message on this device, not a server-side leaderboard.
function readBest(key){try{var v=localStorage.getItem('videl:'+key);if(v===null||!/^\d+$/.test(v))return null;var n=Number(v);return Number.isFinite(n)&&n>=0&&n<=100000000?n:null}catch(e){return null}}
function saveBest(key,value,lower){try{var old=readBest(key),v=Math.round(value);if(!Number.isFinite(v)||v<0||v>100000000)return old;if(old!==null&&(lower?old<=v:old>=v))return old;localStorage.setItem('videl:'+key,String(v));return readBest(key)}catch(e){return null}}
function clearBest(key){try{localStorage.removeItem('videl:'+key);return localStorage.getItem('videl:'+key)===null}catch(e){return false}}
var clearButton=document.getElementById('best-clear'),offline=document.getElementById('offline'),clearKey=null,clearAfter=null;
clearButton.addEventListener('click',function(){if(clearKey&&clearBest(clearKey)&&clearAfter)clearAfter()});
function bestControl(key,value,after){clearKey=value===null?null:key;clearAfter=after;clearButton.style.display=clearKey?'inline-block':'none';offline.style.display=clearKey?'none':''}
function shuffle(a){for(var i=a.length-1;i>0;i--){var j=rnd(i+1),t=a[i];a[i]=a[j];a[j]=t}return a}
function grid(items,cols,fn){area.style.gridTemplateColumns='repeat('+cols+',1fr)';while(area.firstChild)area.removeChild(area.firstChild);items.forEach(function(it,i){var b=document.createElement('button'),v=typeof it==='object'?it:{text:String(it)};b.textContent=v.text;b.setAttribute('aria-label',v.label||v.text||'Empty');if(v.on)b.className='active';else if(v.text==='X')b.className='player';else if(v.text==='O')b.className='opponent';else if(/^[0-9]+$/.test(v.text))b.className='tone'+(Number(v.text)%4);b.addEventListener('click',function(){fn(i)});area.appendChild(b)})}
function matrix(text,items,fn){grid(items,3,fn);var panel=document.createElement('div');panel.className='matrix';panel.setAttribute('aria-label',text.replace(/\n/g,'; '));panel.textContent=text;area.insertBefore(panel,area.firstChild)}
/* GAME_CODE */
document.getElementById('title').textContent=title;document.getElementById('restart').addEventListener('click',function(){epoch++;start()});start();
})();
