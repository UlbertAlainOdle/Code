const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture({conflict=false,accept=true}={}){
 const elements={};
 const document={getElementById(id){return elements[id]??={value:'',checked:false,disabled:false,textContent:'',replaceChildren(){},append(){}};},createElement(){return {};}};
 const calls=[];
 document.getElementById('owner');
 const fetch=async(path,options)=>{
  calls.push({path,options});let value;
  if(options.method==='POST')value=conflict?{detail:'预览已变化'}:{status:'success',migrated:0,enabled:false};
  else if(path.endsWith('/migration'))value={version:'frozen-preview',entries:[],can_apply:true,already_migrated:false};
  else value={enabled:false,identity_migrated:false,items:[{subject:'20000',title:'<script>safe</script>',id:'id',revision:1,state:'active',capacity:{max_active:5,max_total:20},visible_sources:['private']}],next_offset:null};
  return {ok:!(conflict&&options.method==='POST'),status:conflict&&options.method==='POST'?409:200,json:async()=>value};
 };
 vm.runInNewContext(fs.readFileSync('static/js/qq_context.js','utf8'),{document,fetch,confirm:()=>accept,encodeURIComponent,console});
 return {elements,calls};
}
test('personal event page stays read-only until a reviewed version is confirmed',async()=>{
 const {elements:e,calls}=fixture();assert.equal(calls.length,0);
 await e['events-read'].onclick();assert.equal(calls.length,2);assert.ok(e['events-status'].textContent.includes('<script>safe</script>'));
 assert.equal(e['events-status'].innerHTML,undefined);
 e.owner.value='20000';await e['events-migrate'].onclick();
 const body=JSON.parse(calls[2].options.body);assert.equal(body.expected_version,'frozen-preview');assert.equal(body.confirmed,true);
 assert.equal(body.owner_id,'20000');assert.ok(e['events-status'].textContent.includes('仍关闭'));
});
test('declining migration makes no write; conflict retains the selected account without retry',async()=>{
 const declined=fixture({accept:false});await declined.elements['events-read'].onclick();declined.elements.owner.value='20000';
 await declined.elements['events-migrate'].onclick();assert.equal(declined.calls.length,2);
 const {elements:e,calls}=fixture({conflict:true});await e['events-read'].onclick();e.owner.value='20000';
 await e['events-migrate'].onclick();assert.equal(calls.length,3);assert.equal(e.owner.value,'20000');
 assert.equal(e['events-migrate'].disabled,true);assert.ok(e['events-status'].textContent.includes('重新读取'));
});
