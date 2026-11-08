var title='Number rush',a,next,done,begin;
function start(){a=shuffle([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]);next=1;done=false;begin=Date.now();draw()}
function draw(){say(done?'Complete in '+((Date.now()-begin)/1000).toFixed(1)+' seconds!':'Tap the numbers in order. Next: '+next);grid(a.map(function(v){return v||'·'}),4,play)}
function play(i){if(done||a[i]!==next)return;a[i]=0;next++;done=next===17;draw()}
