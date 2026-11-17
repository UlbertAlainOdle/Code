const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {capture}=require('../static/js/browser_settings_archive.js');
const vm=require('node:vm');
const catalog=JSON.parse(fs.readFileSync('static/browser_settings_inventory.json','utf8'));
test('capture preserves exact raw settings without reading credentials or mutating storage',()=>{
 const raw=' {"ambient":0.5} ',reads=[];
 const storage={getItem(key){reads.push(key);return key==='saya_mmd_3d_settings'?raw:null;},setItem(){throw new Error('must not write');}};
 const data=capture(storage,catalog.entries,{frame:{width:640,height:480},camera:{position:[1,2,3]}});
 assert.equal(data.saya_mmd_3d_settings,raw);assert.equal(JSON.parse(data.saya_3d_live_view_snapshot_v1).frame.width,640);
 assert.ok(!reads.includes('saya_api_settings_v1'));assert.ok(!reads.includes('saya_other_api_settings_v1'));
});
test('all authored browser cache keys have an inventory entry or explicit exclusion',()=>{
 const files=['static/js/app.js','static/js/chat_experience.js','static/js/local_api_profile.js','static/js/mmd_try_showcase.js'];
 const known=new Set([...Object.keys(catalog.entries),...Object.keys(catalog.excluded)]);
 for(const path of files){
  const source=fs.readFileSync(path,'utf8'),constants=new Map([...source.matchAll(/(?:const|let)\s+(\w+)\s*=\s*['"]([^'"]+)['"]/g)].map(m=>[m[1],m[2]]));
  for(const m of source.matchAll(/(?:localStorage|sayaSettings|motionSettingsStorage)\.(?:getItem|setItem|removeItem)\(\s*(['"])([^'"]+)\1|(?:localStorage|sayaSettings|motionSettingsStorage)\.(?:getItem|setItem|removeItem)\(\s*(\w+)/g)){
   const key=m[2]||constants.get(m[3]);if(!key)continue;
   assert.ok(known.has(key)||catalog.excluded_prefixes.some(p=>key.startsWith(p)),`${path}: unregistered ${key}`);
  }
 }
});
test('failed re-preview invalidates the prior save and cannot submit an unreviewed snapshot',async()=>{
 const callbacks=[],nodes={},data=new Map([['saya_mmd_3d_settings','{"ambient":0.5}']]);let previews=0,writes=0;
 for(const key of ['preview','refresh','save','feedback','status','diff','overwrite','label'])nodes['browser-archive-'+key]={disabled:false,checked:false,value:'',replaceChildren(){},append(){}};
 const window={SayaWebWindow:{whenReady:fn=>callbacks.push(fn)},SayaBrowserViewSnapshot:()=>({frame:{width:400}})};
 const fetch=async(url,init={})=>{
  let value;if(url.startsWith('/static/'))value=catalog;
  else if(url.endsWith('/preview')){previews++;if(previews>1)return {ok:false,status:500,json:async()=>({detail:'fixture preview failure'})};value={revision:'r1',changes:[{category:'3D',label:'fixture',action:'add'}],retained_count:0};}
  else {if(init.method==='PUT')writes++;value={revision:'r1',saved_count:0,history_count:0};}
  return {ok:true,status:200,json:async()=>value};
 };
 vm.runInNewContext(fs.readFileSync('static/js/browser_settings_archive.js','utf8'),{window,fetch,AbortController,setTimeout,clearTimeout,
  document:{getElementById:id=>nodes[id],createElement:()=>({})},localStorage:{getItem:k=>data.get(k)??null,get length(){return data.size;},key:i=>[...data.keys()][i]}});
 callbacks[0]();await new Promise(setImmediate);
 await nodes['browser-archive-preview'].onclick();assert.equal(nodes['browser-archive-save'].disabled,false);
 await nodes['browser-archive-preview'].onclick();assert.equal(nodes['browser-archive-save'].disabled,true);
 await nodes['browser-archive-save'].onclick();assert.equal(writes,0);
});
