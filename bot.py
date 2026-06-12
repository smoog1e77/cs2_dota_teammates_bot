"""Точка входа. Запуск: python bot.py"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from database.engine import engine, init_db, session_maker
from handlers import setup_routers
from middlewares.activity import ActivityMiddleware
from middlewares.ban import BanMiddleware
from middlewares.database import DbSessionMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot")


async def main() -> None:
    # Создаём таблицы (если их ещё нет).
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # На каждый апдейт открываем сессию БД и кладём её в data['session'].
    dp.update.middleware(DbSessionMiddleware(session_maker))
    # Блокировка забаненных (использует сессию из middleware выше).
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    # Отметка «последней активности» — для свежести ленты.
    dp.message.middleware(ActivityMiddleware())
    dp.callback_query.middleware(ActivityMiddleware())
    dp.include_router(setup_routers())

    logger.info("Бот запущен. Ожидаю сообщения…")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
