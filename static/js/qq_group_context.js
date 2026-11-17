/* Owner-only, explicitly read per-group settings; no private content on boot. */
(() => {
    'use strict';
    const $ = key => document.getElementById('qq-group-' + key);
    if (!$('panel')) return;
    let loaded=null, selected=null, busy=false;
    const feedback=text => { $('feedback').textContent=text; };
    async function api(method,body) {
        const init={method,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'}};
        if(body)init.body=JSON.stringify(body);
        const url='/api/qq/group-context'+(method==='GET' && $('owner').value?'?owner_id='+encodeURIComponent($('owner').value):'');
        let response=await fetch(url,init);
        if(response.status===401){
            const auth=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});
            if(!auth.ok)throw new Error('请从本机QQ设置页面打开。');
            response=await fetch(url,init);
        }
        const data=await response.json();
        if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'群设置未保存，请重新读取核对。');
        return data;
    }
    function option(value,label){const node=document.createElement('option');node.value=value;node.textContent=label;return node;}
    function show(){
        selected=loaded?.groups.find(row=>row.group_id===$('select').value)||null;
        $('confirm').checked=false;
        if(!selected)return;
        const v=selected.values;
        for(const [key,field] of [['context','context_enabled'],['memory','memory_enabled'],['write','write_enabled'],['admin','admin_writes'],['wake','reply_wakeup']])$(key).checked=v[field];
        for(const [key,field] of [['retention','retention_seconds'],['idle','topic_idle_seconds'],['days','memory_retention_days']])$(key).value=v[field];
        $('writers').value=(v.writer_ids||[]).join(',');
        feedback('已读取群 '+selected.group_id+'；修改前请核对公开和写入范围。'+(loaded.task_control_enabled?'':'当前任务控制关闭，群新功能暂停。'));
    }
    function action(task){return async()=>{
        if(busy)return;busy=true;$('panel').disabled=true;
        try{await task();}catch(error){feedback(error.message);}finally{busy=false;$('panel').disabled=false;}
    };}
    $('read').addEventListener('click',action(async()=>{
        loaded=await api('GET');
        $('owner').value=loaded.owner_id;
        $('select').replaceChildren(...loaded.groups.map(row=>option(row.group_id,'群 '+row.group_id)));
        if(loaded.groups.length)$('select').value=loaded.groups[0].group_id;
        show();if(!selected)feedback('当前没有已授权群，请先在原群管理中明确授权。');
    }));
    $('select').addEventListener('change',show);
    $('owner').addEventListener('change',()=>{loaded=null;selected=null;$('confirm').checked=false;feedback('主人账号已变化，请重新读取。');});
    $('save').addEventListener('click',action(async()=>{
        if(!loaded || !selected || loaded.owner_id!==$('owner').value)throw new Error('请先读取已授权群。');
        if(!$('confirm').checked)throw new Error('请先勾选，确认当前群和范围。');
        const values={context_enabled:$('context').checked,memory_enabled:$('memory').checked,
            write_enabled:$('write').checked,admin_writes:$('admin').checked,reply_wakeup:$('wake').checked,
            writer_ids:$('writers').value.split(/[,，\s]+/).filter(Boolean),
            retention_seconds:Number($('retention').value),topic_idle_seconds:Number($('idle').value),
            memory_retention_days:Number($('days').value)};
        const data=await api('PUT',{owner_id:loaded.owner_id,group_id:selected.group_id,
            expected_revision:selected.revision,revision:loaded.revision,confirmed:true,values});
        Object.assign(selected,data);$('confirm').checked=false;
        feedback('已保存群 '+selected.group_id+'；此群旧公共话题已结束，新请求使用新范围。');
    }));
})();
