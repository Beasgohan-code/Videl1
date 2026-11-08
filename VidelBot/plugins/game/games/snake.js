var title='Snake',snake,dir,next,food,timer,done,win,running;
function spawn(){var free=[];for(var i=0;i<36;i++)if(snake.indexOf(i)<0)free.push(i);return free.length?free[rnd(free.length)]:-1}
function start(){if(timer)clearInterval(timer);snake=[20,19,18];dir=1;next=1;food=spawn();done=false;win=false;running=false;draw()}
function draw(){say(done?(win?'You grew to eight!':'Snake crashed.'):(running?'Eat ● and grow to eight.':'Tap Start, then use the arrows to eat ●.'));var rows=[];for(var r=0;r<6;r++){var row=[];for(var c=0;c<6;c++){var i=r*6+c;row.push(i===food?'●':snake[0]===i?'◆':snake.indexOf(i)>=0?'■':'·')}rows.push(row.join(' '))}matrix(rows.join('\n'),['←','↑','→',running?'Pause':'Start','↓'],play)}
function play(i){if(done)return;if(i===3){running=!running;if(running)timer=setInterval(tick,450);else clearInterval(timer);draw();return}if(!running)return;var d={0:-1,1:-6,2:1,4:6}[i];if(d&&d!==-dir)next=d}
function tick(){if(done||!running)return;dir=next;var head=snake[0],n=head+dir;if(n<0||n>=36||(dir===1&&head%6===5)||(dir===-1&&head%6===0)||snake.slice(0,n===food?snake.length:snake.length-1).indexOf(n)>=0){done=true;running=false;clearInterval(timer);draw();return}snake.unshift(n);if(n===food){if(snake.length>=8){done=true;win=true;running=false;clearInterval(timer)}else food=spawn()}else snake.pop();draw()}

document.addEventListener('visibilitychange',function(){if(document.hidden&&running){running=false;clearInterval(timer);draw()}});
