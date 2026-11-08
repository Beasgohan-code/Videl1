var title='Sudoku 4×4',a,fixed,done;
function start(){var solution=[1,2,3,4,3,4,1,2,2,1,4,3,4,3,2,1],p=shuffle([0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]);fixed=[];a=[];for(var i=0;i<16;i++){fixed[i]=p.indexOf(i)<8;a[i]=fixed[i]?solution[i]:0}done=false;draw()}
function valid(){for(var r=0;r<4;r++){var row=[],col=[],box=[];for(var j=0;j<4;j++){row.push(a[r*4+j]);col.push(a[j*4+r]);box.push(a[(Math.floor(r/2)*2+Math.floor(j/2))*4+(r%2)*2+j%2])}if(new Set(row).size!==4||new Set(col).size!==4||new Set(box).size!==4)return false}return a.indexOf(0)<0}
function draw(){say(done?'Puzzle solved!':'Tap tiles to cycle 1–4; • marks clues. Rows, columns and 2×2 boxes must differ.');grid(a.map(function(v,i){return {text:v?String(v)+(fixed[i]?'•':''):'·',on:fixed[i],label:(fixed[i]?'Clue ':'Tile ')+(i+1)+': '+(v||'empty')}}),4,play)}
function play(i){if(done||fixed[i])return;a[i]=(a[i]+1)%5;done=valid();draw()}
