const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('event management renders effective state and manual importance without auto-reading or HTML',async()=>{
 const elements={};const calls=[];
 const document={getElementById(id){return elements[id]??={value:'',checked:false,disabled:false,textContent:''};},createElement(){return {};}};
 const fetch=async(path,options)=>{calls.push(path);return {ok:true,status:200,json:async()=>path.endsWith('/migration')?
  {already_migrated:true,can_apply:true,entries:[]}:{enabled:true,identity_migrated:true,next_offset:null,items:[{
   subject:'20000',title:'<img src=x>',id:'fixture',revision:2,state:'suppressed',importance:'high',importance_locked:true,
   state_reason:'stop',capacity:{max_active:5,max_total:20},visible_sources:['private']}]}};};
 vm.runInNewContext(fs.readFileSync('static/js/qq_context.js','utf8'),{document,fetch,confirm:()=>false,encodeURIComponent,console});
 assert.equal(calls.length,0);await elements['events-read'].onclick();
 const text=elements['events-status'].textContent;
 assert.ok(text.includes('suppressed')&&text.includes('high（手动）')&&text.includes('原因stop'));
 assert.ok(text.includes('<img src=x>'));assert.equal(elements['events-status'].innerHTML,undefined);
 assert.equal(calls.length,2);
});
