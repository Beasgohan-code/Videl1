
(function(){
var game='tic',board=[],done=false,first=-1,locked=false,moves=0,epoch=0;
var area=document.getElementById('board'),status=document.getElementById('status');
var lines=[[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
function winner(b){for(var i=0;i<lines.length;i++){var l=lines[i];if(b[l[0]]&&b[l[0]]===b[l[1]]&&b[l[0]]===b[l[2]])return b[l[0]]}return ''}
function toggle(i){var x=i%3,y=Math.floor(i/3);for(var n=0;n<9;n++)if(Math.abs(n%3-x)+Math.abs(Math.floor(n/3)-y)<=1)board[n]=1-board[n]}
function start(g){game=g;epoch++;done=false;first=-1;locked=false;moves=0;
 if(g==='tic')board=['','','','','','','','',''];
 if(g==='pairs'){board=[];for(var i=0;i<16;i++)board.push({v:Math.floor(i/2)+1,up:false,matched:false});for(var j=15;j>0;j--){var k=Math.floor(Math.random()*(j+1)),t=board[j];board[j]=board[k];board[k]=t}}
 if(g==='lights'){board=[0,0,0,0,0,0,0,0,0];for(var q=0;q<7;q++)toggle(Math.floor(Math.random()*9));if(board.every(function(v){return !v}))toggle(4)}
 draw()}
function draw(){['tic','pairs','lights'].forEach(function(g){document.getElementById(g).className=g===game?'on':''});
 area.className=game==='pairs'?'four':'';while(area.firstChild)area.removeChild(area.firstChild);
 for(var i=0;i<board.length;i++){(function(n){var b=document.createElement('button'),v=board[n];
 if(game==='tic'){b.textContent=v||'·';if(v==='X')b.className='player';if(v==='O')b.className='opponent'}
 if(game==='pairs')b.textContent=(v.up||v.matched)?String(v.v)+(v.matched?'✓':''):'?';
 if(game==='lights'){b.textContent=v?'●':'○';if(v)b.className='lit'}
 b.setAttribute('aria-label',(game==='pairs'?'Card ': 'Tile ')+(n+1)+': '+b.textContent);b.addEventListener('click',function(){play(n)});area.appendChild(b)})(i)}
 if(game==='tic'){var w=winner(board);status.textContent=w?(w==='X'?'You win!':'Bot wins.'):done?'Draw.':'You are X. Get three in a row.'}
 if(game==='pairs')status.textContent=done?'All pairs found in '+moves+' tries!':'Match all eight pairs. Tries: '+moves;
 if(game==='lights')status.textContent=done?'All lights off in '+moves+' moves!':'Turn every light off. Moves: '+moves;
}
function play(i){if(done||locked)return;
 if(game==='tic'){if(board[i])return;board[i]='X';if(!winner(board)&&board.indexOf('')!==-1){var pick=-1,signs=['O','X'];for(var s=0;s<2&&pick<0;s++)for(var j=0;j<9;j++)if(!board[j]){board[j]=signs[s];if(winner(board)===signs[s])pick=j;board[j]='';if(pick>=0)break}if(pick<0)pick=board[4]?-1:4;if(pick<0)pick=board.indexOf('');board[pick]='O'}done=!!winner(board)||board.indexOf('')===-1;draw();return}
 if(game==='lights'){toggle(i);moves++;done=board.every(function(v){return !v});draw();return}
 var card=board[i];if(card.up||card.matched)return;card.up=true;if(first<0){first=i;draw();return}moves++;var a=first;first=-1;if(board[a].v===card.v){board[a].matched=true;card.matched=true;done=board.every(function(v){return v.matched});draw();return}locked=true;draw();var e=epoch;setTimeout(function(){if(e!==epoch)return;board[a].up=false;board[i].up=false;locked=false;draw()},700)
}
['tic','pairs','lights'].forEach(function(g){document.getElementById(g).addEventListener('click',function(){start(g)})});document.getElementById('restart').addEventListener('click',function(){start(game)});start(game)
})();
