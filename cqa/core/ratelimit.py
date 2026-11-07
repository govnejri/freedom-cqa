"""Ограничение скорости на профиль LLM: не больше max_concurrency одновременных вызовов и rpm в минуту.

Лимитер общий для процесса и не привязан к event loop (threading.Lock + короткий опрос),
поэтому работает и при нескольких asyncio.run, и из нескольких потоков.
При 429 gateway ставит общую паузу на весь профиль: pause(seconds).
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from cqa.llm.base import Profile

_POLL_MAX = 0.5


class Limiter:
    def __init__(self, name: str, max_concurrency: int, rpm: int):
        self.name = name
        self._lock = threading.Lock()
        self._active = 0
        self._paused_until = 0.0
        self.configure(max_concurrency, rpm)
        self._tokens = float(self._burst)
        self._last = time.monotonic()

    def configure(self, max_concurrency: int, rpm: int) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.rpm = max(1, int(rpm))
        self._rate = self.rpm / 60.0                       # токенов в секунду
        self._burst = max(1, min(self.max_concurrency, self.rpm))

    def _try_acquire(self) -> float:
        """0 — слот получен; иначе сколько примерно подождать."""
        with self._lock:
            now = time.monotonic()
            if now < self._paused_until:
                return self._paused_until - now
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._active >= self.max_concurrency:
                return 0.05
            if self._tokens < 1.0:
                return (1.0 - self._tokens) / self._rate
            self._tokens -= 1.0
            self._active += 1
            return 0.0

    async def acquire(self) -> None:
        while True:
            wait = self._try_acquire()
            if wait <= 0:
                return
            await asyncio.sleep(min(max(wait, 0.01), _POLL_MAX))

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    def pause(self, seconds: float) -> None:
        """Общая пауза профиля (429): новые вызовы ждут, начатые продолжаются."""
        with self._lock:
            self._paused_until = max(self._paused_until, time.monotonic() + max(0.0, seconds))

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()


_REGISTRY: dict[str, Limiter] = {}
_REG_LOCK = threading.Lock()


def limiter_for(profile: Profile) -> Limiter:
    """Лимитер профиля по имени; при изменении лимитов в профиле — перенастраивается."""
    with _REG_LOCK:
        lim = _REGISTRY.get(profile.name)
        if lim is None:
            lim = _REGISTRY[profile.name] = Limiter(profile.name, profile.max_concurrency, profile.rpm)
        elif (lim.max_concurrency, lim.rpm) != (max(1, profile.max_concurrency), max(1, profile.rpm)):
            with lim._lock:
                lim.configure(profile.max_concurrency, profile.rpm)
        return lim
