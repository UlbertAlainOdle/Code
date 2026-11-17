"""Drain barrier for active web writes/streams; contains no request content."""
from dataclasses import dataclass

from starlette.responses import JSONResponse


# These console writes originate outside the QQ consumer/task registry. Count
# their entire response lifetime so maintenance waits for the original writer.
MEMORY_CONSOLE_WRITES = frozenset({
    '/api/qq/memory-console/policy', '/api/qq/memory-console/decision',
    '/api/qq/memory-console/operation-preview', '/api/qq/memory-console/operation-confirm',
    '/api/qq/memory-console/save-select', '/api/qq/memory-console/save-decision',
    '/api/qq/memory-console/candidate-preview',
    '/api/qq/memory-console/revision-preview', '/api/qq/memory-console/revision-confirm',
    '/api/qq/memory-console/action-preview', '/api/qq/memory-console/action-decision',
    '/api/qq/memory-console/event-decision',
    '/api/qq/memory-console/command',
    '/api/qq/memory-console/library-scan', '/api/qq/memory-console/library-prepare',
    '/api/qq/memory-console/library-overlap', '/api/qq/memory-console/daily-overlap',
    '/api/qq/memory-console/daily-overlap-cancel',
})


@dataclass
class ServiceActivity:
    active_web_requests: int = 0
    active_web_chats: int = 0


class ServiceActivityMiddleware:
    def __init__(self, app, activity, is_draining,on_web_chat=None):
        self.app = app
        self.activity = activity
        self.is_draining = is_draining
        self.on_web_chat=on_web_chat

    async def __call__(self, scope, receive, send):
        tracked = (scope['type'] == 'http' and scope.get('method') in {'POST','PUT','PATCH','DELETE'}
                   and (not scope.get('path','').startswith('/api/qq/')
                        or scope.get('path','').rstrip('/') in MEMORY_CONSOLE_WRITES))
        if not tracked:
            return await self.app(scope, receive, send)
        if self.is_draining():
            response = JSONResponse({'detail':'服务正在维护排空，请稍后重试。'}, status_code=503, headers={'Retry-After':'5'})
            return await response(scope, receive, send)
        self.activity.active_web_requests += 1
        chat=scope.get('path')=='/chat_stream'
        if chat:
            self.activity.active_web_chats += 1
        try:
            if chat and self.on_web_chat:self.on_web_chat()
            # For SSE this await lasts through the body and disconnect, not just headers.
            return await self.app(scope, receive, send)
        finally:
            self.activity.active_web_requests -= 1
            if chat:self.activity.active_web_chats -= 1
