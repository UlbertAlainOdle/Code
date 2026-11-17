'use strict';
(() => {
 const el=id=>document.getElementById(id);let state=null,busy=false;
 const draft=()=>({enabled:el('enabled').checked,concise_replies:el('concise_replies').checked,short_voice:el('short_voice').checked});
 async function api(body){
  const options={method:body?'PUT':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
  if(body)options.body=JSON.stringify(body);
  let response=await fetch('/api/qq/dialogue-experience',options);
  if(response.status===401){const auth=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});if(!auth.ok)throw Error('本机设置会话建立失败');response=await fetch('/api/qq/dialogue-experience',options);}
  const result=await response.json();if(!response.ok)throw Error(result.detail||'操作未完成');return result;
 }
 function render(value){const owner=el('owner').value,owners=value.owners||state?.owners||[];state={...value,owners,values:{enabled:value.values.enabled,concise_replies:value.values.concise_replies!==false,short_voice:value.values.short_voice!==false}};el('owner').replaceChildren();
  for(const id of ['',...owners]){const option=document.createElement('option');option.value=id;option.textContent=id||'请选择主人账号';el('owner').append(option);}el('owner').value=owners.includes(owner)?owner:'';
  for(const key of ['enabled','concise_replies','short_voice'])el(key).checked=state.values[key];
  if(value.diagnostics)el('diagnostics').textContent=JSON.stringify(value.diagnostics,null,2);
 }
 async function run(action){if(busy)return;busy=true;el('read').disabled=el('save').disabled=true;try{await action();}catch(error){el('status').textContent=error.message;}finally{busy=false;el('read').disabled=false;el('save').disabled=!state;}}
 el('read').onclick=()=>run(async()=>{if(state&&JSON.stringify(draft())!==JSON.stringify(state.values))throw Error('存在未保存草稿；请先保存，或刷新页面放弃草稿。');render(await api());el('status').textContent='已读取，未改变开关或发送消息。';});
 el('policy').onsubmit=event=>{event.preventDefault();run(async()=>{if(!state||!el('policy').reportValidity())return;render(await api({owner_id:el('owner').value,revision:state.revision,confirmed:true,values:draft()}));el('status').textContent='已保存。人物和原有设置保留。';});};
})();
