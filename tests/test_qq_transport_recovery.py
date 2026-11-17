"""Actual loopback sockets, disposable OneBot test server; no QQ processes involved."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore, OneBotClient
from saya_app.services.qq_health import inspect_health


def test_real_socket_login_auth_disconnect_and_reconnect_are_distinct(tmp_path):
    phase={'online':False,'authorized':True}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            if not phase['authorized']:
                self.send_response(401); self.end_headers(); return
            data={'online':phase['online']} if self.path=='/get_status' else {'user_id':10000}
            body=json.dumps({'status':'ok','retcode':0,'data':data}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    port=server.server_port
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    bridge=QQBridge(QQSettings(enabled=True,bot_id='10000',access_token='test-only',webhook_token='test-only',allowed_users={'20000'},
        api_url=f'http://127.0.0.1:{port}'),QQStore(tmp_path/'unused.sqlite3'))
    async def probe(): return await inspect_health(bridge,{},OneBotClient,timeout=1)
    try:
        assert asyncio.run(probe())['qq_login']=='login_required'
        phase['online']=True
        assert asyncio.run(probe())['qq_login']=='matched'
        phase['authorized']=False
        assert asyncio.run(probe())['onebot_auth']=='rejected'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    disconnected=asyncio.run(probe())
    # Windows may report refusal after the outer deadline. Both are unavailable,
    # and neither may retain the previous successful login/authentication state.
    assert disconnected['napcat'] in {'unreachable','timeout'}
    assert disconnected['qq_login']=='unknown' and disconnected['onebot_auth']=='unknown'
    phase['authorized']=True
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert asyncio.run(probe())['qq_login']=='matched'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    assert not bridge.store.path.exists() and bridge.task is None
