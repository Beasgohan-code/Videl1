
(function(){
var area=document.getElementById('board'),status=document.getElementById('status'),epoch=0;
function say(s){status.textContent=s}function rnd(n){return Math.floor(Math.random()*n)}
function shuffle(a){for(var i=a.length-1;i>0;i--){var j=rnd(i+1),t=a[i];a[i]=a[j];a[j]=t}return a}
function grid(items,cols,fn){area.style.gridTemplateColumns='repeat('+cols+',1fr)';while(area.firstChild)area.removeChild(area.firstChild);items.forEach(function(it,i){var b=document.createElement('button'),v=typeof it==='object'?it:{text:String(it)};b.textContent=v.text;b.setAttribute('aria-label',v.label||v.text||'Empty');if(v.on)b.className='active';else if(v.text==='X')b.className='player';else if(v.text==='O')b.className='opponent';else if(/^[0-9]+$/.test(v.text))b.className='tone'+(Number(v.text)%4);b.addEventListener('click',function(){fn(i)});area.appendChild(b)})}
function matrix(text,items,fn){grid(items,3,fn);var panel=document.createElement('div');panel.className='matrix';panel.setAttribute('aria-label',text.replace(/\n/g,'; '));panel.textContent=text;area.insertBefore(panel,area.firstChild)}
/* GAME_CODE */
document.getElementById('title').textContent=title;document.getElementById('restart').addEventListener('click',function(){epoch++;start()});start();
})();
