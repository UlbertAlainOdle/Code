/* Explicit owner-scoped policy editing; binary images are never loaded here. */
(()=>{
    const $=key=>document.getElementById('qq-media-'+key);
    if(!$('panel'))return;
    let loaded=null,busy=false;
    const feedback=text=>{$('feedback').textContent=text;};
    async function api(method,body){
        const url='/api/qq/media-policy'+(method==='GET'&&$('owner').value?'?owner_id='+encodeURIComponent($('owner').value):'');
        const init={method,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'}};
        if(body)init.body=JSON.stringify(body);
        let response=await fetch(url,init);
        if(response.status===401){
            const auth=await fetch('/api/qq/ui-session',{method:'POST',credentials:'same-origin',headers:{'X-Saya-QQ-UI':'1'}});
            if(!auth.ok)throw Error('请从本机QQ设置打开。');
            response=await fetch(url,init);
        }
        const data=await response.json();
        if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'设置未保存，请重新读取核对。');
        return data;
    }
    function action(task){return async()=>{
        if(busy)return;busy=true;$('panel').disabled=true;
        try{await task();}catch(error){feedback(error.message);}finally{busy=false;$('panel').disabled=false;}
    };}
    $('read').addEventListener('click',action(async()=>{
        loaded=await api('GET');$('owner').value=loaded.owner_id;const v=loaded.values;
        $('intake').checked=v.intake_enabled;$('images').checked=v.images_enabled;$('quotes').checked=v.quote_images_enabled;
        const documents=loaded.implemented?.includes('documents')===true;
        for(const key of ['documents','docquotes','doccount','docbytes','docpages','docchars','doctimeout'])$(key).disabled=!documents;
        $('documents').checked=v.documents_enabled===true;$('docquotes').checked=v.quote_documents_enabled===true;
        $('doccount').value=v.max_documents??2;$('docbytes').value=(v.max_document_bytes??5242880)/1048576;
        $('docpages').value=v.max_document_pages??20;$('docchars').value=v.max_document_chars??12000;$('doctimeout').value=v.document_timeout_seconds??8;
        const voice=loaded.implemented?.includes('tts_reply')===true;
        for(const key of ['voice','voicechars','voiceseconds','voicetimeout','voiceretention'])$(key).disabled=!voice;
        $('voice').checked=v.voice_reply_enabled===true;$('voicechars').value=v.voice_max_chars??120;
        $('voiceseconds').value=v.voice_max_seconds??30;$('voicetimeout').value=v.voice_timeout_seconds??45;$('voiceretention').value=v.voice_retention_seconds??300;
        $('count').value=v.max_images;$('bytes').value=v.max_image_bytes/1048576;
        $('total').value=v.max_total_bytes/1048576;$('pixels').value=v.max_pixels/1000000;$('timeout').value=v.timeout_seconds;
        $('model').textContent=loaded.vision_model_configured?'视觉模型：'+loaded.vision_model:'尚未配置视觉模型；请在现有本机API配置中选择支持图片的模型。';
        $('confirm').checked=false;feedback('已读取，修改仅在明确确认后保存。');
    }));
    $('owner').addEventListener('input',()=>{loaded=null;$('confirm').checked=false;feedback('账号已变化，请重新读取。');});
    $('save').addEventListener('click',action(async()=>{
        if(!loaded||loaded.owner_id!==$('owner').value)throw Error('请先读取当前主人设置。');
        if(!$('confirm').checked)throw Error('请先核对并勾选确认。');
        const values={...loaded.values,intake_enabled:$('intake').checked,images_enabled:$('images').checked,quote_images_enabled:$('quotes').checked,
            max_images:Number($('count').value),max_image_bytes:Math.round(Number($('bytes').value)*1048576),
            max_total_bytes:Math.round(Number($('total').value)*1048576),max_pixels:Math.round(Number($('pixels').value)*1000000),
            timeout_seconds:Number($('timeout').value)};
        if(loaded.implemented?.includes('documents'))Object.assign(values,{
            documents_enabled:$('documents').checked,quote_documents_enabled:$('docquotes').checked,max_documents:Number($('doccount').value),
            max_document_bytes:Math.round(Number($('docbytes').value)*1048576),max_document_pages:Number($('docpages').value),
            max_document_chars:Number($('docchars').value),document_timeout_seconds:Number($('doctimeout').value)});
        if(loaded.implemented?.includes('tts_reply'))Object.assign(values,{
            voice_reply_enabled:$('voice').checked,voice_max_chars:Number($('voicechars').value),voice_max_seconds:Number($('voiceseconds').value),
            voice_timeout_seconds:Number($('voicetimeout').value),voice_retention_seconds:Number($('voiceretention').value)});
        const data=await api('PUT',{owner_id:loaded.owner_id,revision:loaded.revision,media_revision:loaded.media_revision,confirmed:true,values});
        Object.assign(loaded,data);$('confirm').checked=false;feedback('附件设置已保存；图片与文档分别按各自开关生效。');
    }));
})();
