(function(){
var t=window.TelegramWidget,events=[],state=document.getElementById('state'),box=document.getElementById('log'),notice=document.getElementById('notice');
function draw(){var profile=t&&typeof t.profile==='number'?String(t.profile):'unavailable',theme=t&&t.colorScheme?t.colorScheme:'unavailable',active=t&&typeof t.isActive==='boolean'?(t.isActive?'active':'inactive'):'unknown';state.textContent='Profile: '+profile+' · Theme: '+theme+' · '+active;box.textContent=events.length?events.join('\n'):'No events yet.'}
function add(text){events.push(text);if(events.length>4)events.shift();draw()}
if(t&&typeof t.onEvent==='function'){['themeChanged','activated','deactivated','chatDataChanged','publicDataChanged','contentSafeAreaChanged'].forEach(function(name){t.onEvent(name,function(){add(name)})});add('SDK available');if(typeof t.ready==='function')t.ready()}else add('SDK unavailable');
document.getElementById('tap').addEventListener('click',function(){add('Tap received');notice.textContent='Tap reached this widget.'});
document.getElementById('clear').addEventListener('click',function(){events=[];draw();notice.textContent='Local event list cleared.'});
document.getElementById('copy').addEventListener('click',function(){if(!t||typeof t.copyText!=='function'){notice.textContent='Copy is unavailable in this client.';return}var text=events.length?events.join('\n'):'No events yet.';try{t.copyText(text);notice.textContent='Copy requested from Telegram.'}catch(e){notice.textContent='Copy request unavailable.'}});
})();
