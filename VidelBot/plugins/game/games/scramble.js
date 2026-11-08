var title='Word scramble',words,index,score,answers,answer,puzzle,done;
function start(){words=shuffle(['PLANET','FOREST','GUITAR','ROCKET','BRIDGE','GARDEN','PUZZLE','TIGER','OCEAN','BUTTON']);index=0;score=0;done=false;next()}
function next(){if(index===5){done=true;draw();return}answer=words[index];puzzle=shuffle(answer.split('')).join('');if(puzzle===answer)puzzle=answer.slice(1)+answer[0];var pool=words.filter(function(w){return w!==answer});answers=shuffle([answer,pool[0],pool[1]]);draw()}
function draw(){say(done?(score>=4?'You win! ':'Try again. ')+'Score '+score+'/5':'Unscramble: '+puzzle+' · '+(index+1)+'/5 · Score '+score);grid(done?['Finished']:answers,1,play)}
function play(i){if(done)return;if(answers[i]===answer)score++;index++;next()}
