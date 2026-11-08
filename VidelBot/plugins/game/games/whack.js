var title='Target tap',target,hits,left,timer,done,win,running;
function start(){if(timer)clearInterval(timer);target=rnd(9);hits=0;left=20;done=false;win=false;running=false;draw()}
function tick(){left--;if(left<=0){done=true;running=false;clearInterval(timer)}else target=(target+1+rnd(8))%9;draw()}
function draw(){say(done?(win?'You hit 10 targets!':'Time up! Hits: '+hits):(running?'Tap the moving ●. ':'Tap Start, then catch ●. ')+'Time: '+left+'s · Hits: '+hits+'/10');var c=[];for(var i=0;i<9;i++)c.push({text:i===target?'●':'·',on:i===target});c.push(running?'Pause':'Start');grid(c,3,play)}
function play(i){if(done)return;if(i===9){running=!running;if(running)timer=setInterval(tick,1000);else clearInterval(timer);draw();return}if(!running||i!==target)return;hits++;if(hits>=10){done=true;win=true;running=false;clearInterval(timer)}else target=(target+1+rnd(8))%9;draw()}

document.addEventListener('visibilitychange',function(){if(document.hidden&&running){running=false;clearInterval(timer);draw()}});
