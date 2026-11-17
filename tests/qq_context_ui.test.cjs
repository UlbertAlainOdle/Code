const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/js/qq_settings.js','utf8');
const html=fs.readFileSync('static/Chat4o5-Modern.html','utf8');
function fixture(){
 const elements={};
 for(const key of ['owner','source','refresh','select','off','feedback']){
  const id='qq-context-'+key;assert.ok(html.includes(`id="${id}"`));
  elements[id]={value:'',children:[],events:{},replaceChildren(){this.children=[];},appendChild(o){this.children.push(o);},addEventListener(n,fn){this.events[n]=fn;}};
 }
 const calls=[];let reject=false;
 const context={$:id=>elements[id],document:{createElement:()=>({})},Date,encodeURIComponent,
  api:async(path,options)=>{calls.push({path,options});if(options&&reject)throw new Error('conflict');return {owners:['20000'],owner_id:'20000',sessions:[{id:'session_a.jsonl',preview:'<script>fixture</script>'}],revision:'r1',state:{generation:0,enabled:false}};}};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('    let contextState = null;'),source.indexOf('    window.SayaQQSettings =')),context);
 return {elements,calls,reject:()=>{reject=true;}};
}
test('context selection stays off until explicit read and confirmation,uses server revision',async()=>{
 const {elements:e,calls}=fixture();assert.equal(calls.length,0);
 await e['qq-context-refresh'].events.click();assert.equal(calls.length,1);
 assert.ok(e['qq-context-source'].children[0].textContent.includes('<script>fixture</script>'));
 e['qq-context-source'].value='session_a.jsonl';await e['qq-context-select'].events.click();
 const saved=JSON.parse(calls[1].options.body);assert.equal(saved.owner_id,'20000');assert.equal(saved.revision,'r1');assert.equal(saved.generation,0);assert.equal(saved.session_id,'session_a.jsonl');
});
test('conflict preserves chosen draft and shows error',async()=>{
 const {elements:e,calls,reject}=fixture();await e['qq-context-refresh'].events.click();e['qq-context-source'].value='session_a.jsonl';reject();await e['qq-context-select'].events.click();
 assert.equal(e['qq-context-source'].value,'session_a.jsonl');assert.equal(e['qq-context-feedback'].textContent,'conflict');assert.equal(calls.length,2);
});
test('context chooser does not mark unrelated QQ settings draft dirty',()=>{
 const c={form:{addEventListener(event,fn){this.handler=fn;}},epoch:0,dirty:false,feedback(){}};
 vm.createContext(c);
 vm.runInContext(source.split('\n').find(line=>line.includes("form.addEventListener('input'")),c);
 c.form.handler({target:{id:'qq-context-source'}});assert.equal(c.dirty,false);assert.equal(c.epoch,0);
 c.form.handler({target:{id:'qq-control-shared_memory'}});assert.equal(c.dirty,true);assert.equal(c.epoch,1);
});
