const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/qq_memory_management.js', 'utf8');
const html = fs.readFileSync('static/Chat4o5-Modern.html', 'utf8');

function fixture() {
    const elements = {};
    for (const key of ['panel','owner','kind','query','refresh','prev','next','items','editor',
        'detail','subject','visibility','groups','requesters','writers','audience','confirm','save','revoke','feedback']) {
        assert.ok(html.includes(`id="qq-memory-${key}"`));
        elements[key] = element();
    }
    function element() {
        return {value:'', checked:false, children:[], events:{},
            replaceChildren(){this.children=[];}, appendChild(child){this.children.push(child);},
            addEventListener(name, fn){this.events[name]=fn;}};
    }
    elements.owner.value = '20000'; elements.kind.value = 'saved';
    let reject = false;
    const calls = [];
    const policy = {revision:1, subject_id:'20000', visibility:'groups', groups:['90000'], requesters:[], writers:[]};
    const detail = {memory:{id:'a', title:'<script>sample</script>',content:'BODY',source:'manual'},
        content_version:'a'.repeat(64), policy:null, policy_current:false, authorized_groups:['90000']};
    const context = {document:{getElementById:id=>elements[id.replace('qq-memory-','')], createElement:element}, URLSearchParams,
        fetch:async(url, init)=>{
            calls.push({url, init});
            if(init.method==='PUT') return {ok:!reject, status:reject?409:200,
                json:async()=>reject?{detail:'version conflict'}:{...detail,policy}};
            if(url.includes('/item?'))return {ok:true, json:async()=>detail};
            return {ok:true, json:async()=>({owner_id:'20000', owners:['20000'], revision:'r1', kind:'saved',
                page:1, total:11, has_next:true, snapshot:'catalog1',items:[{id:'a',title:'<script>sample</script>',preview:'BODY',status:'active'}]})};
        }};
    vm.createContext(context); vm.runInContext(source, context);
    return {e:elements, calls, reject:()=>{reject=true;}};
}
async function open(f) {
    await f.e.refresh.events.click();
    await f.e.items.children[0].children[1].events.click();
}
test('no private load on boot, safe text rendering and explicit confirmation', async()=>{
    const f=fixture(); assert.equal(f.calls.length,0); await open(f);
    assert.ok(f.e.detail.textContent.includes('<script>sample</script>'));
    await f.e.save.events.click(); assert.equal(f.calls.length,2);
    assert.ok(f.e.feedback.textContent.includes('勾选'));
    f.e.groups.value='90000'; f.e.visibility.value='groups'; f.e.confirm.checked=true;
    await f.e.save.events.click();
    const body=JSON.parse(f.calls[2].init.body);
    assert.equal(body.owner_id,'20000'); assert.equal(body.memory_id,'a');
    assert.equal(body.content_version,'a'.repeat(64)); assert.equal(body.expected_revision,0);
    assert.equal(body.revision,'r1'); assert.deepEqual(body.groups,['90000']);
    assert.equal(f.e.confirm.checked,false); assert.equal(f.e.panel.disabled,false);
});
test('conflict preserves permission draft and confirmation for review',async()=>{
    const f=fixture(); await open(f); f.reject(); f.e.requesters.value='30000'; f.e.confirm.checked=true;
    await f.e.save.events.click(); assert.equal(f.e.requesters.value,'30000');
    assert.equal(f.e.feedback.textContent,'version conflict');
});
test('revoke sends explicit empty audience and uses current CAS',async()=>{
    const f=fixture();await open(f);f.e.confirm.checked=true;await f.e.save.events.click();
    f.e.confirm.checked=true;await f.e.revoke.events.click();
    const b=JSON.parse(f.calls.at(-1).init.body);
    assert.equal(b.expected_revision,1);assert.equal(b.visibility,'private');
    assert.deepEqual(b.groups,[]);assert.deepEqual(b.requesters,[]);assert.deepEqual(b.writers,[]);
});
test('paging carries catalog snapshot and changing scope clears detail',async()=>{
    const f=fixture();await f.e.refresh.events.click();await f.e.next.events.click();
    assert.ok(f.calls.at(-1).url.includes('snapshot=catalog1'));
    await open(f);f.e.owner.value='30000';f.e.owner.events.input();
    assert.equal(f.e.editor.hidden,true);f.e.confirm.checked=true;
    const before=f.calls.length;await f.e.save.events.click();assert.equal(f.calls.length,before);
});
test('memory inputs do not dirty unrelated QQ settings',()=>{
    const controls=fs.readFileSync('static/js/qq_settings.js','utf8');
    const context={form:{addEventListener(name,fn){this.handler=fn;}},epoch:0,dirty:false,feedback(){}};
    vm.createContext(context);vm.runInContext(controls.split('\n').find(line=>line.includes("form.addEventListener('input'")),context);
    context.form.handler({target:{id:'qq-memory-requesters'}});assert.equal(context.dirty,false);
    context.form.handler({target:{id:'qq-control-enabled'}});assert.equal(context.dirty,true);
});
