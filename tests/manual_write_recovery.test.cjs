const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/js/app.js'),'utf8');
function lab(responses){
 const nodes=new Map(['memory-add-content','memory-add-title','memory-add-layer','memory-add-category'].map(id=>[id,{value:id==='memory-add-content'?'手动正文':'',disabled:false}]));
 const requests=[];const button={};
 const context=vm.createContext({crypto:{randomUUID:()=> 'a'.repeat(32)},URLSearchParams,currentProjectId:null,projectsCache:[],
  document:{getElementById:id=>nodes.get(id),querySelectorAll:()=>[...nodes.values()],querySelector:()=>button},
  showToast(){},surfaceMemoryMaintenanceResult(){},toggleAddMemoryPanel(){},loadMemoryList(){},
  fetch:async(url,options)=>{requests.push({url,options});const item=responses.shift();if(item instanceof Error)throw item;return {ok:true,json:async()=>item};}});
 const start=source.indexOf('let manualMemorySubmission=');const end=source.indexOf('async function showMemoryReverseHealth(',start);
 vm.runInContext(source.slice(start,end),context);return {context,nodes,requests};
}
test('unknown manual save is followed only by a scoped read-only query',async()=>{
 const {context,nodes,requests}=lab([{status:'unknown',operation:{operation_id:'b'.repeat(32)}},{committed:true,items:[{memory_id:'stored'}]}]);
 await context.saveManualMemory();assert.equal(nodes.get('memory-add-content').disabled,true);
 await context.saveManualMemory();assert.match(requests[1].url,/^\/api\/kb\/manual-result\?/);assert.equal(requests[1].options,undefined);
});
test('disconnected manual form retries exactly the original request',async()=>{
 const {context,nodes,requests}=lab([new Error('disconnected'),{status:'success',memory_id:'stored'}]);
 await context.saveManualMemory();nodes.get('memory-add-content').value='new text';await context.saveManualMemory();
 assert.equal(requests[0].options.body,requests[1].options.body);
});
