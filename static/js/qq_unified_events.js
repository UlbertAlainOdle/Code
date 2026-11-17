(() => {
 'use strict';
 const el=id=>document.getElementById(id);let snapshot=null,busy=false;
 async function request(method='GET',body){
  const opts={method,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
  if(body)opts.body=JSON.stringify(body);
  let response=await fetch('/api/qq/unified-events',opts);
  if(response.status===401){await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});response=await fetch('/api/qq/unified-events',opts);}
  const result=await response.json();if(!response.ok)throw Error(result.detail||'统一事件操作失败');return result;
 }
 async function run(action){if(busy)return;busy=true;el('unified-read').disabled=el('unified-save').disabled=true;
  try{await action();}catch(e){el('unified-status').textContent=e.message;}
  finally{busy=false;el('unified-read').disabled=false;el('unified-save').disabled=!snapshot;}}
 el('unified-read').onclick=()=>run(async()=>{
  if(snapshot && (el('unified-enabled').checked!==snapshot.policy.values.enabled||el('unified-topics').checked!==!!snapshot.policy.values.topic_routing_enabled||el('unified-level').value!==snapshot.policy.values.minimum_level||+el('unified-active').value!==snapshot.policy.values.group_active||+el('unified-total').value!==snapshot.policy.values.group_total))throw Error('存在未保存草稿，请先保存或刷新页面放弃草稿。');
  snapshot=await request();const v=snapshot.policy.values;
  el('unified-enabled').checked=v.enabled;el('unified-level').value=v.minimum_level;
  el('unified-topics').checked=!!v.topic_routing_enabled;
  el('unified-active').value=v.group_active;el('unified-total').value=v.group_total;
  el('unified-status').textContent=`个人卡 ${snapshot.preview.personal}；保留旧公共/未知 ${snapshot.preview.legacy_retained}。\n`+snapshot.groups.map(x=>`${x.group} · ${x.id} v${x.revision}\n${x.summary}`).join('\n\n')+(snapshot.more?'\n仅展示前100项。':'');
 });
 el('unified-save').onclick=()=>run(async()=>{
  if(!snapshot||!el('owner').value)throw Error('先读取并选择主人账号。');
  for(const id of ['unified-active','unified-total'])if(!el(id).reportValidity())return;
  const values={...snapshot.policy.values,enabled:el('unified-enabled').checked,topic_routing_enabled:el('unified-topics').checked,minimum_level:el('unified-level').value,group_active:+el('unified-active').value,group_total:+el('unified-total').value};
  if(values.enabled&&!snapshot.policy.values.enabled&&!confirm('启用后本人可在授权群主动查询自己的个人事件，群公共事件可由具资格参与者跨场合更新。保留原停止记录与期限，先备份。确认当前预览？'))return;
  await request('PUT',{owner_id:el('owner').value,revision:snapshot.policy.revision,values,confirmed:true,expected_version:snapshot.preview.version});
  snapshot=null;el('unified-status').textContent='已保存。请重新读取核对；关系学习和原启动方式保持。';
 });
})();
