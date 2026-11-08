var title='Color Flood',board,region,moves,done;
function mark(){region=[];var q=[0],color=board[0];region[0]=true;for(var k=0;k<q.length;k++){var i=q[k];for(var j=0;j<16;j++)if(!region[j]&&board[j]===color&&Math.abs(i%4-j%4)+Math.abs(Math.floor(i/4)-Math.floor(j/4))===1){region[j]=true;q.push(j)}}return q}
function start(){board=[];for(var i=0;i<16;i++)board.push(rnd(3)+1);board[0]=1;board[15]=2;moves=0;done=false;draw()}
function draw(){var q=mark();say(done?'Board flooded in '+moves+' moves!':'Grow the top-left group. Tap any tile to choose its number. Moves: '+moves);var cells=board.map(function(n,i){return {text:String(n),on:!!region[i],label:'Tile '+(i+1)+', number '+n+(region[i]?', in group':'')}});grid(cells,4,play)}
function play(i){if(done||board[i]===board[0])return;var q=mark(),color=board[i];q.forEach(function(j){board[j]=color});moves++;done=mark().length===16;draw()}
