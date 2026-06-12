"""Надёжная отправка в Telegram: один ретрай на флуд-лимит, лог прочих ошибок.

Под нагрузкой (рекламный наплыв) Telegram может ответить флуд-лимитом
(TelegramRetryAfter) — тогда нужно подождать и повторить, иначе уведомление
потеряется молча. Прочие ошибки (бот заблокирован, битый photo_id, длинная
подпись) гасим и логируем, чтобы один сбойный апдейт не ронял обработчик.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.exceptions import TelegramRetryAfter


async def safe_send(
    make: Callable[[], Awaitable[Any]],
    *,
    logger: logging.Logger,
    descr: str,
) -> Any | None:
    """Выполнить отправку `make()`. На флуд-лимит — подождать и повторить один раз.

    Возвращает результат отправки при успехе или None, если доставить не удалось.
    `make` — фабрика корутины (lambda), чтобы её можно было создать заново на ретрае.
    """
    for attempt in (1, 2):
        try:
            return await make()
        except TelegramRetryAfter as e:
            if attempt == 1:
                logger.info("%s: флуд-лимит, жду %s c и повторяю", descr, e.retry_after)
                await asyncio.sleep(e.retry_after + 1)
                continue
            logger.warning("%s: флуд-лимит и после повтора — пропускаю", descr)
            return None
        except Exception as e:  # noqa: BLE001 — намеренно широкий: доставка не критична
            logger.warning("%s не доставлено: %s: %s", descr, type(e).__name__, e)
            return None
    return None
