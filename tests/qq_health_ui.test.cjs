const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

async function render(result){
 const source=fs.readFileSync('static/js/qq_settings.js','utf8');
 const start=source.indexOf("    $('qq-settings-test').addEventListener");
 const end=source.indexOf("    $('qq-settings-search-sync').addEventListener",start);
 const fields={};let click,operation,feedback;
 const context={api:async()=>({checked_at:0,requires_action:[],...result}),renderService(){},
  $:id=>fields[id]??=(id==='qq-settings-test'?{addEventListener(name,fn){click=fn;}}:{children:[],replaceChildren(){this.children=[];},append(x){this.children.push(x);}}),
  document:{createElement:()=>({children:[],append(...items){this.children.push(...items);}})},
  action:fn=>(operation=fn()),feedback:(message,error)=>{feedback={message,error};}};
 vm.runInNewContext(source.slice(start,end),context);click();await operation;
 return {feedback,cards:fields['qq-health-status'].children};
}

for(const network of ['reachable','unreachable','unknown'])test('health UI independently renders network '+network,async()=>{
 const r=await render({qq_login:'matched',network});
 assert.equal(r.feedback.error,network!=='reachable');
 const row=r.cards.find(x=>x.children[0].textContent==='外网连通性');
 assert.equal(row.children[1].textContent,{reachable:'可达',unreachable:'不可达',unknown:'未知'}[network]);
});
test('reachable internet cannot hide the wrong QQ account',async()=>{
 assert.equal((await render({qq_login:'wrong_account',network:'reachable'})).feedback.error,true);
});
test('older backend without a network field remains readable',async()=>{
 assert.equal((await render({qq_login:'matched'})).feedback.error,false);
});
