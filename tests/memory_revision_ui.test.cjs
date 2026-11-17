const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/app.js','utf8');
function fixture({confirm=true,responses=[],version='a'.repeat(64)}={}) {
    const calls=[],messages=[],reviews=[],drafts=new Map();let loads=0;
    const card={dataset:{contentVersion:version,projectId:'project-a'}};
    const context={memoryRevisionDrafts:drafts,document:{querySelector:()=>card},cssEscapeValue:x=>x,
        window:{prompt:()=> '新增信息',confirm:text=>{reviews.push(text);return confirm;}},
        fetch:async(url,init)=>{calls.push({url,body:init?JSON.parse(init.body):null});
            const next=responses.shift()||{ok:true,data:{status:'preview',old:'旧事实',new:'旧事实；新增信息',confirmation_token:'t'.repeat(32)}};
            return {ok:next.ok!==false,status:next.status||200,json:async()=>next.data};},
        showToast:x=>messages.push(x),loadMemoryList:async()=>{loads++;}};
    vm.createContext(context);
    for(const name of ['requireMemoryVersion','previewMemoryRevision','showMemoryRevisionHistory']) {
        const start=source.search(new RegExp(`^    (?:async )?function ${name}\\(`,'m'));
        const rest=source.slice(start+1),end=rest.search(/^    (?:async )?function /m);
        vm.runInContext(source.slice(start,start+1+end),context);
    }
    return {c:context,calls,messages,reviews,drafts,loads:()=>loads};
}
test('preview holds the rendered version and project; decline never commits and keeps draft',async()=>{
    const f=fixture({confirm:false});await f.c.previewMemoryRevision('m','append');
    assert.equal(f.calls.length,1);assert.equal(f.calls[0].body.expected_version,'a'.repeat(64));
    assert.equal(f.calls[0].body.project_id,'project-a');assert.equal(f.loads(),0);
    assert.equal(f.drafts.get('m:append'),'新增信息');assert.match(f.reviews[0],/原内容：[\s\S]*新内容/);
});
test('stale preview or confirmation never shows success or loses draft',async()=>{
    for(const responses of [
        [{ok:false,status:409,data:{detail:'版本已变化'}}],
        [{data:{status:'preview',old:'旧',new:'新',confirmation_token:'t'.repeat(32)}},{data:{status:'stale'}}]
    ]) {
        const f=fixture({responses});await f.c.previewMemoryRevision('m','correct');
        assert.equal(f.loads(),0);assert.equal(f.drafts.get('m:correct'),'新增信息');
        assert.ok(!f.messages.some(x=>x.startsWith('同一条记忆已更新')));
    }
});
test('successful confirmation sends only the opaque token and reloads once',async()=>{
    const f=fixture({responses:[{data:{status:'preview',old:'旧',new:'新',confirmation_token:'token'}},{data:{status:'applied'}}]});
    await f.c.previewMemoryRevision('m','append');
    assert.equal(JSON.stringify(f.calls[1].body),JSON.stringify({confirmation_token:'token'}));
    assert.equal(f.loads(),1);assert.equal(f.drafts.size,0);
});
test('missing card version prevents any network mutation',async()=>{
    const f=fixture({version:''});await f.c.previewMemoryRevision('m','append');
    assert.equal(f.calls.length,0);assert.equal(f.drafts.get('m:append'),'新增信息');
});
test('partial projection keeps draft and does not invite a second save',async()=>{
    const f=fixture({responses:[{data:{status:'preview',old:'旧',new:'新',confirmation_token:'token'}},{data:{status:'partial'}}]});
    await f.c.previewMemoryRevision('m','correct');
    assert.equal(f.loads(),0);assert.equal(f.drafts.get('m:correct'),'新增信息');
    assert.ok(f.messages.some(x=>x.includes('请勿重复保存')));
});
