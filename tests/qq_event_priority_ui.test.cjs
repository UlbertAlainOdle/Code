const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('event dates and automatic importance are read on demand and rendered as plain text',async()=>{
 const elements={};const calls=[];
 const document={getElementById(id){return elements[id]??={value:'',checked:false,disabled:false,textContent:''};},createElement(){return {};}};
 const fetch=async(path)=>{calls.push(path);return {ok:true,status:200,json:async()=>path.endsWith('/migration')?
  {already_migrated:true,can_apply:true,entries:[]}:{enabled:true,identity_migrated:true,next_offset:null,items:[{
   subject:'20000',title:'<img src=x>',id:'fixture',revision:2,state:'active',importance:'high',importance_rule_version:1,
   state_reason:'evidence_or_time',capacity:{max_active:5,max_total:20},visible_sources:['private'],
   timing:{deadline:{local:'2026-11-06',precision:'day',timezone:'Asia/Shanghai',status:'future'}}}]}};};
 vm.runInNewContext(fs.readFileSync('static/js/qq_context.js','utf8'),{document,fetch,confirm:()=>false,encodeURIComponent,console});
 assert.equal(calls.length,0);await elements['events-read'].onclick();
 const text=elements['events-status'].textContent;
 assert.ok(text.includes('high（自动规则v1）')&&text.includes('截止：2026-11-06'));
 assert.ok(text.includes('<img src=x>'));assert.equal(elements['events-status'].innerHTML,undefined);
 assert.equal(calls.length,2);
});
