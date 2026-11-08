var title='Minefield',mines,open,done,started;
function start(){mines=[];open=[];for(var i=0;i<16;i++)open.push(false);done=false;started=false;draw()}
function near(a,b){return Math.abs(a%4-b%4)<=1&&Math.abs(Math.floor(a/4)-Math.floor(b/4))<=1}
function draw(){say(done?(mines.some(function(i){return open[i]})?'Mine hit!':'Field cleared!'):'Reveal 13 safe tiles. First tap is safe.');grid(open.map(function(v,i){return v?(mines.indexOf(i)>=0?'✹':String(mines.filter(function(m){return near(m,i)}).length)):'?'}),4,play)}
function play(i){if(done||open[i])return;if(!started){var pool=[];for(var j=0;j<16;j++)if(j!==i)pool.push(j);mines=shuffle(pool).slice(0,3);started=true}open[i]=true;if(mines.indexOf(i)>=0)done=true;else if(open.filter(Boolean).length===13)done=true;draw()}
