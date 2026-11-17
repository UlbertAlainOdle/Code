const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function fixture(){
    const e={},calls=[];for(const key of ['panel','owner','read','save','model','intake','images','quotes','count','bytes','total','pixels','timeout','confirm','feedback',
        'documents','docquotes','doccount','docbytes','docpages','docchars','doctimeout',
        'voice','voicechars','voiceseconds','voicetimeout','voiceretention']){
        assert.ok(fs.readFileSync('static/Chat4o5-Modern.html','utf8').includes(`id="qq-media-${key}"`));
        e[key]={value:'',checked:false,events:{},addEventListener(n,f){this.events[n]=f;}};
    }
    let reject=false;
    const ctx={encodeURIComponent,document:{getElementById:id=>e[id.replace('qq-media-','')]},fetch:async(url,init)=>{
        calls.push({url,init});const get=init.method==='GET';
        return {ok:get||!reject,status:get||!reject?200:409,json:async()=>get?{owner_id:'20000',revision:'s1',media_revision:3,
            vision_model_configured:false,implemented:['images','documents','tts_reply'],values:{intake_enabled:false,images_enabled:false,quote_images_enabled:false,
                voice_reply_enabled:false,voice_max_chars:120,voice_max_seconds:30,voice_timeout_seconds:45,voice_retention_seconds:300,
                documents_enabled:false,quote_documents_enabled:false,max_documents:2,max_document_bytes:5242880,
                max_document_pages:20,max_document_chars:12000,document_timeout_seconds:8,
                max_images:2,max_image_bytes:8388608,max_total_bytes:12582912,max_pixels:16000000,timeout_seconds:15}}:
            reject?{detail:'media_policy_changed'}:{media_revision:4}};
    }};
    vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/js/qq_media.js','utf8'),ctx);
    return {e,calls,reject(){reject=true;}};
}
test('media stays off until explicit read and confirmed CAS; capability status is truthful',async()=>{
    const f=fixture();assert.equal(f.calls.length,0);await f.e.read.events.click();
    assert.equal(f.e.intake.checked,false);assert.equal(f.e.images.checked,false);assert.ok(f.e.model.textContent.includes('尚未配置'));
    await f.e.save.events.click();assert.equal(f.calls.length,1);f.e.confirm.checked=true;f.e.images.checked=true;await f.e.save.events.click();
    const b=JSON.parse(f.calls[1].init.body);assert.equal(b.media_revision,3);assert.equal(b.revision,'s1');
    assert.equal(b.values.max_image_bytes,8388608);assert.equal(b.values.images_enabled,true);assert.equal(b.values.intake_enabled,false);
    assert.equal(f.e.confirm.checked,false);
});
test('media conflict preserves draft; owner input invalidates authority without saving',async()=>{
    const f=fixture();await f.e.read.events.click();f.reject();f.e.count.value='4';f.e.confirm.checked=true;await f.e.save.events.click();
    assert.equal(f.e.feedback.textContent,'media_policy_changed');assert.equal(f.e.count.value,'4');assert.equal(f.e.confirm.checked,true);
    f.e.owner.value='30000';f.e.owner.events.input();const n=f.calls.length;f.e.confirm.checked=true;await f.e.save.events.click();assert.equal(f.calls.length,n);
});
test('document permissions are independent, disabled by default and use server limits',async()=>{
    const f=fixture();await f.e.read.events.click();assert.equal(f.e.documents.checked,false);assert.equal(f.e.docquotes.checked,false);
    f.e.documents.checked=true;f.e.docpages.value='10';f.e.confirm.checked=true;await f.e.save.events.click();
    const b=JSON.parse(f.calls[1].init.body);assert.equal(b.values.documents_enabled,true);assert.equal(b.values.images_enabled,false);
    assert.equal(b.values.quote_documents_enabled,false);assert.equal(b.values.max_document_pages,10);assert.equal(b.values.max_document_bytes,5242880);
});
test('voice defaults off, does not imply intake or document access and carries its own bounds',async()=>{
    const f=fixture();await f.e.read.events.click();assert.equal(f.e.voice.checked,false);
    f.e.voice.checked=true;f.e.voiceseconds.value='20';f.e.confirm.checked=true;await f.e.save.events.click();
    const v=JSON.parse(f.calls[1].init.body).values;assert.equal(v.voice_reply_enabled,true);assert.equal(v.voice_max_seconds,20);
    assert.equal(v.intake_enabled,false);assert.equal(v.documents_enabled,false);assert.equal(v.voice_retention_seconds,300);
});
test('media draft does not dirty global QQ preferences; multiple owners can be supplied before first read',()=>{
    const context={form:{addEventListener(n,f){this.handler=f;}},epoch:0,dirty:false,feedback(){}};vm.createContext(context);
    vm.runInContext(fs.readFileSync('static/js/qq_settings.js','utf8').split('\n').find(l=>l.includes("form.addEventListener('input'")),context);
    context.form.handler({target:{id:'qq-media-count'}});assert.equal(context.dirty,false);
    const html=fs.readFileSync('static/Chat4o5-Modern.html','utf8');for(const name of ['group','contact','media'])assert.ok(html.includes(`input id="qq-${name}-owner"`));
});
