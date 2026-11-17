"""One live browser UI owner across all browsers; QQ has no dependency on it."""
import hmac
import secrets
import threading

from fastapi import HTTPException


class WebWindowManager:
    def __init__(self, busy=lambda:False):
        self._lock=threading.RLock()
        self._owner=None
        self._busy=busy

    def claim(self):
        with self._lock:
            if self._owner or self._busy():return None
            self._owner=secrets.token_urlsafe(32)
            return self._owner

    def release(self,token):
        with self._lock:
            if self._owner and hmac.compare_digest(self._owner,token):self._owner=None

    def require(self,token):
        with self._lock:
            if not self._owner or not token or not hmac.compare_digest(self._owner,token):
                raise HTTPException(409,'此页面未占用唯一聊天窗口，请刷新页面；若其他浏览器已打开，请先关闭原页面。')

    def status(self):
        with self._lock:return {'occupied':bool(self._owner),'finishing_request':bool(self._busy())}
