const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('static/js/app.js','utf8');
const version='a'.repeat(64), next='b'.repeat(64);
function fixture(replies=[]){
    const card={dataset:{contentVersion:version},querySelector:()=>({value:'draft'})};
    const calls=[],resolved=[],errors=[];
    const context={document:{querySelector:()=>card},cssEscapeValue:x=>x,
        readChatMemoryImportance:()=>null,readMemoryCandidateUpdates:()=>({content:'draft'}),
        fetch:async(url,init)=>{calls.push({url,body:JSON.parse(init.body)});
            const r=replies.shift()||{status:200,data:{status:'success',content_version:next}};
            return {status:r.status,ok:r.status<400,json:async()=>r.data};},
        showToast:m=>errors.push(m),resolveChatMemoryCard:(id,kind)=>resolved.push(kind),
        resolveExplicitMemoryCard:(id,kind)=>resolved.push(kind),loadMemoryList:async()=>{},
        surfaceMemoryMaintenanceResult:()=>{}};
    vm.createContext(context);
    for(const name of ['memoryVersionFor','requireMemoryVersion','memoryMutationResponse','updateRenderedMemory',
        'updateMemoryContent','toggleMemoryStar','keepExplicitMemory','ignoreExplicitMemory',
        'confirmAutonomousMemoryCandidate','ignoreAutonomousMemoryCandidate']){
        const start=source.search(new RegExp(`^    (?:async )?function ${name}\\(`,'m'));
        assert.ok(start>=0,name);
        const rest=source.slice(start+1),end=rest.search(/^    (?:async )?function /m);
        vm.runInContext(source.slice(start,start+1+end),context);
    }
    return {c:context,card,calls,resolved,errors};
}
test('content conflict keeps rendered version and draft without retry',async()=>{
    const f=fixture([{status:409,data:{detail:'changed'}}]);
    await f.c.updateMemoryContent('m','my draft');
    assert.equal(f.calls.length,1);assert.equal(f.calls[0].body.expected_version,version);
    assert.equal(f.card.dataset.contentVersion,version);assert.match(f.errors[0],/草稿已保留/);
});
test('successful content update advances only that rendered row',async()=>{
    const f=fixture();await f.c.updateMemoryContent('m','new');
    assert.equal(f.card.dataset.contentVersion,next);
    await f.c.updateMemoryContent('m','newer');assert.equal(f.calls[1].body.expected_version,next);
});
test('old unversioned card cannot silently overwrite or send a request',async()=>{
    const f=fixture();delete f.card.dataset.contentVersion;
    await f.c.keepExplicitMemory('m');await f.c.ignoreAutonomousMemoryCandidate('c');
    assert.equal(f.calls.length,0);assert.deepEqual(f.resolved,[]);assert.equal(f.errors.length,2);
});
test('candidate partial commit leaves card and draft for reconciliation',async()=>{
    const f=fixture([{status:503,data:{detail:'记忆已保存，候选状态待核对；请重新读取，勿重复创建'}}]);
    await f.c.confirmAutonomousMemoryCandidate('c');
    assert.equal(f.calls[0].body.expected_version,version);assert.deepEqual(f.resolved,[]);
    assert.match(f.errors[0],/候选状态待核对/);
});
test('ignore conflict and failed explicit delete never show success',async()=>{
    const f=fixture([{status:409,data:{}},{status:200,data:{status:'confirmation_required',confirmation_token:'t'}},
        {status:409,data:{}}]);
    await f.c.ignoreAutonomousMemoryCandidate('c');await f.c.ignoreExplicitMemory('m');
    assert.deepEqual(f.resolved,[]);assert.equal(f.errors.length,2);
    assert.equal(f.calls[2].body.expected_version,version);assert.equal(f.calls[2].body.confirmation_token,'t');
});
test('explicit save failure preserves card; truncated preview cannot replace unseen body',async()=>{
    const f=fixture([{status:500,data:{detail:'write failed'}}]);
    await f.c.keepExplicitMemory('m');assert.deepEqual(f.resolved,[]);
    f.card.dataset.contentTruncated='true';await f.c.confirmAutonomousMemoryCandidate('c');
    assert.equal(f.calls.length,1);assert.deepEqual(f.resolved,[]);assert.match(f.errors.at(-1),/完整条目/);
});
test('successful candidate ignore resolves only after acknowledgement',async()=>{
    const f=fixture();await f.c.ignoreAutonomousMemoryCandidate('c');assert.deepEqual(f.resolved,['ignored']);
    assert.equal(f.calls[0].body.expected_version,version);
});
