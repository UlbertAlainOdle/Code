const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/qq_settings.js','utf8');
const html=fs.readFileSync('static/Chat4o5-Modern.html','utf8');

function fixture(){
 const declared=source.match(/const fields = (\[[\s\S]*?\]);/)[1];
 const fields=vm.runInNewContext(declared);
 const inputs={};
 for(const key of fields){
  const tag=html.match(new RegExp('<(?:input|select)[^>]*id="qq-control-'+key+'"[^>]*>'))?.[0];
  assert.ok(tag,'field must exist in actual settings HTML: '+key);
  inputs['qq-control-'+key]={type:tag.includes('type="checkbox"')?'checkbox':tag.includes('type="number"')?'number':'select-one'};
 }
 inputs['qq-settings-status']={dataset:{}};
 inputs['qq-settings-identity']={};inputs['qq-settings-capabilities']={};
 const context={$:id=>inputs[id],fields,rememberWebConfiguration(){},renderShared(){},current:null,dirty:true};
 vm.createContext(context);
 const render=source.slice(source.indexOf('    function render(payload)'),source.indexOf('    function rememberWebConfiguration'));
 const collect=source.slice(source.indexOf('    function collect()'),source.indexOf('    async function action'));
 vm.runInContext(render+'\n'+collect,context);
 return {context,inputs,fields};
}

test('QQ delivery settings render and collect the same persisted values as other QQ controls',()=>{
 const {context,inputs,fields}=fixture();
 const settings=Object.fromEntries(fields.map(key=>[key,inputs['qq-control-'+key].type==='checkbox'?false:1]));
 Object.assign(settings,{delivery_mode:'chat',bubble_strategy:'length',reply_reference:true,bubble_chars:240,reply_max_messages:3});
 context.payload={settings,status:{running:true},identity:{bot_id:'10000',owner_ids:['20000'],model:'api'}};
 vm.runInContext('render(payload)',context);
 assert.equal(context.dirty,false);
 assert.equal(inputs['qq-control-reply_reference'].checked,true);
 inputs['qq-control-delivery_mode'].value='legacy';
 inputs['qq-control-bubble_strategy'].value='natural';
 inputs['qq-control-bubble_chars'].value='180';
 inputs['qq-control-reply_max_messages'].value='2';
 inputs['qq-control-reply_reference'].checked=false;
 const result=vm.runInContext('collect()',context);
 assert.equal(result.delivery_mode,'legacy');assert.equal(result.bubble_chars,180);
 assert.equal(result.bubble_strategy,'natural');
 assert.equal(result.reply_max_messages,2);assert.equal(result.reply_reference,false);
 assert.equal(result.shared_memory,false);
});

test('new task control field is disabled and omitted when the running backend predates it',()=>{
 const {context,inputs,fields}=fixture();
 const settings=Object.fromEntries(fields.filter(k=>k!=='task_control_enabled').map(key=>[key,inputs['qq-control-'+key].type==='checkbox'?false:1]));
 context.payload={settings,status:{running:true},identity:{bot_id:'10000',owner_ids:['20000'],model:'api'}};
 vm.runInContext('render(payload)',context);
 assert.equal(inputs['qq-control-task_control_enabled'].disabled,true);
 const result=vm.runInContext('collect()',context);
 assert.equal(Object.hasOwn(result,'task_control_enabled'),false);
 assert.equal(Object.hasOwn(result,'delivery_mode'),true);
});

test('QQ mutation toggle is disabled and omitted with an older backend',()=>{
 const {context,inputs,fields}=fixture();
 const settings=Object.fromEntries(fields.filter(k=>k!=='memory_management_enabled').map(key=>[key,inputs['qq-control-'+key].type==='checkbox'?false:1]));
 context.payload={settings,status:{running:true},identity:{bot_id:'10000',owner_ids:['20000'],model:'api'}};
 vm.runInContext('render(payload)',context);
 assert.equal(inputs['qq-control-memory_management_enabled'].disabled,true);
 assert.equal(Object.hasOwn(vm.runInContext('collect()',context),'memory_management_enabled'),false);
});

test('QQ delivery controls show actual continuation and storage boundaries',()=>{
 fixture();
 assert.match(html,/\/continue 结果编号/);
 assert.match(html,/每会话最多8份、全局128份/);
 assert.match(html,/id="qq-control-bubble_chars"[^>]*min="100"[^>]*max="1500"/);
 assert.match(html,/id="qq-control-reply_max_messages"[^>]*min="1"[^>]*max="6"/);
});
