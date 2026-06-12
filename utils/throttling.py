"""Простой in-memory rate limiter для анти-спама (лайки/сообщения).

Скользящее окно по пользователю: не более N действий за period секунд.
Хранится в памяти процесса — для одного бота этого достаточно. При нескольких
процессах вынеси счётчики в Redis.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from config import settings


class RateLimiter:
    def __init__(self, max_actions: int, period: float = 60.0) -> None:
        self.max_actions = max_actions
        self.period = period
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        """Зарегистрировать действие. False — если лимит превышен."""
        now = time.monotonic()
        dq = self._hits[user_id]
        boundary = now - self.period
        while dq and dq[0] < boundary:
            dq.popleft()
        if len(dq) >= self.max_actions:
            return False
        dq.append(now)
        return True

    def retry_after(self, user_id: int) -> int:
        """Через сколько секунд снова можно действовать."""
        dq = self._hits.get(user_id)
        if not dq:
            return 0
        return max(1, int(self.period - (time.monotonic() - dq[0])))


# Общий лимитер на «активные» действия (лайк / лайк с сообщением / ответ на лайк).
like_limiter = RateLimiter(settings.actions_per_minute)
