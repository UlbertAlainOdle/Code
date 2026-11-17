/* Standalone MMD tool: hydrate the same file before any preferences are read. */
(async()=>{
    try{
        await window.SayaSettingsStore.initialize();
        const script=document.createElement('script');script.src='/static/js/mmd_try_showcase.js?v=20261028-runtime-final-2';
        script.onerror=()=>{document.body.textContent='MMD工具加载失败，请刷新。';};document.body.append(script);
    }catch(error){
        const note=document.createElement('p');note.setAttribute('role','alert');note.textContent='本地设置无法读取，工具尚未启动。请检查后端后刷新；原缓存保留。';document.body.prepend(note);
    }
})();
