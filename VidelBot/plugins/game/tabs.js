// A few related games share one immutable widget: switches are local, no queries.
var title='Puzzle Trio',activeGame=0,resetGame=null,tabs=document.getElementById('tabs');
/* TAB_MOUNTS */
function selectGame(i){epoch++;activeGame=i;bestControl('',null,null);for(var j=0;j<tabs.children.length;j++){tabs.children[j].className=j===i?'selected':'';tabs.children[j].setAttribute('aria-pressed',j===i?'true':'false')}mounts[i]()}
function start(){if(resetGame)resetGame();else selectGame(0)}
['Tiles','Mines','Maze'].forEach(function(label,i){var b=document.createElement('button');b.textContent=label;b.addEventListener('click',function(){selectGame(i)});tabs.appendChild(b)});
