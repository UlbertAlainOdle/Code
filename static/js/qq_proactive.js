'use strict';
(() => {
const el=id=>document.getElementById(id);let state=null,busy=false;
const bools=['source_context','source_projects','source_dates','source_dream'];
const numbers=['daily_limit','min_gap_minutes','min_idle_minutes','quiet_start','quiet_end'];
const reasons={disabled:'已关闭',no_candidate:'暂无可用话题',awaiting_reply:'等你有新回复再考虑',quiet_hours:'免打扰时段',recent_interaction:'还未达到聊天空闲时间',daily_limit:'今日已达上限',cooldown:'仍在间隔内',pending:'已有一条待处理',chose_silence:'这次选择安静',ready:'准备就绪',cancelled_or_unavailable:'本次取消或暂不可用'};
function draft(){if(!state)return null;const p={...state.values,owner_id:el('owner').value,enabled:el('enabled').value==='true',timezone:el('timezone').value.trim()};for(const k of bools)p[k]=el(k).checked;for(const k of numbers)p[k]=Number(el(k).value);return p;}
async function api(body){
 const init={method:body?'PUT':'GET',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
 if(body)init.body=JSON.stringify(body);
 let response=await fetch('/api/qq/proactive',init);
 if(response.status===401){await fetch('/api/qq/ui-session',{method:'POST',headers:{'X-Saya-QQ-UI':'1'},credentials:'same-origin'});response=await fetch('/api/qq/proactive',init);}
 if(!response.ok){const v=await response.json();throw Error(v.detail||'操作未完成');}return response.json();
}
function render(value){state=value;const p=value.values;el('owner').replaceChildren();const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择接收账号';el('owner').append(placeholder);
 for(const id of value.owners||[]){const option=document.createElement('option');option.value=id;option.textContent=id;el('owner').append(option);}
 if(p.owner_id)el('owner').value=p.owner_id;el('enabled').value=String(p.enabled);
 for(const k of bools)el(k).checked=p[k];for(const k of numbers)el(k).value=p[k];el('timezone').value=p.timezone;
 el('runtime').textContent='当前：'+(p.enabled?'开启':'关闭')+'；'+(reasons[value.last_reason]||'等待符合条件的话题')+'。';el('save').disabled=false;
}
async function run(fn){if(busy)return;busy=true;el('read').disabled=el('save').disabled=true;
 try{await fn();}catch(e){el('status').textContent=e.message;}finally{busy=false;el('read').disabled=false;el('save').disabled=!state;}}
el('policy').onsubmit=e=>e.preventDefault();
el('read').onclick=()=>run(async()=>{if(state&&JSON.stringify(draft())!==JSON.stringify(state.values))throw Error('请先保存草稿；若要放弃草稿可刷新页面。');render(await api());el('status').textContent='已读取，未修改开关，也未发送消息。';});
el('save').onclick=()=>run(async()=>{if(!state)throw Error('请先读取。');if(!el('policy').reportValidity())return;
 const values={...state.values,owner_id:el('owner').value,enabled:el('enabled').value==='true',timezone:el('timezone').value.trim()};
 for(const k of bools)values[k]=el(k).checked;for(const k of numbers)values[k]=Number(el(k).value);
 const oldOwners=state.owners;const result=await api({owner_id:el('owner').value,revision:state.revision,values,confirmed:true});render({...result,owners:oldOwners});
 el('status').textContent='已保存。'+(values.enabled?'满足空闲/话题/心情等条件后才考虑开口。':'主动互动已关闭。');});
})();
