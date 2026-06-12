"""Конфигурация бота. Все значения читаются из файла .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из переменных окружения / .env."""

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///bot.db"
    admin_ids: str = ""
    reports_to_hide: int = 5
    # Через сколько минут лайкнутая/пропущенная анкета снова попадётся в ленте.
    rebrowse_cooldown_minutes: int = 60
    # Анти-спам: максимум действий лайк/сообщение в минуту на пользователя.
    actions_per_minute: int = 20
    # Сколько дней анкета держится в разделе «Взаимные симпатии» (история мэтчей).
    matches_history_days: int = 2
    # Через сколько дней без активности анкета пропадает из ленты (вернётся, как зайдёт).
    feed_inactive_days: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def admins(self) -> list[int]:
        """Список ID администраторов из строки ADMIN_IDS."""
        return [int(x) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
