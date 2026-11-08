var title='Dots & Boxes',edges,boxes,done;
function around(i){return [i-5,i+5,i-1,i+1]}
function claim(who){var gained=0;[6,8,16,18].forEach(function(i){if(!boxes[i]&&around(i).every(function(e){return !!edges[e]})){boxes[i]=who;gained++}});return gained}
function candidates(){var a=[];for(var i=0;i<25;i++){var r=Math.floor(i/5),c=i%5;if((r+c)%2===1&&!edges[i])a.push(i)}return a}
function start(){edges={};boxes={};done=false;draw()}
function draw(){var x=0,o=0;Object.keys(boxes).forEach(function(i){if(boxes[i]==='X')x++;else o++});done=x+o===4;say(done?(x===o?'Draw':x>o?'You win':'Bot wins')+'! You '+x+' · Bot '+o:'Tap + to draw an edge. Complete a box for an extra turn. You '+x+' · Bot '+o);var cells=[];for(var i=0;i<25;i++){var r=Math.floor(i/5),c=i%5,t=(r%2===0&&c%2===0)?'•':(r%2&&c%2)?(boxes[i]||''):(edges[i]?(r%2?'│':'─'):'+');cells.push({text:t,label:'Cell '+(i+1)+' '+(t||'empty')})}grid(cells,5,play)}
function play(i){if(done||candidates().indexOf(i)<0)return;edges[i]='X';if(!claim('X')){var choices=candidates();while(choices.length){var best=choices[0],max=-1;choices.forEach(function(e){edges[e]='O';var count=0;[6,8,16,18].forEach(function(b){if(!boxes[b]&&around(b).every(function(k){return !!edges[k]}))count++});delete edges[e];if(count>max){max=count;best=e}});edges[best]='O';if(!claim('O'))break;choices=candidates()}}draw()}
