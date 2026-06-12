"""Inline-клавиатуры.

Каждая кнопка несёт в callback_data всё необходимое (действие, игру, id цели),
поэтому нажатия всегда однозначны — конфликтов кнопок не возникает.
Формат: части разделены ':'. game = 'cs2' | 'dota2' (без двоеточий).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import GENDER_FEMALE, GENDER_MALE
from utils.constants import DOTA_POSITIONS, MAX_PHOTOS, REGIONS, RANKS


def wizard_step_kb(
    step: str, game: str, can_back: bool = True
) -> InlineKeyboardMarkup | None:
    """Inline-клавиатура шага мастера анкеты (+ кнопка «Назад»).

    Для текстовых шагов (ник/возраст/о себе) содержит только «Назад». Шаг «Фото»
    управляется нижней reply-клавиатурой (create_photos_kb), поэтому здесь его нет.
    Возвращает None, если кнопок нет (первый шаг без «Назад»).
    """
    b = InlineKeyboardBuilder()
    if step == "gender":
        b.button(text="👨 Мужской", callback_data=f"cg:{GENDER_MALE}")
        b.button(text="👩 Женский", callback_data=f"cg:{GENDER_FEMALE}")
        b.adjust(2)
    elif step == "rank":
        for idx, label in enumerate(RANKS[game]):
            b.button(text=label, callback_data=f"cr:{idx}")
        b.adjust(2)
    elif step == "position":
        for idx, label in enumerate(DOTA_POSITIONS):
            b.button(text=label, callback_data=f"cpos:{idx}")
        b.adjust(1)
    elif step == "region":
        for idx, label in enumerate(REGIONS):
            b.button(text=label, callback_data=f"creg:{idx}")
        b.adjust(2)
    if can_back:
        b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="wback"))
    markup = b.as_markup()
    return markup if markup.inline_keyboard else None


def edit_photos_done_kb(count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"✅ Готово ({count}/{MAX_PHOTOS})", callback_data="ephdone")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
#  Создание анкеты
# --------------------------------------------------------------------------- #
def gender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Мужской", callback_data=f"cg:{GENDER_MALE}")
    builder.button(text="👩 Женский", callback_data=f"cg:{GENDER_FEMALE}")
    builder.adjust(2)
    return builder.as_markup()


def rank_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(RANKS[game]):
        builder.button(text=label, callback_data=f"cr:{idx}")
    builder.adjust(2)
    return builder.as_markup()


def position_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(DOTA_POSITIONS):
        builder.button(text=label, callback_data=f"cpos:{idx}")
    builder.adjust(1)
    return builder.as_markup()


def region_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(REGIONS):
        builder.button(text=label, callback_data=f"creg:{idx}")
    builder.adjust(2)
    return builder.as_markup()


def preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Опубликовать анкету", callback_data="cpub")
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
#  Лента анкет
# --------------------------------------------------------------------------- #
def browse_kb(
    target_id: int,
    game: str,
    photo_count: int = 1,
    photo_idx: int = 0,
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Лайк", callback_data=f"br:like:{target_id}:{game}")
    builder.button(text="💌 Сообщение", callback_data=f"br:msg:{target_id}:{game}")
    has_photos = photo_count > 1
    if has_photos:
        # Листание фото: показываем сколько всего и переходим к следующему.
        nxt = (photo_idx + 1) % photo_count
        builder.button(
            text=f"📷 Фото {photo_idx + 1}/{photo_count}",
            callback_data=f"br:ph:{target_id}:{game}:{nxt}",
        )
    builder.button(text="➡️ Дальше", callback_data=f"br:next:{game}")
    builder.button(text="🚫 Пожаловаться", callback_data=f"br:report:{target_id}:{game}")
    if is_admin:
        # Видна только администраторам — мгновенный бан анкеты прямо из ленты.
        builder.button(text="🔨 Забанить (админ)", callback_data=f"br:ban:{target_id}:{game}")
    builder.button(text="🔙 В меню", callback_data=f"br:stop:{game}")
    # Раскладка: [Лайк][Сообщ] / [Фото?] / [Дальше] / [Жалоба] / [Бан?] / [Меню]
    rows = [2]
    if has_photos:
        rows.append(1)
    rows += [1, 1]
    if is_admin:
        rows.append(1)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def like_response_kb(liker_id: int, game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❤️ Ответить взаимностью", callback_data=f"lk:yes:{liker_id}:{game}"
    )
    builder.button(text="👎 Пропустить", callback_data=f"lk:no:{liker_id}:{game}")
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
#  Моя анкета: редактирование
# --------------------------------------------------------------------------- #
def my_profile_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Имя", callback_data=f"ed:name:{game}")
    builder.button(text="🎂 Возраст", callback_data=f"ed:age:{game}")
    builder.button(text="📝 Описание", callback_data=f"ed:about:{game}")
    rank_label = "📈 FACEIT Elo" if game == "cs2" else "🏆 Ранг"
    builder.button(text=rank_label, callback_data=f"ed:rank:{game}")
    builder.button(text="🚻 Пол", callback_data=f"ed:gender:{game}")
    if game == "dota2":
        builder.button(text="🎯 Позиция", callback_data=f"ed:position:{game}")
    builder.button(text="🌍 Регион", callback_data=f"ed:region:{game}")
    builder.button(text="🖼 Фото", callback_data=f"ed:photo:{game}")
    builder.button(text="♻️ Пересоздать анкету", callback_data=f"ed:recreate:{game}")
    builder.button(text="🗑 Удалить анкету", callback_data=f"ed:delete:{game}")
    # Раскладка подстраивается под наличие кнопки «Позиция».
    if game == "dota2":
        builder.adjust(2, 2, 2, 2, 1, 1)
    else:
        builder.adjust(2, 2, 1, 2, 1, 1)
    return builder.as_markup()


def rank_edit_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(RANKS[game]):
        builder.button(text=label, callback_data=f"er:{idx}:{game}")
    builder.adjust(2)
    return builder.as_markup()


def gender_edit_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Мужской", callback_data=f"eg:{GENDER_MALE}:{game}")
    builder.button(text="👩 Женский", callback_data=f"eg:{GENDER_FEMALE}:{game}")
    builder.adjust(2)
    return builder.as_markup()


def position_edit_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(DOTA_POSITIONS):
        builder.button(text=label, callback_data=f"epos:{idx}:{game}")
    builder.adjust(1)
    return builder.as_markup()


def region_edit_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(REGIONS):
        builder.button(text=label, callback_data=f"ereg:{idx}:{game}")
    builder.adjust(2)
    return builder.as_markup()


def delete_confirm_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"del:yes:{game}")
    builder.button(text="↩️ Нет, оставить", callback_data=f"del:no:{game}")
    builder.adjust(2)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
#  Фильтры поиска
# --------------------------------------------------------------------------- #
def filters_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚻 Пол", callback_data=f"flt:gender:{game}")
    builder.button(text="🎂 Возраст", callback_data=f"flt:age:{game}")
    builder.button(text="🏆 Ранг", callback_data=f"flt:rank:{game}")
    builder.button(text="🌍 Регион", callback_data=f"flt:region:{game}")
    builder.button(text="♻️ Сбросить фильтры", callback_data=f"flt:reset:{game}")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def filter_gender_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Мужской", callback_data=f"fg:{GENDER_MALE}:{game}")
    builder.button(text="👩 Женский", callback_data=f"fg:{GENDER_FEMALE}:{game}")
    builder.button(text="Любой", callback_data=f"fg:any:{game}")
    builder.adjust(2, 1)
    return builder.as_markup()


def filter_region_kb(game: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, label in enumerate(REGIONS):
        builder.button(text=label, callback_data=f"freg:{idx}:{game}")
    builder.button(text="Любой", callback_data=f"freg:any:{game}")
    builder.adjust(2)
    return builder.as_markup()


def filter_rank_kb(game: str, which: str, selected_min: int | None = None) -> InlineKeyboardMarkup:
    """Выбор границы ранга. which='min' или 'max'."""
    builder = InlineKeyboardBuilder()
    start = selected_min if (which == "max" and selected_min is not None) else 0
    for idx, label in enumerate(RANKS[game]):
        if idx < start:
            continue
        builder.button(text=label, callback_data=f"frk:{which}:{idx}:{game}")
    builder.button(text="Без ограничения", callback_data=f"frk:{which}:any:{game}")
    builder.adjust(2)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
#  Админ-панель
# --------------------------------------------------------------------------- #
def admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="adm:stats")
    builder.button(text="🚩 Жалобы", callback_data="adm:reports")
    builder.button(text="🔨 Забанить", callback_data="adm:ban")
    builder.button(text="♻️ Разбанить", callback_data="adm:unban")
    builder.button(text="📢 Рассылка", callback_data="adm:broadcast")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админ-меню", callback_data="adm:home")
    builder.adjust(1)
    return builder.as_markup()


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить всем", callback_data="adm:bc_yes")
    builder.button(text="❌ Отмена", callback_data="adm:home")
    builder.adjust(1)
    return builder.as_markup()
