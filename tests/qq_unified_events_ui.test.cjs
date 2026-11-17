const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(){
 const elements={},calls=[];
 const values={version:1,enabled:false,minimum_level:'important',group_active:5,group_total:20,max_contributions:48,contribution_chars:2048,summary_chars:2000};
 const state={policy:{revision:7,values},preview:{version:'fixture',personal:1,legacy_retained:0},groups:[{id:'fixture',group:'90000',revision:1,summary:'<img src=x onerror=secret()> 未决分歧'}],more:false};
 const doc={getElementById(id){return elements[id]??=( {value:id==='owner'?'20000':'',checked:false,disabled:false,textContent:'',reportValidity:()=>true});}};
 vm.runInNewContext(fs.readFileSync('static/js/qq_unified_events.js','utf8'),{document:doc,confirm:()=>true,
  fetch:async(path,options)=>{calls.push({path,options});return {ok:true,status:200,json:async()=>state};}});
 return {elements,calls,state};
}
test('no automatic request; read uses textContent and preserves stored capacity fields',async()=>{
 const {elements:e,calls,state}=fixture();assert.equal(calls.length,0);
 await e['unified-read'].onclick();assert.equal(calls.length,1);
 assert.match(e['unified-status'].textContent,/<img/);assert.equal(e['unified-status'].innerHTML,undefined);
 assert.equal(e['unified-level'].value,'important');assert.equal(e['unified-total'].value,20);
 await e['unified-save'].onclick();const body=JSON.parse(calls[1].options.body);
 assert.equal(body.revision,7);assert.equal(body.values.max_contributions,48);assert.equal(body.expected_version,'fixture');
 assert.equal(body.confirmed,true);assert.equal(calls[1].options.method,'PUT');
});
test('read refuses to overwrite unsaved event drafts',async()=>{
 const {elements:e,calls}=fixture();await e['unified-read'].onclick();e['unified-level'].value='familiar';
 await e['unified-read'].onclick();assert.equal(calls.length,1);assert.match(e['unified-status'].textContent,/未保存草稿/);
});
