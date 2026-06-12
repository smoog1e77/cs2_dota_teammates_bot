"""Middleware: блокирует забаненных пользователей.

Регистрируется на observer'ах message и callback_query. Сессию БД берёт из
data['session'], которую кладёт DbSessionMiddleware (она работает на уровне update,
то есть «снаружи» и доступна здесь).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import settings
from database.queries import get_user


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        user = data.get("event_from_user")
        # Админов не блокируем никогда — иначе случайный самобан запер бы /admin.
        if user is not None and user.id in settings.admins:
            return await handler(event, data)
        if session is not None and user is not None:
            db_user = await get_user(session, user.id)
            if db_user is not None and db_user.is_banned:
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔ Доступ к боту ограничен.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⛔ Доступ к боту ограничен администратором.")
                return None  # дальше по цепочке не пускаем
        return await handler(event, data)
