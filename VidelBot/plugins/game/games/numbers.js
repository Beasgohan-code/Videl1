var title='Number rush',a,next,done,begin,elapsed,record;
function start(){a=shuffle([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]);next=1;done=false;begin=0;elapsed=0;record=readBest('numbers');draw()}
function draw(){var best=record===null?'':' · Best here: '+(record/1000).toFixed(1)+'s';say(done?'Complete in '+(elapsed/1000).toFixed(1)+' seconds!'+best:'Tap 1–16 in order. Time starts on 1. Next: '+next+best);bestControl('numbers',record,function(){record=null;draw()});grid(a.map(function(v){return v||'·'}),4,play)}
function play(i){if(done||a[i]!==next)return;if(next===1)begin=Date.now();a[i]=0;next++;done=next===17;if(done){elapsed=Math.max(0,Date.now()-begin);record=saveBest('numbers',elapsed,true)}draw()}
