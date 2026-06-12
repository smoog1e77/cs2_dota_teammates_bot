"""Reply-клавиатуры (нижнее меню навигации).

Тексты кнопок содержат название игры — благодаря этому обработчик всегда
однозначно понимает, к какой игре относится действие. Никаких конфликтов.
"""
from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from utils.constants import GAMES, game_name

# Постоянные подписи кнопок (используются и в клавиатурах, и в фильтрах хендлеров)
BTN_MAIN_MENU = "🔄 Главное меню"
BTN_HELP = "ℹ️ Помощь"
BTN_CANCEL = "❌ Отмена"
# Кнопки нижней клавиатуры на шаге «Фото» при создании анкеты.
BTN_PHOTOS_DONE = "✅ Готово"
BTN_BACK = "⬅️ Назад"

PREFIX_CREATE = "📝 Создать анкету"
PREFIX_BROWSE = "👀 Найти тиммейта"
PREFIX_MY = "👤 Моя анкета"
PREFIX_MATCHES = "💞 Взаимные симпатии"
PREFIX_FILTERS = "⚙️ Фильтры"


def main_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=f"{GAMES['cs2']['emoji']} {GAMES['cs2']['title']}"),
        KeyboardButton(text=f"{GAMES['dota2']['emoji']} {GAMES['dota2']['title']}"),
    )
    builder.row(KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True)


def game_menu_kb(game: str, has_profile: bool = True) -> ReplyKeyboardMarkup:
    """Меню игры. Состав кнопок зависит от того, есть ли у игрока анкета:

    - анкеты нет  → только «Создать анкету» (искать без анкеты нельзя);
    - анкета есть → «Найти тиммейта» и «Моя анкета», кнопки «Создать» уже нет.
    """
    name = game_name(game)
    builder = ReplyKeyboardBuilder()
    if has_profile:
        builder.row(KeyboardButton(text=f"{PREFIX_BROWSE} {name}"))
        builder.row(
            KeyboardButton(text=f"{PREFIX_MATCHES} {name}"),
            KeyboardButton(text=f"{PREFIX_FILTERS} {name}"),
        )
        builder.row(KeyboardButton(text=f"{PREFIX_MY} {name}"))
    else:
        builder.row(KeyboardButton(text=f"{PREFIX_CREATE} {name}"))
    builder.row(KeyboardButton(text=BTN_MAIN_MENU))
    return builder.as_markup(resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    """Единственная кнопка «Отмена» во время ввода анкеты — без лишних кнопок."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_CANCEL))
    return builder.as_markup(resize_keyboard=True)


def create_photos_kb() -> ReplyKeyboardMarkup:
    """Нижняя клавиатура шага «Фото»: «Готово» рядом с «Назад» и «Отмена».

    Кнопка подтверждения вынесена сюда (а не на inline под сообщением), чтобы она
    всегда была под рукой внизу экрана — там же, где и «Отмена».
    """
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_PHOTOS_DONE))
    builder.row(KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL))
    return builder.as_markup(resize_keyboard=True)
