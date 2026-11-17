/* Explicit legacy-cache capture, separate from the local runtime preference store. */
(() => {
    'use strict';
    function capture(storage,entries,view) {
        const values={};
        for(const [key,item] of Object.entries(entries)) {
            if(item.synthetic)continue;
            const raw=storage.getItem(key);if(raw!==null)values[key]=raw;
        }
        if(view)values.saya_3d_live_view_snapshot_v1=JSON.stringify(view);
        return values;
    }
    if(typeof module!=='undefined')module.exports={capture};
    if(typeof window==='undefined')return;
    const ready=window.SayaWebWindow?.whenReady || (fn=>document.addEventListener('DOMContentLoaded',fn));
    let catalog=null,status=null,preview=null,pending=null,busy=false;
    const $=id=>document.getElementById(id);
    function message(text){$('browser-archive-feedback').textContent=text;}
    async function api(path='',options={}) {
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
        try {
            const init={...options,signal:controller.signal,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
            let response=await fetch('/api/qq/browser-settings'+path,init);
            if(response.status===401) {
                const session=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'X-Saya-QQ-UI':'1'}});
                if(!session.ok)throw new Error('请在本机聊天页面打开设置存档。');
                response=await fetch('/api/qq/browser-settings'+path,init);
            }
            const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'存档操作失败。');return data;
        }finally{clearTimeout(timer);}
    }
    async function action(fn) {
        if(busy)return;busy=true;
        try{await fn();}catch(error){message(error.name==='AbortError'?'操作超时，请重新读取本机存档核对。':error.message);}
        finally{busy=false;}
    }
    function collect(){return capture(localStorage,catalog.entries,
        window.SayaBrowserViewReady && !window.SayaBrowserViewReady()?null:window.SayaBrowserViewSnapshot?.());}
    function invalidate(){preview=null;pending=null;$('browser-archive-save').disabled=true;}
    async function refresh() {
        status=await api();invalidate();
        $('browser-archive-status').textContent=`本机已存 ${status.saved_count} 项，保留 ${status.history_count} 份历史；${status.saved_at?'上次 '+new Date(status.saved_at).toLocaleString():'尚未建立存档'}。${status.runtime_applied?'统一读取已开启；登记设置从本地文件加载，浏览器原缓存保留。':'统一读取未开启；当前沿用浏览器偏好。'}`;
        if($('browser-runtime-enable'))$('browser-runtime-enable').disabled=status.runtime_applied||!status.saved_count;
        if($('browser-runtime-disable'))$('browser-runtime-disable').disabled=!status.runtime_applied;
    }
    ready(()=>{
        if(!$('browser-archive-preview'))return;
        for(const [id,enabled] of [['browser-runtime-enable',true],['browser-runtime-disable',false]]){
            const button=$(id);if(button)button.onclick=()=>action(async()=>{
                if(!status)await refresh();
                await window.SayaSettingsStore.setEnabled(enabled,status.revision);
                message('设置来源已保存；正在重新加载页面。浏览器原缓存保留。');window.location.reload();
            });
        }
        if($('browser-runtime-flush'))$('browser-runtime-flush').onclick=()=>action(async()=>{
            await window.SayaSettingsStore.flush();await refresh();message('当前修改已保存到本机文件。');
        });
        window.addEventListener?.('saya-local-settings-state',event=>{
            if(status&&!preview&&!busy&&event.detail?.revision&&event.detail.revision!==status.revision)void action(refresh);
        });
        $('browser-archive-refresh').onclick=()=>action(async()=>{await refresh();message('已读取本机存档版本，浏览器缓存未改变。');});
        $('browser-archive-preview').onclick=()=>action(async()=>{
            if(!catalog){const response=await fetch('/static/browser_settings_inventory.json',{cache:'no-store'});if(!response.ok)throw new Error('设置清单无法读取。');catalog=await response.json();}
            if(!status)await refresh();
            invalidate();
            const captured=collect();const result=await api('/preview',{method:'POST',body:JSON.stringify({values:captured,revision:status.revision})});
            pending=captured;preview=result;
            const container=$('browser-archive-diff');container.replaceChildren();
            const labels={add:'新增',same:'相同',conflict:'不同，需允许覆盖'};
            for(const item of preview.changes){const row=document.createElement('p');row.textContent=`${item.category} · ${item.label}：${labels[item.action]}`;container.append(row);}
            const unknown=[];
            for(let i=0;i<localStorage.length;i++) {
                const key=localStorage.key(i);
                if(!(key in catalog.entries)&&!(key in catalog.excluded)&&!catalog.excluded_prefixes.some(prefix=>key.startsWith(prefix)))unknown.push(key);
            }
            $('browser-archive-save').disabled=!preview.changes.length;
            message(`当前可存档 ${preview.changes.length} 项；本浏览器缺失的 ${preview.retained_count} 项旧存档会保留。${unknown.length?'发现未登记缓存键（不读取其值）：'+unknown.join('、'):'已核对全部缓存键；API缓存由上方API配置入口管理。'}`);
        });
        $('browser-archive-save').onclick=()=>action(async()=>{
            if(!preview||!pending)throw new Error('请先预览当前浏览器设置。');
            // Capture was frozen for review. No cache writes occur here.
            const result=await api('',{method:'PUT',body:JSON.stringify({values:pending,revision:preview.revision,
                overwrite:$('browser-archive-overwrite').checked,source_label:$('browser-archive-label').value})});
            status=result;invalidate();await refresh();message('浏览器原缓存已明确导入本机文件，缓存和API配置均保留。若统一读取已开启，请刷新应用新版本。');
        });
        void action(refresh);
    });
})();
