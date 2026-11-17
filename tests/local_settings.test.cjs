const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {createStore,fitFrame}=require('../static/js/local_settings.js');
const entries=JSON.parse(fs.readFileSync('static/browser_settings_inventory.json','utf8')).entries;
const tick=()=>new Promise(setImmediate);
function fixture(values={},enabled=true){
 const nativeValues=new Map([['saya_weather_city','STALE_CITY'],['saya_thinking_effort','STALE_THINKING'],['saya_api_settings_v1','PRIVATE_NATIVE_API']]);
 const native={getItem:k=>nativeValues.get(k)??null,setItem:(k,v)=>nativeValues.set(k,v),removeItem:k=>nativeValues.delete(k)};
 const requests=[],states=[],timers=[];let data={revision:'r1',runtime_applied:enabled,entries,values:{...values}},handler=null;
 const request=async(path='',init={})=>{requests.push({path,init});if(handler)return handler(path,init);if(init.method==='PUT'){
  const body=JSON.parse(init.body);assert.equal(body.revision,data.revision);Object.assign(data.values,body.values);for(const k of body.clear_fields)data.values[k]&&delete data.values[k];data.revision+='x';
 }return structuredClone(data);};
 const store=createStore({native,request,notify:s=>states.push(s),schedule:fn=>timers.push(fn),cancel(){}});store.accept(structuredClone(data));
 return {store,nativeValues,requests,states,timers,get data(){return data;},set handler(fn){handler=fn;}};
}
test('local values win before boot and missing/reset values never fall back to stale browser cache',async()=>{
 const f=fixture({saya_weather_city:'CORRECT_CITY'});assert.equal(f.store.storage.getItem('saya_weather_city'),'CORRECT_CITY');
 assert.equal(f.store.storage.getItem('saya_thinking_effort'),null);
 f.store.storage.removeItem('saya_weather_city');await f.store.flush();
 assert.equal(f.store.storage.getItem('saya_weather_city'),null);assert.equal(f.nativeValues.get('saya_weather_city'),'STALE_CITY');
 assert.equal(f.store.storage.getItem('saya_api_settings_v1'),'PRIVATE_NATIVE_API');
 assert.ok(!JSON.stringify(f.requests).includes('PRIVATE_NATIVE_API'));
});
test('legacy mode is an explicit persistent opt-out and preserves prior browser behavior',()=>{
 const f=fixture({},false);assert.equal(f.store.storage.getItem('saya_weather_city'),'STALE_CITY');
 f.store.storage.setItem('saya_weather_city','LEGACY');assert.equal(f.nativeValues.get('saya_weather_city'),'LEGACY');assert.equal(f.store.state().pending,0);
});
test('in-flight writes serialize revisions and retain edits made before the response arrives',async()=>{
 const f=fixture();let finish;f.handler=async(path,init)=>{const body=JSON.parse(init.body);return new Promise(resolve=>finish=()=>resolve({revision:body.revision+'x'}));};
 f.store.storage.setItem('saya_weather_city','A');const flushing=f.store.flush();await tick();
 f.store.storage.setItem('saya_weather_city','B');finish();await tick();assert.equal(f.requests.length,2);
 assert.equal(JSON.parse(f.requests[1].init.body).values.saya_weather_city,'B');assert.equal(JSON.parse(f.requests[1].init.body).revision,'r1x');
 finish();await flushing;assert.equal(f.store.state().pending,0);assert.equal(f.store.storage.getItem('saya_weather_city'),'B');
});
test('conflict preserves unsaved draft, stops automatic overwrite, and blocks sending',async()=>{
 const f=fixture();const conflict=Object.assign(new Error('fixture conflict'),{status:409});f.handler=async()=>{throw conflict;};
 f.store.storage.setItem('saya_weather_city','DRAFT');await assert.rejects(f.store.flush(),/conflict/);
 assert.equal(f.store.state().stale,true);assert.deepEqual(f.store.draft(),{saya_weather_city:'DRAFT'});
 await assert.rejects(f.store.beforeChat());assert.equal(f.nativeValues.get('saya_weather_city'),'STALE_CITY');assert.equal(f.requests.length,1);
});
test('lost successful response reconciles matching saved state without replaying the write',async()=>{
 const f=fixture();let written=false;f.handler=async(path,init)=>{
  if(init.method==='PUT'){written=true;throw new Error('response lost');}
  return {...f.data,revision:written?'r2':'r1',values:{saya_weather_city:'DRAFT'}};
 };
 f.store.storage.setItem('saya_weather_city','DRAFT');await assert.rejects(f.store.flush());await f.store.retry();
 assert.equal(f.store.state().pending,0);assert.equal(f.store.state().revision,'r2');assert.equal(f.requests.filter(x=>x.init.method==='PUT').length,1);
 assert.equal(f.store.state().stale,true);await assert.rejects(f.store.beforeChat());
});
test('external changes are detected before another chat and do not use stale runtime values',async()=>{
 const f=fixture({saya_weather_city:'OLD'});f.data.revision='new';await assert.rejects(f.store.beforeChat(),/其他入口/);assert.equal(f.store.state().stale,true);
});
test('explicitly discarding a failed draft enables a clean reload without altering the local file or cache',async()=>{
 const f=fixture();f.handler=async()=>{throw new Error('offline');};
 f.store.storage.setItem('saya_weather_city','unsaved');await assert.rejects(f.store.flush());f.store.discard();
 assert.equal(f.store.state().pending,0);assert.equal(f.store.state().error,'');assert.equal(f.nativeValues.get('saya_weather_city'),'STALE_CITY');assert.deepEqual(f.data.values,{});
});
test('existing server-owned profile and style remain projections without archive writes',async()=>{
 const f=fixture();f.store.project('saya_user_profile_v1',{name:'server'});assert.equal(JSON.parse(f.store.storage.getItem('saya_user_profile_v1')).name,'server');
 f.store.storage.setItem('saya_style_settings_v1','{"base_style":"balanced"}');await f.store.flush();assert.equal(f.requests.length,0);
});
test('different viewport sizes clamp position and size without modifying the canonical snapshot',()=>{
 const frame={left:1800,top:900,width:650,height:800},copy=JSON.stringify(frame);
 const small=fitFrame(frame,{width:640,height:480},{left:220,top:52});
 assert.ok(small.left>=220&&small.left+small.width<=632);assert.ok(small.top>=52&&small.top+small.height<=472);
 assert.equal(JSON.stringify(frame),copy);
 const tiny=fitFrame(frame,{width:1,height:1});assert.ok(tiny.left>=0&&tiny.top>=0&&tiny.width>0&&tiny.height>0);
});
test('application consumers and standalone tool are bootstrapped through the explicit adapter',()=>{
 for(const name of ['app.js','chat_experience.js']){const source=fs.readFileSync('static/js/'+name,'utf8');assert.match(source,/SayaSettingsStore\?\.storage/);assert.ok(!/localStorage\.(?:getItem|setItem|removeItem)/.test(source));}
 const html=fs.readFileSync('static/mmd_try_showcase.html','utf8');assert.ok(html.indexOf('local_settings.js')<html.indexOf('settings_tool_boot.js'));
 assert.ok(!/src="\/static\/js\/mmd_try_showcase.js/.test(html));
});
test('project list failures preserve saved navigation until a successful server read',async()=>{
 const vm=require('node:vm'),source=fs.readFileSync('static/js/app.js','utf8');
 const method=source.slice(source.indexOf('    async function loadProjects()'),source.indexOf('    function setCurrentProject('));
 const context={fetch:async()=>{throw new Error('offline');},projectsCache:[{id:'saved-project'}]};
 vm.createContext(context);vm.runInContext(method,context);
 assert.equal(await context.loadProjects(),false);assert.equal(context.projectsCache[0].id,'saved-project');
 context.fetch=async()=>({ok:false,json:async()=>({projects:[]})});assert.equal(await context.loadProjects(),false);assert.equal(context.projectsCache.length,1);
 context.fetch=async()=>({ok:true,json:async()=>({projects:[]})});assert.equal(await context.loadProjects(),true);assert.equal(context.projectsCache.length,0);
});
