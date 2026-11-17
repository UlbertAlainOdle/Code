"""Bounded, credential-free connectivity evidence independent of cached QQ login."""

import asyncio
import time

import httpx


class PublicNetworkProbe:
    # Public HEADs only: no account credentials, search requests or model calls.
    URLS = ('https://www.qq.com/', 'https://api.deepseek.com/')

    def __init__(self, transport=None, clock=time.monotonic, ttl=5):
        self.transport = transport
        self.clock = clock
        self.ttl = ttl
        self._at = float('-inf')
        self._value = 'unknown'
        self._lock = asyncio.Lock()

    async def _check(self):
        async with httpx.AsyncClient(transport=self.transport, timeout=1.2, trust_env=False,
                                     follow_redirects=False, headers={'Cache-Control': 'no-cache'}) as client:
            async def reachable(url):
                try:
                    # Any actual HTTP response proves this endpoint was reached;
                    # a 403/404 is not a login error and must not prompt a QR scan.
                    await client.head(url)
                    return True
                except httpx.HTTPError:
                    return False
            results = await asyncio.gather(*(reachable(url) for url in self.URLS))
            return 'reachable' if any(results) else 'unreachable'

    async def probe(self):
        async with self._lock:
            if self.clock() - self._at < self.ttl:
                return self._value
            try:
                self._value = await asyncio.wait_for(self._check(), timeout=2)
            except (asyncio.TimeoutError, OSError, ValueError):
                self._value = 'unknown'
            self._at = self.clock()
            return self._value


public_network_probe = PublicNetworkProbe()
