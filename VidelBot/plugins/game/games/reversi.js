var title='Mini reversi',a,done,result;
function start(){a=['','','','','','O','X','','','X','O','','','','',''];done=false;result='';draw()}
function flips(i,v){if(a[i])return [];var out=[],other=v==='X'?'O':'X',x=i%4,y=Math.floor(i/4);for(var dy=-1;dy<=1;dy++)for(var dx=-1;dx<=1;dx++){if(!dx&&!dy)continue;var xx=x+dx,yy=y+dy,line=[];while(xx>=0&&xx<4&&yy>=0&&yy<4&&a[yy*4+xx]===other){line.push(yy*4+xx);xx+=dx;yy+=dy}if(line.length&&xx>=0&&xx<4&&yy>=0&&yy<4&&a[yy*4+xx]===v)out=out.concat(line)}return out}
function options(v){var out=[];for(var i=0;i<16;i++)if(flips(i,v).length)out.push(i);return out}
function place(i,v){var f=flips(i,v);if(!f.length)return false;a[i]=v;f.forEach(function(x){a[x]=v});return true}
function end(){var x=a.filter(function(v){return v==='X'}).length,o=a.filter(function(v){return v==='O'}).length;done=true;result=(x===o?'Draw':x>o?'You win':'Bot wins')+' · X '+x+' / O '+o}
function draw(){say(done?result:'You are X. Trap O pieces between your pieces.');grid(a.map(function(v){return v||'·'}),4,play)}
function play(i){if(done||!place(i,'X'))return;var bot=options('O');if(bot.length){bot.sort(function(x,y){return flips(y,'O').length-flips(x,'O').length});place(bot[0],'O')}var user=options('X');if(!user.length){if(!options('O').length)end();else {var guard=0;while(!options('X').length&&options('O').length&&guard++<16){var b=options('O');place(b[0],'O')}if(!options('X').length&&!options('O').length)end()}}draw()}
