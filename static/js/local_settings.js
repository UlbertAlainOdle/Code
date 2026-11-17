/* Registered preferences use memory backed by the versioned local file. Native
 * browser storage is kept intact for explicit imports and API drafts only. */
(() => {
    'use strict';
    function createStore({native,request,notify=()=>{},schedule=setTimeout,cancel=clearTimeout}) {
        let entries={},values={},revision='',enabled=false,initialized=false,error=null,stale=false,inflight=null,timer=null;
        const pending=new Map();
        const state=()=>({initialized,enabled,pending:pending.size,saving:!!inflight,error:error?.message||'',stale,revision});
        const emit=()=>notify(state());
        const accept=data=>{entries=data.entries;values={...data.values};revision=data.revision;enabled=data.runtime_applied;initialized=true;error=null;stale=false;emit();};
        function write(key,value) {
            if(!initialized)throw new Error('本地设置尚未读取。');
            if(!enabled||!Object.hasOwn(entries,key))return value===null?native.removeItem(key):native.setItem(key,String(value));
            if(stale)throw new Error('本机配置已改变，请先核对未保存草稿并刷新。');
            const next=value===null?null:String(value);
            if((Object.hasOwn(values,key)?values[key]:null)===next)return;
            if(next===null)delete values[key];else values[key]=next;
            if(entries[key].authority==='existing_server')return; // already persisted by its own API
            pending.set(key,next);cancel(timer);timer=schedule(()=>{if(!error)void flush().catch(()=>{});},500);emit();
        }
        const storage={
            getItem(key){
                if(!initialized)throw new Error('本地设置尚未读取。');
                return enabled&&Object.hasOwn(entries,key)?(Object.hasOwn(values,key)?values[key]:null):native.getItem(key);
            },
            setItem:(key,value)=>write(key,String(value)),removeItem:key=>write(key,null)
        };
        async function flush() {
            cancel(timer);
            if(stale||error)throw error||new Error('本机配置已更新，请刷新。');
            if(inflight){await inflight;return flush();}
            if(!enabled||!pending.size)return;
            inflight=(async()=>{
                while(pending.size) {
                    const batch=new Map(pending),patch={},clear=[];
                    for(const [key,value] of batch)if(value===null)clear.push(key);else patch[key]=value;
                    try {
                        const data=await request('/runtime',{method:'PUT',body:JSON.stringify({revision,values:patch,clear_fields:clear})});
                        revision=data.revision;
                        for(const [key,value] of batch)if(pending.get(key)===value)pending.delete(key);
                    } catch(caught){error=caught;stale=caught.status===409;throw caught;}
                }
            })();emit();
            try{await inflight;}finally{inflight=null;emit();}
        }
        async function check() {
            if(!initialized||inflight)return;
            const data=await request();
            if(data.runtime_applied!==enabled||data.revision!==revision){stale=true;error=new Error('本机配置已由其他入口修改；未覆盖新版本，请核对草稿后刷新页面。');emit();throw error;}
            emit();
        }
        async function retry() {
            if(inflight)return inflight;
            const remote=await request('/runtime');
            if(remote.runtime_applied!==enabled){stale=true;error=new Error('设置来源已切换，请刷新页面。');emit();throw error;}
            if(remote.revision!==revision) {
                const allApplied=pending.size&&[...pending].every(([key,value])=>(Object.hasOwn(remote.values,key)?remote.values[key]:null)===value);
                if(!allApplied){stale=true;error=new Error('检测到新版本，未自动覆盖；请导出未保存草稿后刷新核对。');emit();throw error;}
                // A response may have been lost after a successful atomic commit.
                pending.clear();revision=remote.revision;stale=true;
                error=new Error('已核对修改保存成功；为同步其他配置，请刷新后继续。');emit();return;
            }
            error=null;stale=false;emit();await flush();
        }
        return {storage,state,accept,flush,check,retry,
            discard(){if(inflight)throw new Error('请等待正在保存的操作结束。');cancel(timer);pending.clear();error=null;stale=false;emit();},
            project(key,value){if(entries[key]?.authority!=='existing_server')throw new Error('invalid projection');values[key]=JSON.stringify(value);},
            draft:()=>Object.fromEntries(pending),
            async beforeChat(){await flush();await check();}};
    }
    function fitFrame(frame,viewport,bounds={}) {
        const number=(value,fallback)=>Number.isFinite(value)?value:fallback;
        const vw=Math.max(1,number(viewport.width,1)),vh=Math.max(1,number(viewport.height,1));
        const left=Math.min(Math.max(0,number(bounds.left,0)),Math.max(0,vw-48));
        const top=Math.min(Math.max(0,number(bounds.top,52)),Math.max(0,vh-48));
        const maxW=Math.max(1,vw-left-8),maxH=Math.max(1,vh-top-8);
        const width=Math.min(maxW,Math.max(Math.min(160,maxW),number(frame?.width,300)));
        const height=Math.min(maxH,Math.max(Math.min(120,maxH),number(frame?.height,350)));
        return {width,height,left:Math.min(Math.max(left,vw-width-8),Math.max(left,number(frame?.left,left))),
            top:Math.min(Math.max(top,vh-height-8),Math.max(top,number(frame?.top,top)))};
    }
    if(typeof module!=='undefined')module.exports={createStore,fitFrame};
    if(typeof window==='undefined')return;
    let store=null,initializing=null,viewDirty=false,viewTimer=null;
    const native=window.localStorage;
    async function request(path='',options={}) {
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
        const init={...options,credentials:'same-origin',cache:'no-store',signal:controller.signal,
            headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1'}};
        try {
            let response=await fetch('/api/qq/browser-settings'+path,init);
            if(response.status===401) {
                const session=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'X-Saya-QQ-UI':'1'}});
                if(!session.ok)throw new Error('本地设置需要本机同源访问。');
                response=await fetch('/api/qq/browser-settings'+path,init);
            }
            const data=await response.json();
            if(!response.ok){const error=new Error(typeof data.detail==='string'?data.detail:'本地设置操作失败。');error.status=response.status;throw error;}
            return data;
        } finally{clearTimeout(timer);}
    }
    function render(state) {
        let bar=document.getElementById('local-settings-indicator');
        if(!bar){
            bar=document.createElement('div');bar.id='local-settings-indicator';bar.setAttribute('role','status');
            const text=document.createElement('span');text.dataset.localSettingsText='';bar.append(text);
            const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=fn;bar.append(b);return b;};
            button('重试保存',()=>void store.retry().catch(()=>{})).dataset.localSettingsRetry='';
            button('导出未保存草稿',()=>{
                const url=URL.createObjectURL(new Blob([JSON.stringify({values:store.draft()},null,2)],{type:'application/json'}));
                const a=document.createElement('a');a.href=url;a.download='saya-unsaved-settings.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
            }).dataset.localSettingsExport='';
            const reload=button('刷新读取',()=>{
                if(store.state().pending&&!reload.dataset.confirm){reload.dataset.confirm='1';reload.textContent='确认舍弃草稿并刷新';return;}
                store.discard();window.location.reload();
            });reload.dataset.localSettingsReload='';document.body.append(bar);
        }
        bar.hidden=!state.enabled&&!state.error;
        bar.dataset.problem=state.error||state.stale?'true':'false';
        bar.querySelector('[data-local-settings-text]').textContent=state.error?`本地设置未同步：${state.error}`:state.saving||state.pending?`本地设置：${state.pending}项正在保存，关闭前请等待…`:'本地设置已同步';
        for(const name of ['retry','export','reload'])bar.querySelector(`[data-local-settings-${name}]`).hidden=!state.error;
        window.dispatchEvent(new CustomEvent('saya-local-settings-state',{detail:state}));
    }
    async function initialize() {
        if(initializing)return initializing;
        initializing=(async()=>{
            store=createStore({native,request,notify:render});store.accept(await request('/runtime'));
            if(store.state().enabled){
                for(const [key,url,field] of [['saya_user_profile_v1','/api/user-profile','profile'],['saya_style_settings_v1','/api/saya-style/settings','settings']]){
                    const response=await fetch(url,{cache:'no-store'}),data=await response.json();
                    if(!response.ok||!data[field])throw new Error('服务端个人资料/风格无法读取，未使用旧缓存启动。');
                    store.project(key,data[field]);
                }
            }
            setInterval(()=>{if(!window.SayaWebWindow||window.SayaWebWindow.active())void store.check().catch(error=>{
                if(!store.state().stale)render({...store.state(),error:'无法核对本机版本，请恢复连接。'});
            });},5000);
            window.addEventListener('focus',()=>{if(!window.SayaWebWindow||window.SayaWebWindow.active())void store.check().catch(()=>{});});
            window.addEventListener('beforeunload',event=>{if(store.state().pending){event.preventDefault();event.returnValue='';}});
            return store.state();
        })();return initializing;
    }
    function captureView(){
        if(!viewDirty||!store?.state().enabled||!window.SayaBrowserViewReady?.())return;
        const snapshot=window.SayaBrowserViewSnapshot?.();if(!snapshot)return;
        store.storage.setItem('saya_3d_live_view_snapshot_v1',JSON.stringify(snapshot));viewDirty=false;
    }
    window.SayaSettingsStore={initialize,fitFrame,
        get storage(){if(!store)throw new Error('本地设置尚未读取。');return store.storage;},
        state:()=>store?.state(),request,
        async flush(){captureView();await store?.flush();},
        async beforeChat(){captureView();await store?.beforeChat();},
        check:()=>store?.check(),
        markViewDirty(){if(!store?.state().enabled)return;viewDirty=true;clearTimeout(viewTimer);viewTimer=setTimeout(captureView,200);},
        viewReady(){if(viewDirty)captureView();},
        async setEnabled(enabled,revision){await store.flush();const data=await request('/runtime',{method:'PUT',body:JSON.stringify({revision:revision||store.state().revision,enabled})});return data;}
    };
})();
