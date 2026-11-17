import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter,WebSocket,WebSocketDisconnect,Request,HTTPException

router=APIRouter(prefix='/api/web-window',tags=['web-window'])


@router.get('/status')
def status(request:Request):
    from .qq import local_origin
    if not local_origin(request):raise HTTPException(403,'仅允许本机访问')
    return request.app.state.web_window.status()


def local_websocket(socket):
    try:
        origin=urlsplit(socket.headers.get('origin',''))
        expected='https' if socket.url.scheme=='wss' else 'http'
        return (ipaddress.ip_address(socket.client.host).is_loopback
            and socket.url.hostname in {'127.0.0.1','localhost','::1'}
            and origin.scheme==expected and origin.hostname==socket.url.hostname
            and (origin.port or (443 if expected=='https' else 80))==(socket.url.port or (443 if expected=='https' else 80))
            and not origin.username and not origin.password and not origin.query and not origin.fragment and origin.path in {'','/'})
    except (ValueError,AttributeError):return False


@router.websocket('/connect')
async def connect(socket:WebSocket):
    if not local_websocket(socket):
        await socket.close(code=4403);return
    manager=socket.app.state.web_window
    await socket.accept()
    token=manager.claim()
    if token is None:
        await socket.send_json({'type':'occupied','message':'聊天页面已在其他窗口打开，或上一条回复尚未结束。请先关闭原页面，等待回复结束后重新尝试。'})
        await socket.close(code=4409);return
    try:
        await socket.send_json({'type':'accepted','token':token})
        while True:
            message=await socket.receive_text()
            if len(message)>512:
                await socket.close(code=1009);break
            if message=='ping':await socket.send_json({'type':'pong'})
            if message=='release':
                manager.release(token)
                await socket.send_json({'type':'released'})
                await socket.close(code=1000);break
    except WebSocketDisconnect:pass
    finally:manager.release(token)
