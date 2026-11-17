const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/qq_experience.js','utf8');
function fixture(){
 const elements={};
 for(const id of ['owner','enabled','concise_replies','short_voice','read','save','policy','status','diagnostics'])elements[id]={value:'',checked:false,disabled:false,children:[],replaceChildren(){this.children=[];},append(o){this.children.push(o);},reportValidity(){return !!elements.owner.value;}};
 const calls=[];let current={revision:2,values:{enabled:false},owners:['20000'],diagnostics:{count:4}},failure=false;
 const context={document:{getElementById:id=>elements[id],createElement:()=>({})},JSON,Error,fetch:async(path,options)=>{
  calls.push({path,options});if(failure)return {ok:false,status:409,json:async()=>({detail:'设置已变化'})};
  if(options.method==='PUT'){const body=JSON.parse(options.body);current={revision:3,values:body.values};}
  return {ok:true,status:200,json:async()=>current};
 }};
 vm.createContext(context);vm.runInContext(source,context);
 return {elements,calls,fail(){failure=true;}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('no implicit enabling or owner selection; save uses actual revision and only its own policy',async()=>{
 const {elements:e,calls}=fixture();assert.equal(calls.length,0);
 e.read.onclick();await settle();assert.equal(e.owner.value,'');assert.equal(e.enabled.checked,false);
 e.owner.value='20000';e.enabled.checked=true;e.policy.onsubmit({preventDefault(){}});await settle();
 assert.deepEqual(JSON.parse(calls[1].options.body),{owner_id:'20000',revision:2,confirmed:true,values:{enabled:true,concise_replies:true,short_voice:true}});
 assert.equal(e.owner.value,'20000');assert.equal(e.enabled.checked,true);
});
test('reading cannot overwrite an unsaved draft and conflict preserves it',async()=>{
 const f=fixture(),e=f.elements;e.read.onclick();await settle();e.enabled.checked=true;
 e.read.onclick();await settle();assert.equal(f.calls.length,1);assert.match(e.status.textContent,/草稿/);
 e.owner.value='20000';f.fail();e.policy.onsubmit({preventDefault(){}});await settle();
 assert.equal(e.enabled.checked,true);assert.equal(e.owner.value,'20000');assert.equal(e.status.textContent,'设置已变化');
});
test('independent pacing preference remains explicit in the saved policy',async()=>{
 const {elements:e,calls}=fixture();e.read.onclick();await settle();e.owner.value='20000';e.enabled.checked=true;e.concise_replies.checked=false;
 e.policy.onsubmit({preventDefault(){}});await settle();
 assert.deepEqual(JSON.parse(calls[1].options.body).values,{enabled:true,concise_replies:false,short_voice:true});
 assert.equal(e.concise_replies.checked,false);assert.equal(e.short_voice.checked,true);
});
