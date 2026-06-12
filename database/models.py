"""ORM-модели (SQLAlchemy 2.0).

Три таблицы:
  * users        — пользователи Telegram (контакты для обмена при взаимности);
  * profiles     — анкеты. Уникальность (user_id, game) гарантирует, что у одного
                   пользователя только ОДНА анкета на игру и данные не «двоятся»;
  * interactions — действия (лайк / пропуск / жалоба). Уникальность
                   (actor_id, target_id, game) исключает повторный показ.

Все «горячие» выборки покрыты индексами — бот спокойно держит тысячи анкет.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Допустимые значения (хранятся строками — переносимо между SQLite и PostgreSQL)
GAME_CS2 = "cs2"
GAME_DOTA2 = "dota2"

GENDER_MALE = "male"
GENDER_FEMALE = "female"

ACTION_LIKE = "like"
ACTION_SKIP = "skip"
ACTION_REPORT = "report"


class User(Base):
    """Пользователь Telegram. id == telegram user id."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Последняя активность — по ней лента прячет давно не заходивших (вернутся, как зайдут).
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Profile(Base):
    """Анкета пользователя в конкретной игре."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    game: Mapped[str] = mapped_column(String(16), nullable=False)

    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[str] = mapped_column(String(40), nullable=False)
    # Позиция в Dota 2 (1–5). Для CS2 остаётся пустой.
    position: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    # Регион/сервер — используется фильтрами поиска.
    region: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    about: Mapped[str] = mapped_column(Text, default="", nullable=False)
    photo_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # Дополнительные фото (file_id через перевод строки), всего с основным до 3.
    extra_photos: Mapped[str] = mapped_column(Text, default="", nullable=False)

    likes_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    views_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Одна анкета на (пользователь, игра) — данные не «двоятся» на две игры.
        UniqueConstraint("user_id", "game", name="uq_user_game"),
        # Главный индекс для ленты: показываем активные анкеты по игре.
        Index("ix_profiles_game_active", "game", "is_active"),
    )

    @property
    def all_photos(self) -> list[str]:
        """Все фото анкеты: основное + дополнительные (по порядку)."""
        extra = [p for p in (self.extra_photos or "").split("\n") if p]
        return [self.photo_id, *extra]


class Interaction(Base):
    """Действие одного пользователя в адрес другого внутри одной игры."""

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # like / skip / report
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mutual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Время последнего действия по этой паре — по нему работает кулдаун
    # повторного показа анкеты в ленте (лайк/пропуск скрывают анкету на N минут).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Один итоговый вердикт на (actor, target, game) → анкета не повторяется.
        UniqueConstraint("actor_id", "target_id", "game", name="uq_actor_target_game"),
        # Исключение уже просмотренных в ленте (actor + game).
        Index("ix_interactions_actor_game", "actor_id", "game"),
        # Поиск входящих лайков и проверка взаимности (target + game + type).
        Index("ix_interactions_target_game_type", "target_id", "game", "type"),
    )


class SearchFilter(Base):
    """Фильтры поиска зрителя в конкретной игре (None = без ограничения)."""

    __tablename__ = "search_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game: Mapped[str] = mapped_column(String(16), nullable=False)

    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    age_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Границы ранга — индексы в списке RANKS[game] (включительно).
    rank_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[str | None] = mapped_column(String(40), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "game", name="uq_filter_user_game"),
    )
