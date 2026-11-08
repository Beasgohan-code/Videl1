var title='Reaction test',phase,when,result;
function start(){phase='wait';result='Wait for green, then tap as fast as you can.';draw();var e=epoch;setTimeout(function(){if(e!==epoch||phase!=='wait')return;phase='go';when=Date.now();result='Tap now!';draw()},1200+rnd(1800))}
function draw(){say(result);grid([{text:phase==='go'?'TAP!':phase==='wait'?'Wait…':'Finished',on:phase==='go'}],1,play)}
function play(){if(phase==='wait'){phase='done';result='Too early! Start a new game.'}else if(phase==='go'){var ms=Date.now()-when;phase='done';result=ms+' ms · '+(ms<=800?'Great reaction!':'Try again for under 800 ms.')}draw()}
