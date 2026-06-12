"""Middleware: отмечает пользователя активным на каждом действии.

По `last_active` лента прячет давно не заходивших — анкета возвращается, как
только человек снова что-то нажмёт. Обновляем ПОСЛЕ обработчика, чтобы не мешать
его работе, и в своём commit, потому что не каждый хендлер коммитит сам.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database.queries import touch_user_activity


class ActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        session = data.get("session")
        user = data.get("event_from_user")
        if session is not None and user is not None:
            try:
                await touch_user_activity(session, user.id)
                await session.commit()
            except Exception:
                await session.rollback()
        return result
