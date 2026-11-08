var title='Rock paper scissors',wins,losses,done,last;
function start(){wins=0;losses=0;done=false;last='First to three wins.';draw()}
function draw(){say(done?last+' Final '+wins+'–'+losses:last+' Score '+wins+'–'+losses);grid(['Rock','Paper','Scissors'],3,play)}
function play(i){if(done)return;var bot=rnd(3),names=['Rock','Paper','Scissors'];if(i===bot)last='Both chose '+names[i]+'. Draw.';else if((i-bot+3)%3===1){wins++;last='Bot chose '+names[bot]+'. Round won.'}else{losses++;last='Bot chose '+names[bot]+'. Round lost.'}done=wins===3||losses===3;if(done)last=wins===3?'You win!':'Bot wins.';draw()}
