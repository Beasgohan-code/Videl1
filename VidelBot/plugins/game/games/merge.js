var title='Merge 128',a,done,win,turns;
function add(){var empty=[];for(var i=0;i<16;i++)if(!a[i])empty.push(i);if(empty.length)a[empty[rnd(empty.length)]]=rnd(10)?2:4}
function start(){a=[];for(var i=0;i<16;i++)a.push(0);done=false;win=false;turns=0;add();add();draw()}
function line(ids){var v=ids.map(function(i){return a[i]}).filter(Boolean),out=[];for(var j=0;j<v.length;j++){if(v[j]===v[j+1]){out.push(v[j]*2);j++}else out.push(v[j])}while(out.length<4)out.push(0);ids.forEach(function(i,k){a[i]=out[k]})}
function canMove(){if(a.indexOf(0)>=0)return true;for(var i=0;i<16;i++)if((i%4<3&&a[i]===a[i+1])||(i<12&&a[i]===a[i+4]))return true;return false}
function draw(){say(done?(win?'128 reached!':'No moves left.'):'Merge equal tiles to reach 128. Moves: '+turns);var cells=a.map(function(v){return v||'·'});cells.push('←','↑','↓','→');grid(cells,4,play)}
function play(n){if(done||n<16)return;var old=a.join(',');for(var r=0;r<4;r++){var ids=[];for(var c=0;c<4;c++){if(n===16)ids.push(r*4+c);if(n===19)ids.push(r*4+3-c);if(n===17)ids.push(c*4+r);if(n===18)ids.push((3-c)*4+r)}line(ids)}if(a.join(',')===old)return;turns++;win=a.some(function(v){return v>=128});if(!win)add();done=win||!canMove();draw()}
