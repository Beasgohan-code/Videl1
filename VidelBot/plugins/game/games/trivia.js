var title='Quick trivia',round,score,done,last;
var questions=[['Which planet is closest to the Sun?','Mercury','Mars','Earth','Neptune'],['How many sides does a hexagon have?','6','5','7','8'],['Which is the largest ocean?','Pacific','Atlantic','Indian','Arctic'],['Which instrument has black and white keys?','Piano','Violin','Drum','Flute'],['Which animal is a mammal?','Dolphin','Shark','Salmon','Octopus'],['Water freezes at what Celsius temperature?','0','10','50','100'],['Which gas do plants absorb?','Carbon dioxide','Helium','Hydrogen','Neon'],['How many hours are in a day?','24','12','36','48']];
var answers;
function start(){questions=shuffle(questions);round=0;score=0;done=false;last='';next()}
function next(){if(round===questions.length){done=true;draw();return}answers=shuffle(questions[round].slice(1));draw()}
function draw(){say(done?(score>=6?'You win! ':'Try again. ')+'Score '+score+'/8':last+questions[round][0]+' · '+(round+1)+'/8');grid(done?['Finished']:answers,2,play)}
function play(i){if(done)return;if(answers[i]===questions[round][1]){score++;last='Correct! '}else last='Answer: '+questions[round][1]+'. ';round++;next()}
