(()=>{'use strict';
let state=null,busy=false,source='favorites';const $=id=>document.getElementById(id);let blobs=[];
function status(text){$('status').textContent=text;}
async function api(path,body){
 const url='/api/qq/favorites'+path+(path.includes('?')?'&':'?')+'owner_id='+encodeURIComponent($('owner').value)+'&source='+source;
 const init={method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'}};
 if(body)init.body=JSON.stringify(body);
 let response=await fetch(url,init);
 if(response.status===401){await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});response=await fetch(url,init);}
 if(!response.ok){const d=await response.json();throw Error(typeof d.detail==='string'?d.detail:'操作未完成。');}
 return path.includes('/preview')?response.blob():response.json();
}
function draft(){return [...$('items').querySelectorAll('article')].map(el=>({id:el.dataset.id,selected:el.querySelector('input').checked,meaning:el.querySelector('textarea').value}));}
function dirty(){return state&&draft().some((x,i)=>x.selected!==state.items[i].selected||x.meaning!==state.items[i].meaning);}
function body(){if(!state)throw Error('请先读取本机清单。');return{owner_id:$('owner').value,catalogue_revision:state.catalogue_revision,confirmed:true};}
function clear(){blobs.forEach(URL.revokeObjectURL);blobs=[];$('items').replaceChildren();}
function render(data){
 state=data;if(data.owner_id)$('owner').value=data.owner_id;clear();let pack=null;
 for(const item of data.items){
  if(source==='official'&&item.package_id!==pack){pack=item.package_id;const title=document.createElement('h2');title.textContent='官方表情包 '+pack;$('items').append(title);}
  const row=document.createElement('article');row.dataset.id=item.id;
  const preview=document.createElement('div'),image=document.createElement('img');image.alt='尚未加载预览';
  const load=document.createElement('button');load.textContent='预览首帧';
  load.onclick=run(async()=>{const url=URL.createObjectURL(await api('/'+item.id+'/preview'));blobs.push(url);image.src=url;image.alt=item.label;});preview.append(image,load);
  const detail=document.createElement('div'),label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=item.selected;
  label.append(check,document.createTextNode(' 指定 '+item.label));
  const meaning=document.createElement('textarea');meaning.maxLength=1200;meaning.value=item.meaning;meaning.placeholder='含义、情绪、适合语境、应避免的场景';
  const analyze=document.createElement('button');analyze.textContent='分析这张（调用当前视觉模型）';
  analyze.onclick=run(async()=>{if(dirty())throw Error('请先保存当前选择和说明，避免分析覆盖草稿。');if(!item.selected)throw Error('请先勾选这张并保存指定。');render(await api('/analyze?key='+encodeURIComponent(item.id),body()));status('已读取首帧并生成说明，请检查语义；不会自动发送。');});
  detail.append(label,document.createElement('br'),meaning,analyze);row.append(preview,detail);$('items').append(row);
 }
}
function run(fn){return async()=>{if(busy)return;busy=true;document.querySelectorAll('button').forEach(x=>x.disabled=true);try{await fn();}catch(e){status(e.message);}finally{busy=false;document.querySelectorAll('button').forEach(x=>x.disabled=false);}};}
function switchSource(next){return run(async()=>{
 if(dirty())throw Error('请先保存当前来源的草稿，再切换。');source=next;state=null;clear();
 $('source-favorites').setAttribute('aria-pressed',String(source==='favorites'));$('source-official').setAttribute('aria-pressed',String(source==='official'));
 $('source-help').textContent=source==='official'?'官方表情包：请在机器人好友私聊，从QQ已添加的官方包发送要使用的表情，等机器人回复后点“读取近期官方表情”。按包归类，仅收录已发送样本；当前协议不能列出全部已添加包或自动展开整包。':'收藏表情：机器人账号已收藏的单张图片。';
 $('refresh').textContent=source==='official'?'重新扫描近期官方样本':'同步机器人QQ收藏';
 render(await api(''));status(source==='official'?'已读取保存的官方样本；近期扫描不是QQ已添加整包清单。':'已读取上次保存的收藏快照；QQ增删后请点“同步机器人QQ收藏”。');
 });}
$('source-favorites').onclick=switchSource('favorites');$('source-official').onclick=switchSource('official');
$('read').onclick=run(async()=>{if(dirty())throw Error('请先保存草稿，或刷新页面明确放弃草稿。');render(await api(''));status(source==='official'?'已读取保存的官方样本（'+state.items.length+'项）；这不是QQ官方包的当前收藏清单。':'已读取上次保存的清单（'+state.items.length+'项）；未联网同步QQ。QQ增删后请点同步按钮。');});
$('refresh').onclick=run(async()=>{if(!state)render(await api(''));if(dirty())throw Error('请先保存草稿再同步。');const previous=state.items.map(x=>x.id);status(source==='official'?'正在扫描近期官方表情样本……':'正在读取机器人QQ的当前收藏……');render(await api('/refresh',body()));const current=new Set(state.items.map(x=>x.id)),old=new Set(previous),added=[...current].filter(id=>!old.has(id)).length,removed=previous.filter(id=>!current.has(id)).length;status(source==='official'?'已核对最近主人好友私聊；这是样本清单，不代表官方包的新增或删除。':'已同步QQ当前收藏：'+state.items.length+'项，新增'+added+'项，移除'+removed+'项；未改动QQ收藏。');});
$('save').onclick=run(async()=>{render(await api('/select',{...body(),choices:draft()}));status(source==='official'?'官方样本指定与说明已保存；当前自动发送使用单张收藏来源。':'指定与说明已保存；启用自动/收藏模式后，回复只从已指定且有说明的表情选择。');});
let expressionState=null;
async function expressionApi(body){
 const path='/api/qq/expression-policy'+(body?'':'?owner_id='+encodeURIComponent($('owner').value));
 const init={method:body?'PUT':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
 if(body){init.headers.Origin=location.origin;init.body=JSON.stringify(body);}
 let res=await fetch(path,init);
 if(res.status===401){await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});res=await fetch(path,init);}
 if(!res.ok){const value=await res.json();throw Error(value.detail||'表达模式操作未完成。');}
 return res.json();
}
function showExpression(data){expressionState=data;$('expression-mode').value=data.mode;$('expression-status').textContent=data.activated?'当前模式：'+data.mode+'；按心情与语义选择；语音可配颜文字，与大表情互斥。':'尚未启用新策略，请选择模式并保存。';if(data.owner_id&&!$('owner').value)$('owner').value=data.owner_id;}
if($('expression-read')){
 $('expression-read').onclick=run(async()=>showExpression(await expressionApi()));
 $('expression-save').onclick=run(async()=>{const mode=$('expression-mode').value;if(!expressionState)expressionState=await expressionApi();showExpression(await expressionApi({owner_id:$('owner').value||expressionState.owner_id,expression_revision:expressionState.expression_revision,mode,confirmed:true}));});
}
$('owner').oninput=()=>{state=null;expressionState=null;status('账号已变化，请重新读取；当前草稿未自动提交。');};
})();
