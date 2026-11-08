var title='Pattern memory',seq,at,phase,flash,done;
function start(){seq=[];at=0;done=false;round()}
function round(){seq.push(rnd(4));phase='show';flash=-1;draw();var e=epoch,n=0;function step(){if(e!==epoch)return;if(n>=seq.length){flash=-1;phase='input';at=0;draw();return}flash=seq[n++];draw();setTimeout(function(){if(e!==epoch)return;flash=-1;draw();setTimeout(step,250)},450)}setTimeout(step,400)}
function draw(){say(done?'Five patterns completed!':phase==='lost'?'Wrong tile. Try a new game.':phase==='show'?'Watch the pattern · round '+seq.length:'Repeat the pattern · round '+seq.length);grid(['●','▲','■','◆'].map(function(v,i){return {text:v+(i===flash?' !':''),on:i===flash,label:v+(i===flash?' flashing':'')}}),2,play)}
function play(i){if(phase!=='input')return;if(i!==seq[at]){phase='lost';draw();return}at++;if(at===seq.length){if(seq.length===5){done=true;phase='done';draw()}else round()}}
