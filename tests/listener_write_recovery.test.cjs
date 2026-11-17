const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/js/app.js'),'utf8');
const start=source.indexOf('function renderListenerProposalCard(');
const end=source.indexOf('async function enhanceListenerSessionOnLoad(',start);
function lab(responses){
    const inputs={'.lc-title-input':{value:'标题'},'.lc-content':{value:'正文'},'.lc-layer':{value:'semantic'},'.lc-category':{value:'general'}};
    const button={};const card={querySelector:selector=>inputs[selector]||button,querySelectorAll:()=>Object.values(inputs)};
    const requests=[];
    const context=vm.createContext({currentSessionId:'session_listener.jsonl',LISTENER_LAYERS:[],crypto:{randomUUID:()=> 'a'.repeat(32)},
        URLSearchParams,document:{getElementById:()=>({appendChild(){}}),createElement:()=>card},
        escapeHtml:s=>String(s).replace(/</g,'&lt;'),ic:()=>'',appendListenerNotice(){},setListenerInputLocked(){},showToast(){},
        enhanceListenerSessionOnLoad:async()=>{},fetch:async(url,options)=>{
            requests.push({url,options});const payload=responses.shift();if(payload instanceof Error)throw payload;
            return {ok:true,json:async()=>payload};
        }});
    vm.runInContext(source.slice(start,end),context);
    return {context,button,inputs,requests,card};
}
test('partial Listener save only repairs original files on next click',async()=>{
    const {context,button,inputs,requests}=lab([
        {operation:{committed:true,operation_id:'b'.repeat(32)},recovery_pending:true,message:'待恢复'},
        {operation:{committed:true,operation_id:'b'.repeat(32)},confirmation_version:'d'.repeat(64),proposal:{title:'标题',content:'正文',layer:'semantic'},action:'created'}]);
    context.renderListenerProposalCard({content:'正文'},false,'c'.repeat(64));
    await button.onclick();
    assert.equal(inputs['.lc-content'].disabled,true);
    assert.equal(JSON.parse(requests[0].options.body).expected_version,'c'.repeat(64));
    await button.onclick();
    assert.equal(requests[1].url,'/api/listener/recover');
    assert.deepEqual(JSON.parse(requests[1].options.body),{session_id:'session_listener.jsonl',operation_id:'b'.repeat(32)});
});
test('network failure retries the frozen request and never adopts subsequently edited fields',async()=>{
    const {context,button,inputs,requests}=lab([new Error('disconnected'),{operation:{committed:false,operation_id:'b'.repeat(32)}}]);
    context.renderListenerProposalCard({content:'正文'},false,'c'.repeat(64));
    await button.onclick();inputs['.lc-content'].value='different';await button.onclick();
    assert.equal(requests[0].options.body,requests[1].options.body);
});
test('restored unknown Listener result is queried without a memory POST',async()=>{
    const {context,button,requests,card}=lab([{committed:false,items:[{state:'unknown'}]}]);
    context.renderListenerProposalCard({content:'</textarea><script>'},true,'c'.repeat(64),{operation_id:'b'.repeat(32),committed:false});
    assert.match(card.innerHTML,/&lt;\/textarea>/);
    await button.onclick();assert.match(requests[0].url,/^\/api\/listener\/result\?/);
    assert.equal(requests[0].options,undefined);
});
