"""TTL cache with single-flight deduplication.

Single-flight matters more than the cache itself: if three users scan the same
trending token at once, the naive version profiles every wallet three times.
Here the second and third callers await the first caller's result.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from cachetools import TTLCache

log = logging.getLogger(__name__)


class AsyncCache:
    def __init__(self, maxsize: int, ttl: int, name: str = "cache"):
        self._store: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._inflight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.name = name
        self.hits = 0
        self.misses = 0
        self.dedupes = 0

    def peek(self, key: str) -> Any | None:
        return self._store.get(key)

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value

    async def get_or_compute(
        self, key: str, factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        async with self._lock:
            if key in self._store:
                self.hits += 1
                return self._store[key]
            existing = self._inflight.get(key)
            if existing is not None:
                self.dedupes += 1
                fut = existing
            else:
                self.misses += 1
                fut = asyncio.get_running_loop().create_future()
                self._inflight[key] = fut
        if existing is not None:
            return await asyncio.shield(fut)

        try:
            value = await factory()
        except Exception as exc:  # noqa: BLE001
            async with self._lock:
                self._inflight.pop(key, None)
            if not fut.done():
                fut.set_exception(exc)
            raise
        async with self._lock:
            self._store[key] = value
            self._inflight.pop(key, None)
        if not fut.done():
            fut.set_result(value)
        return value

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "name": self.name,
            "size": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "dedupes": self.dedupes,
            "hit_rate": round(100 * self.hits / total, 1) if total else 0.0,
        }
