var title='Sliding tiles',a,turns,done;
function start(){a=[1,2,3,4,5,6,7,8,0];var z=8,previous=-1;for(var n=0;n<100;n++){var choices=[];for(var i=0;i<9;i++)if(Math.abs(i%3-z%3)+Math.abs(Math.floor(i/3)-Math.floor(z/3))===1&&i!==previous)choices.push(i);var k=choices[rnd(choices.length)];a[z]=a[k];a[k]=0;previous=z;z=k}if(a.join(',')==='1,2,3,4,5,6,7,8,0'){a[8]=8;a[7]=0}turns=0;done=false;draw()}
function draw(){say(done?'Solved in '+turns+' moves!':'Arrange 1–8 in order. Moves: '+turns);grid(a.map(function(v){return v||'·'}),3,play)}
function play(i){if(done||!a[i])return;var z=a.indexOf(0);if(Math.abs(i%3-z%3)+Math.abs(Math.floor(i/3)-Math.floor(z/3))!==1)return;a[z]=a[i];a[i]=0;turns++;done=a.join(',')==='1,2,3,4,5,6,7,8,0';draw()}
