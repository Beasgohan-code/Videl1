var title='Higher or lower',current,round,score,done,last;
function start(){current=1+rnd(12);round=0;score=0;done=false;last='';draw()}
function draw(){say(done?(score>=5?'You win! ':'Try again. ')+'Score '+score+'/8':last+' Current number: '+current+' · '+(round+1)+'/8 · Score '+score);grid(['Higher','Lower'],2,play)}
function play(i){if(done)return;var next=1+rnd(12);if(next===current)last='Equal: no point.';else if((i===0&&next>current)||(i===1&&next<current)){score++;last='Correct!'}else last='Not this time.';current=next;round++;done=round===8;draw()}
