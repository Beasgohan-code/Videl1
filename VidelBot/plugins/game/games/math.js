var title='Math sprint',round,score,answers,correct,question,done,last,record;
function start(){round=0;score=0;done=false;last='';record=readBest('math');next()}
function next(){if(round===10){done=true;record=saveBest('math',score,false);draw();return}var a=rnd(12)+1,b=rnd(12)+1;correct=a+b;question=a+' + '+b+' = ?';answers=shuffle([correct,correct+1,correct-1,correct+2]);draw()}
function draw(){say(done?(score>=8?'You win! ':'Try again. ')+'Score '+score+'/10'+(record===null?'':' · Personal best: '+record):last+question+' · '+(round+1)+'/10 · Score '+score);bestControl('math',record,function(){record=null;draw()});grid(done?['Finished']:answers,done?1:2,play)}
function play(i){if(done)return;if(answers[i]===correct){score++;last='Correct! '}else last='Answer: '+correct+'. ';round++;next()}
