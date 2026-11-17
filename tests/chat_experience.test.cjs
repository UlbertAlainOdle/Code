const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function sessionLoadFixture() {
    const source=fs.readFileSync('static/js/app.js','utf8');
    const method=source.slice(source.indexOf('    async function loadSession(id,'),source.indexOf('    function archiveSession('));
    const content={innerHTML:'EXISTING_WEB_CHAT'},toasts=[];
    const context={isWaitingForResponse:false,currentRoleMode:'',currentHistoryLength:2,currentSessionId:'session_web.jsonl',
        document:{getElementById:()=>content},window:{},showToast:message=>toasts.push(message),
        setCurrentRoleMode(){},updateModeProgressRing(){},updateMoodChip(){},enhanceListenerSessionOnLoad(){}};
    vm.createContext(context);vm.runInContext(method,context);
    return {context,content,toasts};
}

test('rejected cached channel session and failed HTTP load preserve the current web chat',async()=>{
    for(const status of [404,500]) {
        const {context,content,toasts}=sessionLoadFixture();
        context.fetch=async()=>({ok:false,status,json:async()=>({detail:'Session is not available in web history'})});
        assert.equal(await context.loadSession('session_qq_cached.jsonl',{skipConfirm:true}),false);
        assert.equal(context.currentSessionId,'session_web.jsonl');assert.equal(context.currentHistoryLength,2);
        assert.equal(content.innerHTML,'EXISTING_WEB_CHAT');assert.match(toasts[0],/Load failed/);
    }
});

test('successful ordinary web session load still switches the active chat',async()=>{
    const {context,content}=sessionLoadFixture();
    context.fetch=async()=>({ok:true,json:async()=>({session_id:'session_new.jsonl',items:[]})});
    assert.equal(await context.loadSession('session_new.jsonl',{skipConfirm:true}),true);
    assert.equal(context.currentSessionId,'session_new.jsonl');assert.equal(context.currentHistoryLength,0);
    assert.equal(content.innerHTML,'');
});

class Element {
    constructor(tag='div') { this.tag = tag; this.children=[]; this.dataset={}; this.attributes={}; this.className=''; this.style={setProperty:(key,value)=>this.style[key]=value}; }
    get classList() { return {add:x=>this.className += ' '+x, toggle:(x,on)=>{this.className=this.className.split(' ').filter(c=>c!==x).concat(on?[x]:[]).join(' ');}}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children=children; }
    insertBefore(child, before) { const i=this.children.indexOf(before); this.children.splice(i<0?this.children.length:i,0,child); }
    get firstChild() { return this.children[0]; }
    get lastChild() { return this.children.at(-1); }
    setAttribute(key,value) { this.attributes[key]=value; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener() {}
    querySelector(selector) {
        const match = x => selector.startsWith('.') ? selector.slice(1).split('.').every(c=>x.className.split(' ').includes(c)) : x.tag===selector;
        for (const child of this.children) { if(match(child))return child; const found=child.querySelector(selector); if(found)return found; }
        return null;
    }
}
function fixture() {
    const elements = {'send-btn':new Element('button'), 'chat-stop-btn':new Element('button')};
    const pending=[], calls=[],callbacks=[];
    const window={SayaSpeech:{stop(){calls.push('stop');}, speak(text,context){calls.push({text,context}); return new Promise(resolve=>pending.push(resolve));}}};
    const document={createElement:tag=>new Element(tag),getElementById:id=>elements[id],querySelector:()=>null,addEventListener(name,callback){if(name==='DOMContentLoaded')callbacks.push(callback);}};
    vm.runInNewContext(fs.readFileSync('static/js/chat_experience.js','utf8'),{window,document,structuredClone,URL,localStorage:{getItem:()=>null,setItem(){}}});
    return {ui:window.SayaChatUI,elements,pending,calls,callbacks,window};
}

test('thinking picker uses server capabilities and shared defaults despite stale browser model',()=>{
    const {ui,elements,callbacks,window}=fixture();
    elements['thinking-effort']=new Element('select');
    elements['model-select']={value:'api',addEventListener(){}};
    elements['api-use-shared-credentials']={checked:true,addEventListener(){}};
    elements['api-model-input']={value:'old-incompatible-model',addEventListener(){}};
    elements['api-url-input']={value:'https://old-provider.example/v1',addEventListener(){}};
    let configuration={thinking_supported:true,follow_global:false,thinking_effort:'auto'};
    window.SayaQQSettings={thinkingConfiguration:()=>configuration};
    callbacks.forEach(callback=>callback());
    assert.equal(elements['thinking-effort'].disabled,false);
    configuration={thinking_supported:false,follow_global:false,thinking_effort:'auto'};
    assert.equal(ui.thinkingEffort(),'auto');
    assert.equal(elements['thinking-effort'].disabled,true);
    configuration={thinking_supported:true,follow_global:true,thinking_effort:'high'};
    assert.equal(ui.thinkingEffort(),'high');
    assert.equal(elements['thinking-effort'].value,'high');
    assert.match(elements['thinking-effort'].title,/共享默认/);
});
test('send and stop alternate in idle, generating and restored states',()=>{
    const {ui,elements}=fixture();
    for(const busy of [false,true,false]) {ui.busy(busy); assert.equal(elements['send-btn'].hidden,busy);assert.equal(elements['chat-stop-btn'].hidden,!busy);}
});
test('same speaker toggles stop; another speaker owns playback despite stale completion',async()=>{
    const {ui,calls,pending}=fixture(), a=new Element('button'),b=new Element('button');
    const first=ui.speak(a,'old',{moodState:{speech_mood:'sad'}});
    assert.equal(a.attributes['aria-pressed'],'true');
    const second=ui.speak(b,'new',{moodState:{speech_mood:'happy'}});
    assert.equal(a.attributes['aria-pressed'],'false');
    assert.equal(b.attributes['aria-pressed'],'true');
    pending[0]();await first;
    assert.equal(b.attributes['aria-pressed'],'true');
    await ui.speak(b,'new',{});
    assert.equal(b.attributes['aria-pressed'],'false');
    assert.equal(calls.filter(x=>typeof x==='object').length,2);
    pending[1]();await second;
});
test('each reply freezes its snapshot and shows eight bounded histogram bars',()=>{
    const {ui}=fixture(), row=new Element(),actions=new Element();actions.className='msg-actions';row.append(actions);
    const state={happiness:72,excitement:59,stress:4,speech_mood:'happy'};
    ui.attachMood(row,state,true);state.happiness=1;
    assert.equal(row._moodState.happiness,72);
    const chip=actions.querySelector('.message-mood');
    assert.match(chip.attributes['aria-label'],/快乐 72.*历史估计/);
    assert.equal(chip.querySelector('.mood-histogram-title').textContent,'情绪光谱');
    assert.equal(chip.querySelector('.mood-histogram-subtitle').textContent,'历史记录估计 · 0–100');
    assert.equal(chip.querySelector('.mood-histogram-plot').children.length,8);
    assert.equal(chip.querySelector('.mood-histogram-bar').style.height,'72%');
});
test('actual progress events form an expandable record before a complete answer',()=>{
    const {ui}=fixture(),row=new Element(),bubble=new Element(),body=new Element();bubble.className='message-bubble ai';body.className='message-body';row.append(bubble);bubble.append(body);
    ui.phase(row,'searching','正在搜索……');ui.phase(row,'reading','正在读取来源……');ui.phase(row,'reading','正在读取来源……');
    const progress=bubble.querySelector('.response-progress');assert.equal(progress.open,true);assert.equal(progress.querySelector('ol').children.length,2);assert.equal(body.textContent,undefined);
    ui.phase(row,'completed','处理完成');assert.equal(progress.open,false);assert.equal(progress.dataset.finished,'true');
});

test('polling an unchanged snapshot keeps tooltip elements intact',()=>{
    const {ui}=fixture(),chip=new Element('button');
    const state={happiness:72,curiosity:60};
    ui.paintMood(chip,state);const tooltip=chip.querySelector('.mood-histogram');
    ui.paintMood(chip,{...state});assert.equal(chip.querySelector('.mood-histogram'),tooltip);
    ui.paintMood(chip,{...state,curiosity:80});assert.notEqual(chip.querySelector('.mood-histogram'),tooltip);
});
test('thinking selector follows the actual provider and ignores unsupported saved effort',()=>{
    const {ui,elements,callbacks}=fixture();
    for(const [id,value] of Object.entries({'model-select':'api','api-model-input':'deepseek-v4-flash','api-url-input':'https://api.deepseek.com/v1','thinking-effort':'max'})) {elements[id]=new Element('select');elements[id].value=value;}
    callbacks[0]();elements['thinking-effort'].value='max';
    assert.equal(ui.thinkingEffort(),'max');
    elements['api-model-input'].value='openai/gpt-oss-120b';
    assert.equal(ui.thinkingEffort(),'auto');assert.equal(elements['thinking-effort'].disabled,true);
});
