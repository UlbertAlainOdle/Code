/* Local API configuration wins at runtime; browser drafts change only explicitly. */
(() => {
    'use strict';
    const apiKey = 'saya_api_settings_v1', otherKey = 'saya_other_api_settings_v1';
    const apiFields = ['api_key','api_url','api_model_name','multimodal_model_name','strong_logic_model_name',
        'autonomous_memory_model_name','memory_strong_model_name','brave_search_api_key','deepseek_search_api_key'];
    const otherFields = ['translation_secret_id','translation_secret_key','translation_region','translation_endpoint',
        'tts_api_url','tts_api_key','tts_voice_id','tts_model_id'];
    const names = {api_key:'模型 API Key',api_url:'模型 Base URL',api_model_name:'聊天模型',multimodal_model_name:'图片模型',
        strong_logic_model_name:'强逻辑模型',autonomous_memory_model_name:'自主记忆模型',memory_strong_model_name:'记忆模型',
        brave_search_api_key:'Brave 搜索密钥',deepseek_search_api_key:'DeepSeek 搜索密钥',translation_secret_id:'翻译 Secret ID',translation_secret_key:'翻译 Secret Key',
        translation_region:'翻译区域',translation_endpoint:'翻译端点',tts_api_url:'语音 Base URL',tts_api_key:'语音密钥',tts_voice_id:'语音 Voice ID',tts_model_id:'语音模型'};
    const parse = raw => { const value = JSON.parse(raw || '{}'); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('浏览器缓存格式有误，未覆盖任何配置。'); return value; };
    const lastBackupKey='saya_api_cache_last_backup_v1';
    function searchProviderView(profile){
        const provider=profile?.search_provider||'brave',field=provider+'_search_api_key';
        return {provider,label:provider==='deepseek'?'DeepSeek':'Brave',configured:!!(profile?.settings?.[field]||profile?.configured?.[field])};
    }
    function mergeDraft(draft, profile, fields, strictLocal=false) {
        if (!profile?.prefer_local) return {...draft};
        return {...(strictLocal?{}:draft), ...Object.fromEntries(fields.filter(k => Object.hasOwn(profile.settings,k)).map(k=>[k,profile.settings[k]]))};
    }
    function syncBrowser(storage, settings, now = Date.now()) {
        const originalApi = storage.getItem(apiKey), originalOther = storage.getItem(otherKey);
        const api = parse(originalApi), other = parse(originalOther);
        const backup = `saya_api_cache_backup_${now}_${Math.random().toString(36).slice(2)}`;
        // Backup must succeed before either original cache is changed.
        storage.setItem(backup,JSON.stringify({api:originalApi,other:originalOther,created:now}));
        try {
            storage.setItem(apiKey,JSON.stringify({...api,...Object.fromEntries(apiFields.filter(k=>Object.hasOwn(settings,k)).map(k=>[k,settings[k]]))}));
            storage.setItem(otherKey,JSON.stringify({...other,...Object.fromEntries(otherFields.filter(k=>Object.hasOwn(settings,k)).map(k=>[k,settings[k]]))}));
            storage.setItem(lastBackupKey,backup);
        } catch (error) {
            try { originalApi === null ? storage.removeItem(apiKey) : storage.setItem(apiKey,originalApi);
                originalOther === null ? storage.removeItem(otherKey) : storage.setItem(otherKey,originalOther); } catch (_) { /* Backup remains available. */ }
            throw new Error('浏览器同步失败；已尝试恢复原缓存，完整原值仍在同步前备份中。');
        }
        return backup;
    }
    function restoreBrowser(storage,backup,now=Date.now()) {
        const saved=parse(storage.getItem(backup));
        if(!Object.hasOwn(saved,'api') || !Object.hasOwn(saved,'other') ||
            ![saved.api,saved.other].every(v=>v===null || typeof v==='string'))throw new Error('浏览器备份无效，未更改缓存。');
        const currentApi=storage.getItem(apiKey),currentOther=storage.getItem(otherKey);
        const undo=`saya_api_cache_backup_${now}_${Math.random().toString(36).slice(2)}`;
        storage.setItem(undo,JSON.stringify({api:currentApi,other:currentOther,created:now}));
        try {
            saved.api===null ? storage.removeItem(apiKey):storage.setItem(apiKey,saved.api);
            saved.other===null ? storage.removeItem(otherKey):storage.setItem(otherKey,saved.other);
            storage.setItem(lastBackupKey,undo);
        } catch (_) {
            try { currentApi===null?storage.removeItem(apiKey):storage.setItem(apiKey,currentApi);
                currentOther===null?storage.removeItem(otherKey):storage.setItem(otherKey,currentOther); } catch (_) {}
            throw new Error('缓存恢复未完成，原值保留在恢复前备份中。');
        }
        return undo;
    }
    if (typeof module !== 'undefined') module.exports = {mergeDraft,syncBrowser,restoreBrowser,parse,apiFields,otherFields,searchProviderView};
    if (typeof window === 'undefined') return;
    const pageReady = window.SayaWebWindow?.whenReady || ((fn,options)=>document.addEventListener('DOMContentLoaded',fn,options));
    function confirmTransfer(message){
        return new Promise(resolve=>{
            const overlay=document.createElement('div');overlay.className='local-api-confirm-overlay';
            const panel=document.createElement('section');panel.className='local-api-confirm';panel.setAttribute('role','alertdialog');panel.setAttribute('aria-label','确认配置操作');
            const text=document.createElement('p');text.textContent=message;panel.append(text);
            const finish=value=>{document.removeEventListener('keydown',onKey);overlay.remove();resolve(value);};
            const onKey=event=>{if(event.key==='Escape')finish(false);};
            for(const [label,value] of [['取消',false],['确认执行',true]]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>finish(value);panel.append(button);}
            overlay.onclick=event=>{if(event.target===overlay)finish(false);};overlay.append(panel);document.body.append(overlay);
            document.addEventListener('keydown',onKey);panel.querySelector('button').focus();
        });
    }
    let profile = null, viewRevision = null, loading = null, dirty = false, busy = false;
    const cards = () => [...document.querySelectorAll('[data-local-profile]')];
    const message = (text,error=false) => cards().forEach(card=>{const el=card.querySelector('[data-profile-message]');el.textContent=text;el.dataset.error=String(error);});
    async function api(path='',options={}) {
        const controller=new AbortController(),timeout=window.setTimeout(()=>controller.abort(),8000);
        try {
        const init = {...options,signal:controller.signal,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-Saya-QQ-UI':'1',...(options.headers||{})}};
        let response = await fetch('/api/qq/local-profile'+path,init);
        if (response.status===401) {
            const session = await fetch('/api/qq/ui-session',{method:'POST',signal:controller.signal,credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});
            if (!session.ok) throw new Error('请从本机 Saya 网页打开配置。');
            response = await fetch('/api/qq/local-profile'+path,init);
        }
        const data = await response.json();
        if (!response.ok) {const error=new Error(typeof data.detail==='string'?data.detail:'本机配置操作失败。');error.status=response.status;throw error;}
        return data;
        } catch(error) {if(error.name==='AbortError')throw new Error('本机配置操作超时；未确认保存结果，请重新读取本机版本后核对。');throw error;}
        finally {window.clearTimeout(timeout);}
    }
    function render(status,reset=false) {
        const search=searchProviderView(status);
        if (reset || !viewRevision) viewRevision=status.revision;
        cards().forEach(card=>{
            const count=Object.values(status.configured).filter(Boolean).length;
            card.querySelector('[data-profile-summary]').textContent=`本机已保存 ${count} 项；${status.prefer_local?'本机文件优先':'使用浏览器配置'}。当前搜索：${search.label}，密钥${search.configured?'已配置':'未配置'}。环境变量如已配置，仍优先。`;
            if (reset || !dirty) card.querySelector('[data-profile-prefer]').checked=status.prefer_local;
        });
        const shared = document.getElementById('api-use-shared-credentials');
        if (shared) shared.disabled=status.prefer_local && !!status.configured.api_key;
        const searchStatus=document.getElementById('search-provider-status');
        if(searchStatus){
            searchStatus.textContent=`当前启用 ${search.label}，${search.configured?'密钥已配置（连接状态需实际测试）':'尚未配置可用密钥'}。网页与QQ共用此选择。`;
            document.querySelectorAll('[data-search-enable]').forEach(button=>{
                const active=button.dataset.searchEnable===search.provider;
                button.textContent=(active?'已启用 ':'启用 ')+(button.dataset.searchEnable==='deepseek'?'DeepSeek':'Brave');
                button.setAttribute('aria-pressed',String(active));button.classList.toggle('is-active',active);
            });
            const reuse=document.getElementById('deepseek-search-reuse-model');
            if(reuse){
                if(!reuse.dataset.initialized){reuse.checked=!!status.deepseek_model_key_available&&!status.configured.deepseek_search_api_key;reuse.dataset.initialized='1';}
                reuse.disabled=!status.deepseek_model_key_available;
            }
        }
        window.dispatchEvent(new CustomEvent('saya-web-configuration'));
    }
    async function refresh(reset=false) {
        if (loading) return loading;
        loading=(async()=>{
            const status=await api();
            const runtime=await api('/read',{method:'POST',body:JSON.stringify({revision:status.revision,purpose:'runtime'})});
            const changed = viewRevision && viewRevision!==runtime.revision;
            profile=runtime; render(runtime,reset);
            if(changed && dirty && !reset)message('本机配置已由其他页面更新。浏览器草稿保留；保存前请重新读取本机版本。',true);
            if(reset){dirty=false;message('已读取本机版本；浏览器缓存和表单草稿保持原样。');}
            return runtime;
        })();
        try{return await loading;}finally{loading=null;}
    }
    async function act(work) {
        if(busy)return; busy=true;
        cards().forEach(card=>card.querySelectorAll('button').forEach(b=>b.disabled=true));
        if(document.getElementById('search-provider-status'))document.querySelectorAll('[data-search-enable]').forEach(b=>b.disabled=true);
        try{await work();}catch(error){
            if(error.status===409){try{await refresh();}catch(_){} }
            message(error.message,true);
        }
        finally{busy=false;cards().forEach(card=>card.querySelectorAll('button').forEach(b=>b.disabled=false));
            if(document.getElementById('search-provider-status'))document.querySelectorAll('[data-search-enable]').forEach(b=>b.disabled=false);}
    }
    function drafts() {
        parse(localStorage.getItem(apiKey));parse(localStorage.getItem(otherKey));
        window.SayaBrowserApiDraft?.flush();
        const apiDraft=parse(localStorage.getItem(apiKey)), otherDraft=parse(localStorage.getItem(otherKey));
        const result=Object.fromEntries([...apiFields.map(k=>[k,apiDraft[k]]),...otherFields.map(k=>[k,otherDraft[k]])].filter(([,v])=>typeof v==='string' && v.trim()));
        if(!result.api_key){delete result.api_url;delete result.api_model_name;}
        return result;
    }
    function save(card,onlySearch=false,preferenceOnly=false) { return act(async()=>{
        if(!profile)await refresh();
        let values = preferenceOnly ? {} : drafts();
        if(onlySearch) { const field=(profile.search_provider||'brave')+'_search_api_key';values={[field]:values[field]||''};if(!values[field])throw new Error('当前浏览器未填写所选厂家的搜索密钥；已有本机密钥时无需再次同步，可在Search provider直接启用。'); }
        const clears=preferenceOnly || onlySearch ? [] : [...card.querySelectorAll('[data-clear-field]:checked')].map(el=>el.dataset.clearField);
        if(clears.length && !await confirmTransfer('清空所选本机字段？浏览器缓存保留；清空模型连接任一项会同时清空整组模型连接。'))return;
        if(clears.some(k=>['api_key','api_url','api_model_name'].includes(k)))clears.push('api_key','api_url','api_model_name');
        for(const k of clears)delete values[k];
        const result=await api('',{method:'PUT',body:JSON.stringify({values,revision:viewRevision,
            overwrite:card.querySelector('[data-profile-overwrite]').checked,clear_fields:clears,
            prefer_local:card.querySelector('[data-profile-prefer]').checked})});
        viewRevision=result.revision;dirty=false;await refresh(true);
        card.querySelectorAll('[data-clear-field]').forEach(el=>el.checked=false);
        message('已原子保存到本机文件；其他浏览器会读取同一配置。当前浏览器缓存未被替换。');
        window.SayaQQSettings?.refresh?.();
    }); }
    window.SayaLocalProfile = {
        ready:()=>refresh(),
        api:draft=>{ const result=mergeDraft(draft,profile,apiFields,window.SayaSettingsStore?.state()?.enabled===true);if(profile?.prefer_local && result.api_key)result.use_shared_credentials=true;return result; },
        other:draft=>mergeDraft(draft,profile,otherFields,window.SayaSettingsStore?.state()?.enabled===true),
        push:()=>save(cards()[0]),
        pushSearch:()=>save(cards()[0],true),
        status:()=>profile ? {prefer_local:profile.prefer_local,search_provider:profile.search_provider||'brave',configured:{...profile.configured,api_key:!!profile.settings.api_key}} : null
    };
    function init() {
        if(document.getElementById('search-provider-status'))document.querySelectorAll('[data-search-enable]').forEach(button=>{
            button.onclick=()=>act(async()=>{
                if(!profile)await refresh();
                const provider=button.dataset.searchEnable;
                const typed=document.getElementById(provider==='deepseek'?'deepseek-search-key-input':'brave-search-key-input')?.value.trim()||'';
                const result=await api('/search-provider',{method:'PUT',body:JSON.stringify({provider,revision:viewRevision,
                    api_key:typed||null,reuse_model_key:provider==='deepseek'&&!typed&&!!document.getElementById('deepseek-search-reuse-model')?.checked,
                    overwrite:!!cards()[0]?.querySelector('[data-profile-overwrite]').checked})});
                viewRevision=result.revision;await refresh();
                const feedback=document.getElementById('search-provider-feedback');
                feedback.textContent=`已启用 ${provider==='deepseek'?'DeepSeek':'Brave'}；另一家已停用，密钥仍保留。其他API草稿未保存或改写。`;
                window.SayaQQSettings?.refresh?.();
            });
        });
        cards().forEach(card=>{
            card.querySelector('[data-profile-push]').addEventListener('click',()=>save(card));
            card.querySelector('[data-profile-refresh]').addEventListener('click',()=>act(()=>refresh(true)));
            card.querySelector('[data-profile-preference-save]').addEventListener('click',()=>save(card,false,true));
            card.querySelector('[data-profile-pull]').addEventListener('click',()=>act(async()=>{
                if(!profile)await refresh();
                if(!await confirmTransfer('将本机已保存字段同步到此浏览器？会先完整备份当前浏览器API缓存，其他字段保留。'))return;
                const result=await api('/read',{method:'POST',body:JSON.stringify({revision:viewRevision,purpose:'browser_sync'})});
                const backup=syncBrowser(localStorage,result.settings);
                window.SayaBrowserApiDraft?.reload();dirty=false;
                message(`已同步到当前浏览器；原缓存备份：${backup}。`);
            }));
            card.querySelector('[data-profile-restore]').addEventListener('click',()=>act(async()=>{
                const backup=localStorage.getItem(lastBackupKey);
                if(!backup)throw new Error('此浏览器还没有同步前备份。');
                if(!await confirmTransfer('恢复上次同步前的两组浏览器API缓存？当前缓存也会先备份，本机文件不变。'))return;
                restoreBrowser(localStorage,backup);window.SayaBrowserApiDraft?.reload();
                message('已恢复上次浏览器缓存；本机优先选项和文件保持不变。');
            }));
            const clear=card.querySelector('[data-profile-clears]');
            [...apiFields,...otherFields].forEach(key=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.clearField=key;label.append(input,document.createTextNode(names[key]));clear.append(label);});
        });
        document.addEventListener('input',event=>{if(event.target.closest?.('[data-pane="llm"],[data-pane="other"]'))dirty=true;});
        window.addEventListener('storage',event=>{if([apiKey,otherKey].includes(event.key))message('此浏览器缓存已在另一标签页更改。当前表单保留；请重新打开API设置后再保存到本机。',true);});
        void refresh().catch(error=>message(error.message,true));
        window.addEventListener('focus',()=>{void refresh().catch(error=>message(error.message,true));});
        window.setInterval?.(()=>{if(!busy&&document.visibilityState!=='hidden'&&document.getElementById('search-provider-status'))void refresh().catch(error=>message(error.message,true));},5000);
    }
    pageReady(init);
})();
