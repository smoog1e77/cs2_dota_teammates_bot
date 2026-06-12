"""Формирование текстов анкет, оценки совпадения и контактов."""
from __future__ import annotations

from html import escape

from database.models import User
from utils.constants import GAMES, GENDERS, RANKS


def compatibility_text(game: str, viewer_rank: str, target_rank: str) -> str:
    """Короткая подпись о близости рангов зрителя и анкеты."""
    ranks = RANKS[game]
    try:
        diff = abs(ranks.index(viewer_rank) - ranks.index(target_rank))
    except ValueError:
        return ""
    word = GAMES[game]["rank_word"]
    if diff == 0:
        return f"🎯 Идеальное совпадение {word}!"
    if diff == 1:
        return f"✅ Хорошее совпадение по {word}"
    if diff == 2:
        return f"📊 Заметная разница {word}"
    return f"📉 Значительная разница {word}"


def render_profile(
    profile,
    game: str,
    *,
    compatibility: str | None = None,
) -> str:
    """Текст карточки анкеты (HTML)."""
    g = GAMES[game]
    parts: list[str] = [f"{g['emoji']} <b>Анкета {g['name']}</b>"]

    if compatibility:
        parts.append(f"\n{compatibility}")

    parts.append(
        "\n"
        f"👤 <b>Никнейм:</b> {escape(profile.nickname)}\n"
        f"👫 <b>Пол:</b> {GENDERS.get(profile.gender, '—')}\n"
        f"🎂 <b>Возраст:</b> {profile.age}"
    )
    parts.append(
        f"\n{g['rank_emoji']} <b>{g['rank_label']}:</b> {escape(profile.rank)}"
    )
    # Позиция показывается только если задана (актуально для Dota 2).
    if getattr(profile, "position", ""):
        parts.append(f"\n🎯 <b>Позиция:</b> {escape(profile.position)}")
    if getattr(profile, "region", ""):
        parts.append(f"\n🌍 <b>Регион:</b> {escape(profile.region)}")
    if len(getattr(profile, "all_photos", [None])) > 1:
        parts.append(f"\n📷 <b>Фото:</b> {len(profile.all_photos)}")
    parts.append(f"\n📝 <b>О себе:</b>\n{escape(profile.about) or '—'}")

    return "\n".join(parts)


def contact_link(user: User | None, fallback_id: int) -> str:
    """Кликабельный контакт: @username или упоминание по id."""
    if user and user.username:
        return f"@{user.username}"
    name = escape(user.first_name) if user and user.first_name else "пользователь"
    uid = user.id if user else fallback_id
    return f'<a href="tg://user?id={uid}">{name}</a>'
