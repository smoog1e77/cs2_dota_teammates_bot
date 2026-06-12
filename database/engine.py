"""Асинхронный движок БД и фабрика сессий.

Работает и со SQLite (по умолчанию), и с PostgreSQL — отличается только
строка DATABASE_URL в .env. Для SQLite включаем WAL + foreign_keys, чтобы
бот корректно держал параллельные запросы от множества пользователей.
"""
from __future__ import annotations

import os

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from database.models import Base

# Готовим строку подключения и параметры соединения под нужный драйвер.
_url = make_url(settings.database_url)
_connect_args: dict = {}

if _url.drivername.startswith("sqlite"):
    # Убеждаемся, что папка под файл базы существует — важно для /app/shared/bot.db
    # (иначе бот упал бы, если каталога ещё нет). Для bot.db и :memory: ничего не делаем.
    _db_path = _url.database
    if _db_path and _db_path != ":memory:" and os.path.dirname(_db_path):
        os.makedirs(os.path.dirname(_db_path), exist_ok=True)
elif _url.drivername.startswith("postgresql"):
    # Облачный Postgres (Neon и т.п.): драйвер — asyncpg, SSL обязателен. asyncpg не
    # понимает libpq-параметры из строки (sslmode/channel_binding) — убираем их и
    # включаем SSL здесь. Если драйвер не указан — подставляем asyncpg сами, чтобы
    # можно было вставить строку Neon как есть.
    if _url.drivername == "postgresql":
        _url = _url.set(drivername="postgresql+asyncpg")
    _url = _url.difference_update_query(["sslmode", "channel_binding"])
    _connect_args["ssl"] = "require"

# pool_pre_ping — переподключение при «уснувших» соединениях (важно для Postgres).
engine = create_async_engine(
    _url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

# expire_on_commit=False — объекты остаются доступными после commit().
session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")      # параллельное чтение/запись
        cursor.execute("PRAGMA synchronous=NORMAL")     # быстрее, безопасно с WAL
        cursor.execute("PRAGMA foreign_keys=ON")        # каскадное удаление анкет
        cursor.close()


def _add_column_if_missing(sync_conn, table: str, column: str, ddl: str) -> bool:
    """Добавить колонку, если её ещё нет. Возвращает True, если добавили."""
    inspector = inspect(sync_conn)
    columns = {c["name"] for c in inspector.get_columns(table)}
    if column not in columns:
        sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        return True
    return False


def _light_migrations(sync_conn) -> None:
    """Мелкие идемпотентные миграции для уже существующих БД.

    create_all() создаёт только отсутствующие таблицы, но не меняет существующие,
    поэтому недостающие колонки добавляем вручную (данные при этом сохраняются).
    """
    # Кулдаун повторного показа анкеты.
    if _add_column_if_missing(
        sync_conn, "interactions", "updated_at", "updated_at DATETIME"
    ):
        sync_conn.execute(
            text("UPDATE interactions SET updated_at = created_at WHERE updated_at IS NULL")
        )

    # Новые поля анкеты: позиция (Dota 2), регион, дополнительные фото.
    _add_column_if_missing(sync_conn, "profiles", "position", "position VARCHAR(40) DEFAULT ''")
    _add_column_if_missing(sync_conn, "profiles", "region", "region VARCHAR(40) DEFAULT ''")
    _add_column_if_missing(sync_conn, "profiles", "extra_photos", "extra_photos TEXT DEFAULT ''")

    # Последняя активность пользователя — для скрытия давно не заходивших из ленты.
    if _add_column_if_missing(sync_conn, "users", "last_active", "last_active DATETIME"):
        sync_conn.execute(
            text("UPDATE users SET last_active = created_at WHERE last_active IS NULL")
        )

    # Регион СНГ: меняем флаг РФ на нейтральный белый (в странах СНГ — без политики).
    # Идемпотентно: после первого прогона старых значений уже не остаётся.
    for table in ("profiles", "search_filters"):
        sync_conn.execute(
            text(f"UPDATE {table} SET region = :new WHERE region = :old"),
            {"new": "🏳️ СНГ", "old": "🇷🇺 СНГ"},
        )


async def init_db() -> None:
    """Создать таблицы, если их ещё нет (идемпотентно)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_light_migrations)
