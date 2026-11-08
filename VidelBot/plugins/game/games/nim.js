var title='Take the last',left,done,result;
function start(){left=17;done=false;result='';draw()}
function draw(){say(done?result:'Take 1–3 stones. Taking the last wins. Left: '+left);grid(['Take 1','Take 2','Take 3'],3,play)}
function play(i){var n=i+1;if(done||n>left)return;left-=n;if(!left){done=true;result='You took the last stone. You win!'}else{var bot=left%4||1;bot=Math.min(bot,3,left);left-=bot;if(!left){done=true;result='Bot took the last stone. Bot wins.'}else result='Bot took '+bot+'.'}draw();if(!done)say(result+' Stones left: '+left)}
