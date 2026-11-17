const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.value='';this.checked=false;this.textContent='';this.attributes={};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(k,v){this.attributes[k]=v;}
 querySelectorAll(selector){return this.children.flatMap(x=>x instanceof Element?[...(x.tag===selector?[x]:[]),...x.querySelectorAll(selector)]:[]);}
 querySelector(selector){return this.querySelectorAll(selector)[0];}
}
test('parallel sources preserve drafts and route actions by source',async()=>{
 const ids=['owner','status','items','read','refresh','save','source-help','source-favorites','source-official'];
 const nodes=Object.fromEntries(ids.map(x=>[x,new Element(x==='items'?'main':'button')]));
 const catalogues={favorites:{catalogue_revision:1,owner_id:'20000',items:[{id:'f',label:'favorite',selected:false,meaning:'',analyzed:false}]},official:{catalogue_revision:0,owner_id:'20000',items:[{id:'o',package_id:'123',label:'official',selected:false,meaning:'',analyzed:false}]}};
 const calls=[];const fetch=async(url,init)=>{calls.push(url);const source=new URL(url,'http://127.0.0.1').searchParams.get('source');assert.ok(source in catalogues);let data=catalogues[source];
  if(init.method==='POST'){const body=JSON.parse(init.body);data={...data,catalogue_revision:data.catalogue_revision+1,items:body.choices};catalogues[source]=data;}
  return{ok:true,status:200,json:async()=>structuredClone(data)};};
 const document={getElementById:id=>nodes[id],createElement:tag=>new Element(tag),createTextNode:text=>({text}),querySelectorAll:()=>Object.values(nodes).filter(x=>x.tag==='button')};
 vm.runInNewContext(fs.readFileSync('static/js/qq_favorites.js','utf8'),{document,fetch,URL,location:{origin:'http://127.0.0.1'}});
 await nodes.read.onclick();nodes.items.querySelector('input').checked=true;
 const previousCalls=calls.length;await nodes['source-official'].onclick();
 assert.match(nodes.status.textContent,/保存/);assert.equal(calls.length,previousCalls);
 await nodes.save.onclick();await nodes['source-official'].onclick();
 assert.ok(calls.at(-1).includes('source=official'));
 assert.equal(nodes.items.querySelector('h2').textContent,'官方表情包 123');
 assert.match(nodes['source-help'].textContent,/不能列出全部/);
 assert.equal(nodes.items.querySelector('input').checked,false);
 await nodes.read.onclick();assert.match(nodes.status.textContent,/不是QQ官方包/);
 await nodes['source-favorites'].onclick();assert.equal(nodes.items.querySelector('input').checked,true);
});

test('saved read stays offline; explicit QQ sync updates additions/removals and protects drafts',async()=>{
 const ids=['owner','status','items','read','refresh','save','source-help','source-favorites','source-official'];
 const nodes=Object.fromEntries(ids.map(x=>[x,new Element(x==='items'?'main':'button')]));let syncs=0;
 let data={catalogue_revision:1,owner_id:'20000',items:[{id:'old',label:'old',selected:false,meaning:'',analyzed:false}]};
 const fetch=async(url,init)=>{if(url.includes('/refresh')){syncs++;assert.equal(init.method,'POST');data={...data,catalogue_revision:2,items:[{id:'new',label:'new',selected:false,meaning:'',analyzed:false}]};}return{ok:true,status:200,json:async()=>structuredClone(data)};};
 const document={getElementById:id=>nodes[id],createElement:tag=>new Element(tag),createTextNode:text=>({text}),querySelectorAll:()=>Object.values(nodes).filter(x=>x.tag==='button')};
 vm.runInNewContext(fs.readFileSync('static/js/qq_favorites.js','utf8'),{document,fetch,URL});
 await nodes.read.onclick();assert.equal(syncs,0);assert.match(nodes.status.textContent,/未联网/);
 nodes.items.querySelector('input').checked=true;await nodes.refresh.onclick();assert.equal(syncs,0);assert.match(nodes.status.textContent,/保存/);
 nodes.items.querySelector('input').checked=false;await nodes.refresh.onclick();assert.equal(syncs,1);
 assert.equal(nodes.items.querySelector('article').dataset.id,'new');assert.match(nodes.status.textContent,/新增1项，移除1项/);
});

test('expression mode has independent CAS and never overwrites favorite drafts',async()=>{
 const ids=['owner','status','items','read','refresh','save','source-help','source-favorites','source-official','expression-mode','expression-read','expression-save','expression-status'];
 const nodes=Object.fromEntries(ids.map(x=>[x,new Element(x==='items'?'main':'button')]));let revision=0;const writes=[];
 const catalogue={catalogue_revision:8,owner_id:'20000',items:[{id:'a',label:'favorite',selected:true,meaning:'original',analyzed:true}]};
 const fetch=async(url,init)=>{
  if(url.startsWith('/api/qq/expression-policy')){
   if(init.method==='PUT'){const body=JSON.parse(init.body);writes.push(body);assert.equal(body.expression_revision,revision);assert.equal(body.confirmed,true);assert.equal(body.mode,'auto');revision++;}
   return {ok:true,status:200,json:async()=>({expression_revision:revision,mode:revision?'auto':'off',activated:!!revision,owner_id:'20000'})};
  }
  return {ok:true,status:200,json:async()=>structuredClone(catalogue)};
 };
 const document={getElementById:id=>nodes[id],createElement:tag=>new Element(tag),createTextNode:text=>({text}),querySelectorAll:()=>Object.values(nodes).filter(x=>x.tag==='button')};
 vm.runInNewContext(fs.readFileSync('static/js/qq_favorites.js','utf8'),{document,fetch,URL,location:{origin:'http://127.0.0.1:8000'}});
 await nodes.read.onclick();nodes.items.querySelector('textarea').value='unsaved private meaning';
 nodes['expression-mode'].value='auto';await nodes['expression-save'].onclick();
 assert.equal(writes.length,1);assert.equal(nodes.items.querySelector('textarea').value,'unsaved private meaning');
 assert.match(nodes['expression-status'].textContent,/auto/);
 await nodes['source-official'].onclick();assert.match(nodes.status.textContent,/草稿/);
});
