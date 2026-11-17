'use strict';
(() => {
 const el=id=>document.getElementById(id);let state=null,busy=false;
 const draft=()=>({enabled:el('enabled').checked,timezone:el('timezone').value.trim(),history_token_budget:Number(el('history_token_budget').value),old_fraction:Number(el('old_fraction').value)});
 async function api(body){
  const options={method:body?'PUT':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
  if(body)options.body=JSON.stringify(body);
  let response=await fetch('/api/qq/context-policy',options);
  if(response.status===401){await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});response=await fetch('/api/qq/context-policy',options);}
  const result=await response.json();if(!response.ok)throw Error(result.detail||'操作未完成');return result;
 }
 function render(value){state=value;const owner=el('owner').value;el('owner').replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择主人账号';el('owner').append(placeholder);
  for(const id of value.owners||[]){const option=document.createElement('option');option.value=id;option.textContent=id;el('owner').append(option);}
  el('owner').value=(value.owners||[]).includes(owner)?owner:'';
  for(const [key,val] of Object.entries(value.values)){if(key==='enabled')el(key).checked=val;else el(key).value=String(val);}
 }
 async function run(action){if(busy)return;busy=true;el('read').disabled=el('save').disabled=true;
  try{await action();}catch(error){el('status').textContent=error.message;}finally{busy=false;el('read').disabled=false;el('save').disabled=!state;}
 }
 el('read').onclick=()=>run(async()=>{
  if(state&&JSON.stringify(draft())!==JSON.stringify(state.values))throw Error('存在未保存草稿，请先保存；若需放弃请刷新页面。');
  render(await api());el('status').textContent='已读取。未修改开关或发送消息。';
 });
 el('policy').onsubmit=event=>{event.preventDefault();run(async()=>{
  if(!state||!el('policy').reportValidity())return;
  render(await api({owner_id:el('owner').value,revision:state.revision,confirmed:true,values:draft()}));el('status').textContent='设置已保存，原历史和长期记忆保留。';
 });};
 let eventPreview=null;
 async function eventApi(path,body){
  const options={method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
  if(body)options.body=JSON.stringify(body);
  let response=await fetch(path,options);
  if(response.status===401){await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});response=await fetch(path,options);}
  const value=await response.json();if(!response.ok)throw Error(value.detail||'事件操作未完成');return value;
 }
 el('events-read').onclick=async()=>{
  el('events-read').disabled=true;
  try{
   const preview=await eventApi('/api/qq/person-events/migration');
   const page=await eventApi('/api/qq/person-events?subject='+encodeURIComponent(el('events-subject').value.trim()));
   eventPreview=preview;el('events-migrate').disabled=preview.already_migrated||!preview.can_apply;
   el('events-status').textContent=`事件${page.enabled?'开启':'关闭'}；身份${page.identity_migrated?'已迁移':'待迁移'}。待迁移 ${preview.entries.filter(x=>x.action==='migrate').length} 条；保留旧公共/未知 ${preview.entries.filter(x=>x.action==='keep_legacy').length} 条。\n`
    +page.items.map(x=>`归属：${x.owner_display||'QQ:'+x.subject} · 原话标题：${x.title} · ${x.id} v${x.revision} · ${x.state} · 重要度${x.importance||'normal'}${x.importance_locked?'（手动）':x.importance_rule_version===1?'（自动规则v1）':''} · 原因${x.state_reason||'evidence_or_time'} · ${Object.entries(x.timing||{}).map(([kind,v])=>`${kind==='deadline'?'截止':'安排'}：${v.local||''} ${v.status}`).join('；')} · ${x.capacity.max_active}/${x.capacity.max_total} · ${x.visible_sources.join(', ')}`).join('\n')
    +(page.next_offset===null?'':'\n有下一页，可用主人私聊 /events all 分页位置 查看。');
  }catch(error){el('events-status').textContent=error.message;}finally{el('events-read').disabled=false;}
 };
 el('events-migrate').onclick=async()=>{
  if(!eventPreview||!el('owner').value){el('events-status').textContent='先读取对话时效设置并选择主人账号，再确认迁移。';return;}
  if(!confirm('按当前预览迁移可信单主体事件，混合公共卡保留。旧确认与待发快照失效；原事件开关保持，先备份。是否确认？'))return;
  el('events-migrate').disabled=true;
  try{const result=await eventApi('/api/qq/person-events/migration',{owner_id:el('owner').value,expected_version:eventPreview.version,confirmed:true});
   eventPreview=null;el('events-status').textContent=`已迁移 ${result.migrated} 条；事件仍${result.enabled?'开启':'关闭'}。请重新读取核对。`;
  }catch(error){el('events-status').textContent=error.message+'；重新读取预览后再确认。';}
 };
})();
