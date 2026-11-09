var title='Twenty One',deck,player,dealer,done,result;
function score(cards){var n=0,aces=0;cards.forEach(function(c){if(c===1){n+=11;aces++}else n+=Math.min(c,10)});while(n>21&&aces){n-=10;aces--}return n}
function card(c){return c===1?'A':c===11?'J':c===12?'Q':c===13?'K':String(c)}
function start(){deck=[];for(var s=0;s<4;s++)for(var n=1;n<=13;n++)deck.push(n);shuffle(deck);player=[deck.pop(),deck.pop()];dealer=[deck.pop(),deck.pop()];done=false;result='';draw()}
function draw(){var p=player.map(card).join(' '),d=done?dealer.map(card).join(' '):card(dealer[0])+' ?';say(done?result+' You '+score(player)+' · Dealer '+score(dealer)+'.':'You: '+p+' ('+score(player)+') · Dealer: '+d+'. Hit or stand?');grid(done?['You: '+p,'Dealer: '+d]:['Hit','Stand'],done?1:2,play)}
function play(i){if(done)return;if(i===0){player.push(deck.pop());if(score(player)>21){done=true;result='Bust! Dealer wins.'}}else{while(score(dealer)<17)dealer.push(deck.pop());done=true;var p=score(player),d=score(dealer);result=d>21||p>d?'You win!':p===d?'Draw.':'Dealer wins.'}draw()}
