"""Разовый перенос данных SQLite → PostgreSQL (перед запуском под нагрузкой).

Запуск ИЗ КОРНЯ проекта (где лежит bot.py):

    SOURCE_URL="sqlite+aiosqlite:///bot.db" \
    DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/dbname" \
    BOT_TOKEN=x .venv/bin/python -m scripts.migrate_sqlite_to_pg

  * SOURCE_URL   — откуда читаем (по умолчанию текущий bot.db).
  * DATABASE_URL — куда пишем (ОБЯЗАТЕЛЬНО, строка PostgreSQL).
  * BOT_TOKEN    — любое значение: нужно лишь чтобы прошёл импорт config.

Таблицы в цели создаются автоматически. Переноси в ЧИСТУЮ базу: скрипт не
очищает цель и не разруливает дубли. Типы (даты, булевы, id) конвертируются
самим SQLAlchemy, потому что читаем/пишем через типизированные таблицы моделей.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from database.models import Base, Interaction, Profile, SearchFilter, User

SOURCE_URL = os.environ.get("SOURCE_URL", "sqlite+aiosqlite:///bot.db")
TARGET_URL = os.environ["DATABASE_URL"]

# Порядок важен: users раньше profiles (внешний ключ profiles.user_id → users.id).
MODELS = [User, Profile, Interaction, SearchFilter]


async def main() -> None:
    if not TARGET_URL.startswith("postgresql"):
        raise SystemExit("DATABASE_URL должен быть строкой PostgreSQL (postgresql+asyncpg://...)")

    src = create_async_engine(SOURCE_URL)
    dst = create_async_engine(TARGET_URL)

    # Схема в цели.
    async with dst.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    total = 0
    for model in MODELS:
        table = model.__table__
        async with src.connect() as sconn:
            rows = (await sconn.execute(select(table))).mappings().all()
        if not rows:
            print(f"{table.name}: 0 строк")
            continue
        async with dst.begin() as dconn:
            await dconn.execute(insert(table), [dict(r) for r in rows])
        total += len(rows)
        print(f"{table.name}: перенесено {len(rows)}")

    # Подтягиваем автоинкрементные последовательности под максимальный перенесённый id,
    # иначе следующая вставка в Postgres столкнётся по первичному ключу.
    async with dst.begin() as dconn:
        for model in (Profile, Interaction, SearchFilter):
            t = model.__tablename__
            await dconn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1))"
                )
            )
    print("последовательности id синхронизированы")

    await src.dispose()
    await dst.dispose()
    print(f"\nГотово. Всего перенесено строк: {total}")


if __name__ == "__main__":
    asyncio.run(main())
