const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function fixture() {
    const elements = new Map();
    function element() { return {value:'', selectionStart:0, append(){}, replaceChildren(){}, setAttribute(){}, removeAttribute(){},
        addEventListener(){}, querySelector(){return element();}, reset(){}}; }
    const document = {getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
        createElement:element,addEventListener(){}};
    const window = {};
    const agents = [{id:'saya',name:'Saya',builtin:true}, {id:'editor',name:'编辑'},
        {id:'editor2',name:'编辑二号'}, {id:'review',name:'Review-bot'}];
    vm.runInNewContext(fs.readFileSync('static/js/chat_agents.js','utf8'), {window,document,
        fetch:async()=>({ok:true,json:async()=>({agents})})});
    await window.SayaAgents.ready;
    return window.SayaAgents;
}

test('multiple mentions select distinct agents and remove only routing tokens', async()=>{
    const api = await fixture();
    const result = api.selection('@编辑 @Saya @编辑 请帮我检查');
    assert.equal(result.ids.join(','),'editor,saya');
    assert.equal(result.text,'请帮我检查');
});
test('Chinese names, longest matches, punctuation, and case work', async()=>{
    const api = await fixture();
    assert.equal(api.selection('@编辑二号，@sAyA: 你好').ids.join(','),'editor2,saya');
    assert.equal(api.selection('讨论\n@Review-BOT 这段文字').ids.join(','),'review');
});
test('email and longer unknown names do not invoke an agent', async()=>{
    const api = await fixture();
    const source = 'saya@example.com @编辑不存在 hello@Saya';
    const result = api.selection(source);
    assert.equal(result.ids.length,0);
    assert.equal(result.text,source);
});
