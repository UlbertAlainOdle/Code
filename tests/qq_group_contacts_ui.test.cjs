const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('static/Chat4o5-Modern.html','utf8');
function fixture(kind,keys){
    const e={},calls=[];
    function node(){return {value:'',checked:false,children:[],events:{},
        addEventListener(n,f){this.events[n]=f;},appendChild(n){this.children.push(n);},
        replaceChildren(...nodes){this.children=nodes;}};}
    for(const key of keys){assert.ok(html.includes(`id="qq-${kind}-${key}"`));e[key]=node();}
    let handler=()=>{throw Error('unexpected fetch');},confirm=false;
    const ctx={document:{getElementById:id=>e[id.replace(`qq-${kind}-`,'')],createElement:node},
        fetch:async(url,init)=>{calls.push({url,init});return handler(url,init);},
        window:{confirm:()=>confirm},encodeURIComponent};
    vm.createContext(ctx);vm.runInContext(fs.readFileSync(`static/js/qq_${kind==='group'?'group_context':'contacts'}.js`,'utf8'),ctx);
    return {e,calls,reply(fn){handler=fn;},confirm(){confirm=true;}};
}
const response=value=>({ok:true,status:200,json:async()=>value});
const keys=['panel','feedback','owner','select','confirm','context','memory','write','admin','wake','retention','idle','days','writers','read','save'];
function group(){
    const f=fixture('group',keys);
    f.reply((url,init)=>init.method==='GET'?response({owners:['20000'],owner_id:'20000',revision:'settings-r1',task_control_enabled:true,
        groups:[{group_id:'90000',revision:4,values:{context_enabled:false,memory_enabled:false,write_enabled:false,admin_writes:false,reply_wakeup:false,writer_ids:[],retention_seconds:3600,topic_idle_seconds:1800,memory_retention_days:7}}]}):response({revision:5}));
    return f;
}
test('group settings load only on click; default-off draft and explicit CAS confirmation',async()=>{
    const f=group();assert.equal(f.calls.length,0);await f.e.read.events.click();
    assert.equal(f.e.context.checked,false);assert.equal(f.e.memory.checked,false);assert.equal(f.e.wake.checked,false);
    await f.e.save.events.click();assert.equal(f.calls.length,1);
    f.e.confirm.checked=true;f.e.context.checked=true;f.e.writers.value='30000, 40000';await f.e.save.events.click();
    const b=JSON.parse(f.calls[1].init.body);assert.equal(b.expected_revision,4);assert.equal(b.revision,'settings-r1');
    assert.equal(b.group_id,'90000');assert.equal(b.owner_id,'20000');assert.deepEqual(b.values.writer_ids,['30000','40000']);
    assert.equal(b.values.context_enabled,true);assert.equal(b.values.memory_enabled,false);assert.equal(f.e.confirm.checked,false);
});
test('group conflict preserves draft; changing owner invalidates loaded authority',async()=>{
    const f=group();await f.e.read.events.click();f.e.confirm.checked=true;f.e.writers.value='30000';
    f.reply(()=>({ok:false,status:409,json:async()=>({detail:'group_policy_changed'})}));await f.e.save.events.click();
    assert.equal(f.e.writers.value,'30000');assert.equal(f.e.confirm.checked,true);assert.equal(f.e.feedback.textContent,'group_policy_changed');
    f.e.owner.value='40000';f.e.owner.events.change();const n=f.calls.length;f.e.confirm.checked=true;
    await f.e.save.events.click();assert.equal(f.calls.length,n);
});
test('friend review never submits on boot/read/cancel, renders comment as text and approves one concrete request',async()=>{
    const f=fixture('contact',['panel','feedback','owner','allowed','items','read']);
    f.reply((url,init)=>init.method==='POST'?response({state:'approved'}):response({owners:['20000'],owner_id:'20000',revision:'s1',allowed_users:['20000'],
        requests:[{id:'fr-a',user:'30000',state:'pending',comment:'<script>not markup</script>'}]}));
    assert.equal(f.calls.length,0);await f.e.read.events.click();
    assert.ok(f.e.items.children[0].children[0].textContent.includes('<script>not markup</script>'));
    const click=f.e.items.children[0].children[1].events.click;await click();assert.equal(f.calls.length,1);
    f.confirm();await click();const b=JSON.parse(f.calls[1].init.body);
    assert.equal(b.owner_id,'20000');assert.equal(b.revision,'s1');assert.equal(b.approve,true);assert.equal(b.confirmed,true);
    assert.equal(f.calls[1].url,'/api/qq/contact-requests/fr-a');assert.equal('flag' in b,false);
});
test('unknown friend outcome is not retried; owner change removes action buttons',async()=>{
    const f=fixture('contact',['panel','feedback','owner','allowed','items','read']);
    f.reply(()=>response({owners:['20000'],owner_id:'20000',revision:'s1',allowed_users:[],requests:[{id:'fr-a',user:'30000',state:'pending'}]}));
    await f.e.read.events.click();f.confirm();f.reply(()=>{throw Error('unknown outcome');});
    await f.e.items.children[0].children[1].events.click();assert.equal(f.calls.length,2);
    assert.equal(f.e.feedback.textContent,'unknown outcome');f.e.owner.events.change();assert.equal(f.e.items.children.length,0);
});
test('per-group and friend drafts stay separate from global QQ controls',()=>{
    const controls=fs.readFileSync('static/js/qq_settings.js','utf8');
    const context={form:{addEventListener(n,f){this.handler=f;}},epoch:0,dirty:false,feedback(){}};
    vm.createContext(context);vm.runInContext(controls.split('\n').find(l=>l.includes("form.addEventListener('input'")),context);
    for(const id of ['qq-group-writers','qq-contact-owner']){context.form.handler({target:{id}});assert.equal(context.dirty,false);}
    context.form.handler({target:{id:'qq-control-enabled'}});assert.equal(context.dirty,true);
});
