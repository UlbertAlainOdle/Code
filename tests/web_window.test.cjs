const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(setImmediate);
function fixture(){
 const sockets=[],loads=[],events={},calls=[],timers=[];const nodes={'saya-window-message':{},'saya-window-retry':{}};
 class Socket {static CONNECTING=0;static OPEN=1;constructor(){this.readyState=1;sockets.push(this);}send(text){this.lastMessage=text;}close(){this.readyState=3;this.onclose?.();}}
 const document={readyState:'complete',documentElement:{dataset:{}},getElementById:id=>nodes[id],
  querySelectorAll:()=>[{dataset:{webBootSrc:'/a.js'}},{dataset:{webBootSrc:'/b.js'}}],
  createElement:()=>({}),body:{append(script){loads.push(script.src);window.SayaWebWindow.whenReady(()=>events.ready=(events.ready||0)+1);queueMicrotask(()=>script.onload());}}};
 const window={location:{href:'http://127.0.0.1:8000/static/Chat4o5-Modern.html',origin:'http://127.0.0.1:8000',host:'127.0.0.1:8000',protocol:'http:',reload(){}},
  fetch:async(...args)=>{calls.push(args);return {};},addEventListener:(name,fn)=>events[name]=fn};
 vm.runInNewContext(fs.readFileSync('static/js/web_window.js','utf8'),{window,document,WebSocket:Socket,URL,Headers,Request,queueMicrotask,setTimeout:fn=>timers.push(fn),clearTimeout(){}});
 return {window,document,nodes,sockets,loads,calls,timers,events};
}
test('occupied browser does not boot app or send same-origin requests',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'occupied',message:'already open'})});
 assert.equal(f.document.documentElement.dataset.sayaWindow,'waiting');assert.equal(f.loads.length,0);
 await assert.rejects(f.window.fetch('/chat_stream',{method:'POST'}));assert.equal(f.calls.length,0);
});
test('one granted owner loads scripts in order and headers stay on the same origin only',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'fixture-token'})});await tick();
 assert.deepEqual(f.loads,['/a.js','/b.js']);assert.equal(f.events.ready,2);assert.equal(f.document.documentElement.dataset.sayaWindow,'active');
 await f.window.fetch('/chat_stream',{method:'POST'});assert.equal(f.calls[0][1].headers.get('X-Saya-Web-Window'),'fixture-token');
 await f.window.fetch('https://example.invalid/data');assert.equal(f.calls[1][1].headers,undefined);
});
test('owner disconnect freezes requests; reconnect reuses existing UI without duplicate initialization',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'first'})});await tick();f.sockets[0].close();
 await assert.rejects(f.window.fetch('/chat_stream'));assert.equal(f.document.documentElement.dataset.sayaWindow,'waiting');
 f.timers[0]();f.sockets[1].onmessage({data:JSON.stringify({type:'accepted',token:'second'})});await tick();
 assert.equal(f.loads.length,2);await f.window.fetch('/chat_stream');assert.equal(f.calls[0][1].headers.get('X-Saya-Web-Window'),'second');
});
test('all application and 3D scripts are inert until ownership is granted',()=>{
 const html=fs.readFileSync('static/Chat4o5-Modern.html','utf8');
 const sources=[...html.matchAll(/<script\b[^>]*\ssrc="([^"]+)"/g)].map(m=>m[1]);
 assert.equal(sources.length,1);assert.match(sources[0],/^\/static\/js\/web_window\.js\?v=/);
 assert.equal([...html.matchAll(/data-web-boot-src=/g)].length,22);
 assert.ok(html.indexOf('data-settings-bootstrap="1"')<html.indexOf('three.min.js'));
});
test('disconnect during bootstrap does not restart partially executed scripts',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'first'})});f.sockets[0].close();await tick();
 assert.equal(f.document.documentElement.dataset.sayaWindow,'waiting');assert.equal(f.loads.length,1);
 f.timers[0]();f.sockets[1].onmessage({data:JSON.stringify({type:'accepted',token:'second'})});await tick();
 assert.equal(f.loads.length,1);assert.match(f.nodes['saya-window-retry'].textContent,/刷新/);
});
test('explicit switch releases ownership without destroying the browser draft or reconnecting',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'first'})});await tick();
 await f.window.SayaWebWindow.release();assert.equal(f.sockets[0].lastMessage,'release');
 f.sockets[0].onmessage({data:JSON.stringify({type:'released'})});f.sockets[0].close();
 assert.match(f.nodes['saya-window-message'].textContent,/已释放/);assert.equal(f.timers.length,0);
 await assert.rejects(f.window.fetch('/chat_stream'));
});

test('settings must hydrate before the next application script boots',async()=>{
 const f=fixture();await tick();let finish;const initialized=new Promise(resolve=>finish=resolve);
 f.document.querySelectorAll=()=>[{dataset:{webBootSrc:'/local_settings.js',settingsBootstrap:'1'}},{dataset:{webBootSrc:'/app.js'}}];
 f.window.SayaSettingsStore={initialize:()=>initialized};f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'first'})});await tick();
 assert.deepEqual(f.loads,['/local_settings.js']);finish();await tick();assert.deepEqual(f.loads,['/local_settings.js','/app.js']);
});

test('unsaved settings stop a chat request and window release until persistence succeeds',async()=>{
 const f=fixture();await tick();f.sockets[0].onmessage({data:JSON.stringify({type:'accepted',token:'first'})});await tick();
 f.window.SayaSettingsStore={flush:async()=>{throw new Error('disk failure');},beforeChat:async()=>{throw new Error('disk failure');}};
 await assert.rejects(f.window.fetch('/chat_stream'),/disk failure/);assert.equal(f.calls.length,0);
 await f.window.SayaWebWindow.release();assert.equal(f.sockets[0].lastMessage,undefined);assert.equal(f.window.SayaWebWindow.active(),true);
});
