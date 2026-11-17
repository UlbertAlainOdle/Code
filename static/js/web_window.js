/* The backend WebSocket owns the one UI slot. No browser-storage lock. */
(() => {
    'use strict';
    let socket=null,token='',booted=false,booting=false,bootFailed=false,retryTimer=null,wasOwner=false,released=false;
    const readyCallbacks=[],loadCallbacks=[];
    const whenReady=callback=>booting&&!booted?readyCallbacks.push(callback):document.readyState==='loading'?document.addEventListener('DOMContentLoaded',callback,{once:true}):queueMicrotask(callback);
    const whenLoaded=callback=>booting&&!booted?loadCallbacks.push(callback):document.readyState==='complete'?queueMicrotask(callback):window.addEventListener('load',callback,{once:true});
    const originalFetch=window.fetch.bind(window);
    function show(message) {
        document.documentElement.dataset.sayaWindow='waiting';
        const note=document.getElementById('saya-window-message');if(note)note.textContent=message;
        window.SayaSpeech?.stop();
    }
    window.fetch=async(input,init={})=>{
        const raw=typeof input==='string'||input instanceof URL?String(input):input.url;
        const url=new URL(raw,window.location.href);
        if(url.origin===window.location.origin) {
            if(!token)throw new Error('聊天窗口连接已中断；未改用其他窗口继续发送。');
            if(url.pathname==='/chat_stream')await window.SayaSettingsStore?.beforeChat();
            const headers=new Headers(input instanceof Request?input.headers:undefined);
            new Headers(init.headers||{}).forEach((value,key)=>headers.set(key,value));
            headers.set('X-Saya-Web-Window',token);init={...init,headers};
        }
        return originalFetch(input,init);
    };
    async function boot() {
        if(booted||booting)return;booting=true;
        show('已取得唯一窗口，正在加载聊天与3D资源…');
        try {
            for(const placeholder of document.querySelectorAll('script[data-web-boot-src]')) {
                if(!token)throw new Error('加载时窗口连接中断，请刷新后重新进入；原浏览器设置保留。');
                await new Promise((resolve,reject)=>{
                    const script=document.createElement('script');script.src=placeholder.dataset.webBootSrc;script.async=false;
                    script.onload=resolve;script.onerror=()=>reject(new Error('页面资源加载失败，请刷新后重试。'));
                    document.body.append(script);
                });
                if(placeholder.dataset.settingsBootstrap){
                    if(!window.SayaSettingsStore)throw new Error('本地设置组件无法加载，请刷新重试。');
                    await window.SayaSettingsStore.initialize();
                }
                if(placeholder.dataset.apiBootstrap)await window.SayaLocalProfile.ready();
            }
            booted=true;
            readyCallbacks.splice(0).forEach(callback=>queueMicrotask(callback));
            loadCallbacks.splice(0).forEach(callback=>whenLoaded(callback));
            if(token)document.documentElement.dataset.sayaWindow='active';
        } catch(error){bootFailed=true;show(error.message);document.getElementById('saya-window-retry').textContent='刷新页面重试';}
        finally{booting=false;}
    }
    function connect() {
        clearTimeout(retryTimer);
        if(socket&&[WebSocket.CONNECTING,WebSocket.OPEN].includes(socket.readyState))return;
        released=false;
        show('正在申请唯一聊天窗口…');
        const current=new WebSocket(`${window.location.protocol==='https:'?'wss:':'ws:'}//${window.location.host}/api/web-window/connect`);
        socket=current;let occupied=false;
        current.onmessage=event=>{
            if(current!==socket)return;
            let data;try{data=JSON.parse(event.data);}catch(_){return;}
            if(data.type==='accepted') {
                token=data.token;wasOwner=true;
                if(booted)document.documentElement.dataset.sayaWindow='active';else if(!bootFailed)void boot();
            } else if(data.type==='occupied') {occupied=true;show(data.message);}
            else if(data.type==='released')show('此页面已释放占用，可在其他浏览器打开聊天。原页面内容和浏览器设置保留。');
        };
        current.onerror=()=>{if(current===socket&&!occupied)show('暂时无法连接本机后端，请检查服务后重新尝试。');};
        current.onclose=()=>{
            if(current!==socket)return;token='';
            if(released){show('此页面已释放占用，可在其他浏览器打开聊天。原页面内容和浏览器设置保留。');return;}
            if(!occupied) {
                show('本机连接已中断，页面已暂停。正在等待后端恢复；原页面内容保留。');
                if(wasOwner)retryTimer=setTimeout(connect,3000);
            }
        };
    }
    async function release(){
        try{await window.SayaSettingsStore?.flush();}
        catch(error){window.showToast?.('设置尚未同步，请先重试保存或导出草稿。',{type:'error'});return;}
        released=true;wasOwner=false;token='';clearTimeout(retryTimer);
        show('正在释放此页面占用…');
        if(socket?.readyState===WebSocket.OPEN)socket.send('release');else socket?.close();
    }
    window.SayaWebWindow={whenReady,whenLoaded,release,active:()=>!!token};
    whenReady(()=>{
        document.getElementById('saya-window-retry').onclick=()=>bootFailed?window.location.reload():connect();
        connect();
    });
    window.addEventListener('pagehide',()=>{clearTimeout(retryTimer);wasOwner=false;socket?.close();});
    window.addEventListener('pageshow',event=>{if(event.persisted)connect();});
})();
