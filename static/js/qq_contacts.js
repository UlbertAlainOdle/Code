/* No request flags are exposed to the page. An action requires its own click. */
(() => {
    const $=key=>document.getElementById('qq-contact-'+key);
    if(!$('panel'))return;
    let state=null,busy=false;
    const feedback=text=>{$('feedback').textContent=text;};
    async function api(path,init={}){
        init={credentials:'same-origin',cache:'no-store',...init,headers:{'Content-Type':'application/json'}};
        let response=await fetch('/api/qq/'+path,init);
        if(response.status===401){
            const auth=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});
            if(!auth.ok)throw new Error('请从本机QQ设置页面打开。');
            response=await fetch('/api/qq/'+path,init);
        }
        const value=await response.json();
        if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'请求未确认成功，请重新读取核对，勿重复操作。');
        return value;
    }
    function action(task){return async()=>{
        if(busy)return;busy=true;$('panel').disabled=true;
        try{await task();}catch(error){feedback(error.message);}finally{busy=false;$('panel').disabled=false;}
    };}
    async function read(){
        state=await api('contact-requests'+($('owner').value?'?owner_id='+encodeURIComponent($('owner').value):''));
        $('owner').value=state.owner_id;
        $('allowed').textContent='原聊天允许账号：'+(state.allowed_users.join('、')||'无')+(state.unified_events_enabled?'。统一事件已开启：达到关系门槛并批准好友后，可使用本人事件私聊；不会获得主人的私人记忆权限。':'。接受好友不改变此列表，私人记忆仍需独立授权。');
        $('items').replaceChildren();
        for(const request of state.requests){
            const card=document.createElement('div'),description=document.createElement('p');
            description.textContent=`QQ ${request.user} · ${request.state} · ${request.comment || ''}`;
            card.appendChild(description);
            if(request.state==='pending')for(const approve of [true,false]){
                const button=document.createElement('button');button.type='button';button.textContent=approve?(state.unified_events_enabled?'接受好友（须达到关系门槛）':'接受好友（不增加聊天授权）'):'拒绝好友申请';
                button.addEventListener('click',action(async()=>{
                    if(!state || $('owner').value!==state.owner_id)throw new Error('请先重新读取。');
                    if(!window.confirm(`${approve?'接受':'拒绝'} QQ ${request.user} 的好友申请？`+(state.unified_events_enabled?'达到关系门槛的好友可使用本人事件私聊，不授予主人私人记忆权限。':'接受好友不会授予聊天或私人记忆权限。')))return;
                    await api('contact-requests/'+encodeURIComponent(request.id),{method:'POST',body:JSON.stringify({
                        owner_id:state.owner_id,revision:state.revision,approve,confirmed:true})});
                    await read();feedback(state.unified_events_enabled?'好友申请已处理；事件私聊仍按当前关系资格判断。':'好友申请已处理；聊天允许列表保持。');
                }));card.appendChild(button);
            }
            $('items').appendChild(card);
        }
        feedback(state.requests.length?'仅pending可审核，结果待核对的请求不会再次提交。':'暂无好友申请；不会自动接受陌生人。');
    }
    $('read').addEventListener('click',action(read));
    $('owner').addEventListener('change',()=>{state=null;$('items').replaceChildren();feedback('账号已变化，请重新读取。');});
})();
