const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('static/js/app.js','utf8');
function fixture(response={status:200,data:{status:'success'}}){
    const version='a'.repeat(64), calls=[],toasts=[];
    const card={dataset:{contentVersion:version},closest:()=>({}),replaceWith(){this.replaced=true;}};
    const state={data:{graph_version:version,nodes:[{type:'memory',ref_id:'a',label:'A'},{type:'memory',ref_id:'b',label:'B'}]},linkSourceId:'a',linkRelation:'related_to'};
    const c={document:{querySelector:()=>card,createElement:()=>({dataset:{}})},memoryGraphState:state,
        cssEscapeValue:x=>x,loadMemoryV4Panel:async()=>{},loadMemoryGraphView:async()=>{},memoryGraphRelationLabel:x=>x,
        showToast:m=>toasts.push(m),showConfirmDialog:x=>{c.dialog=x;},showPromptDialog:x=>{c.dialog=x;},
        updateChatMemoryConfirmTitle:()=>{},
        fetch:async(url,init)=>{calls.push({url,body:JSON.parse(init.body)});return {ok:response.status<400,status:response.status,json:async()=>response.data};}};
    vm.createContext(c);
    for(const name of ['requireMemoryVersion','applyMemoryV4Control','correctMemoryV4','forgetMemoryV4',
        'completeMemoryGraphLink','doCreateMemoryGraphRelationship','removeMemoryGraphEdge','doRemoveMemoryGraphRelationship','resolveMemoryConflict']){
        const start=source.search(new RegExp(`^    (?:async )?function ${name}\\(`,'m'));
        const tail=source.slice(start+1), end=tail.search(/^    (?:async )?function /m);
        // Some declarations are followed by window exports; provide window without invoking UI.
        c.window=c;
        vm.runInContext(source.slice(start,start+1+end).split('\n    window.')[0],c);
    }
    return {c,card,state,calls,toasts,version};
}
test('graph link confirmation captures both targets, relation and snapshot',async()=>{
    const f=fixture();await f.c.completeMemoryGraphLink('b');
    f.state.linkSourceId='other';f.state.linkRelation='supports';f.state.data.graph_version='b'.repeat(64);
    f.c.dialog.onConfirm(()=>{});await new Promise(setImmediate);
    assert.deepEqual(f.calls[0].body,{source_memory_id:'a',target_memory_id:'b',relation:'related_to',confidence:1,expected_graph_version:f.version});
});
test('graph remove uses edge id and keeps dialog open on stale snapshot',async()=>{
    const f=fixture({status:409,data:{detail:'graph changed'}});let closed=false;
    await f.c.removeMemoryGraphEdge('edge1');f.c.dialog.onConfirm(()=>{closed=true;});await new Promise(setImmediate);
    assert.equal(f.calls[0].body.edge_id,'edge1');assert.equal(f.calls[0].body.expected_graph_version,f.version);
    assert.equal(closed,false);assert.equal(f.toasts[0],'graph changed');
});
test('V4 disabled response cannot appear as submitted',async()=>{
    const f=fixture({status:200,data:{status:'disabled',reason:'flag disabled'}});
    await assert.rejects(f.c.applyMemoryV4Control('pin','m'),/flag disabled/);
    assert.equal(f.calls[0].body.expected_version,f.version);
});
test('V4 correction dialog holds original read version while list changes',async()=>{
    const f=fixture({status:409,data:{detail:'changed'}});let closed=false;
    f.c.correctMemoryV4('m');f.card.dataset.contentVersion='b'.repeat(64);
    f.c.dialog.onConfirm('my draft',()=>{closed=true;});await new Promise(setImmediate);
    assert.equal(f.calls[0].body.expected_version,f.version);assert.equal(closed,false);
});
test('conflict partial commit preserves card and does not show resolved',async()=>{
    const f=fixture({status:503,data:{detail:'committed; verify state'}});
    await f.c.resolveMemoryConflict('cf','supersede_old');
    assert.equal(f.calls[0].body.expected_version,f.version);assert.equal(f.card.replaced,undefined);
    assert.match(f.toasts[0],/verify state/);
});
test('legacy conflict card without a version sends no verdict',async()=>{
    const f=fixture();delete f.card.dataset.contentVersion;
    await f.c.resolveMemoryConflict('cf','keep_both');assert.equal(f.calls.length,0);
    assert.equal(f.card.replaced,undefined);
});
