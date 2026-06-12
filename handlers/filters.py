"""Фильтры поиска: пол, возраст, ранг/эло, регион. Хранятся в БД на (user, game)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.queries import (
    get_filter,
    get_profile,
    reset_filter,
    update_filter_fields,
)
from keyboards.inline import (
    filter_gender_kb,
    filter_rank_kb,
    filter_region_kb,
    filters_kb,
)
from keyboards.reply import PREFIX_FILTERS, cancel_kb, game_menu_kb
from states.profile_states import FilterEdit
from utils.constants import (
    AGE_MAX,
    AGE_MIN,
    GAMES,
    GENDERS,
    RANKS,
    REGIONS,
    detect_game,
    game_name,
)

router = Router(name="filters")


def _summary(flt, game: str) -> str:
    label = GAMES[game]["rank_label"]
    gender = GENDERS.get(flt.gender) if (flt and flt.gender) else "любой"
    age = "любой"
    if flt and (flt.age_min is not None or flt.age_max is not None):
        lo = flt.age_min if flt.age_min is not None else AGE_MIN
        hi = flt.age_max if flt.age_max is not None else AGE_MAX
        age = f"{lo}–{hi}"
    rank = "любой"
    if flt and (flt.rank_min is not None or flt.rank_max is not None):
        ranks = RANKS[game]
        lo = ranks[flt.rank_min] if flt.rank_min is not None else ranks[0]
        hi = ranks[flt.rank_max] if flt.rank_max is not None else ranks[-1]
        rank = f"{lo} – {hi}"
    region = flt.region if (flt and flt.region) else "любой"
    return (
        "⚙️ <b>Фильтры поиска</b>\n\n"
        f"🚻 Пол: <b>{gender}</b>\n"
        f"🎂 Возраст: <b>{age}</b>\n"
        f"{GAMES[game]['rank_emoji']} {label}: <b>{rank}</b>\n"
        f"🌍 Регион: <b>{region}</b>\n\n"
        "Что настроим?"
    )


async def _send_menu(bot, chat_id, session, user_id, game) -> None:
    flt = await get_filter(session, user_id, game)
    await bot.send_message(chat_id, _summary(flt, game), reply_markup=filters_kb(game))


@router.message(F.text.startswith(PREFIX_FILTERS))
async def open_filters(message: Message, session: AsyncSession) -> None:
    game = detect_game(message.text)
    if await get_profile(session, message.from_user.id, game) is None:
        await message.answer(
            "Сначала создай анкету 🙂", reply_markup=game_menu_kb(game, has_profile=False)
        )
        return
    await _send_menu(message.bot, message.chat.id, session, message.from_user.id, game)


@router.callback_query(F.data.startswith("flt:"))
async def filters_router(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, action, game = cb.data.split(":")
    if action == "gender":
        await cb.message.edit_text("🚻 Кого показывать?", reply_markup=filter_gender_kb(game))
    elif action == "region":
        await cb.message.edit_text("🌍 Из какого региона?", reply_markup=filter_region_kb(game))
    elif action == "rank":
        label = GAMES[game]["rank_label"]
        await cb.message.edit_text(
            f"{label}: выбери НИЖНЮЮ границу:", reply_markup=filter_rank_kb(game, "min")
        )
    elif action == "age":
        await state.set_state(FilterEdit.age_min)
        await state.update_data(game=game)
        await cb.message.answer(
            "🎂 Минимальный возраст (число) или 0 — без ограничения:",
            reply_markup=cancel_kb(),
        )
    elif action == "reset":
        await reset_filter(session, cb.from_user.id, game)
        await session.commit()
        flt = await get_filter(session, cb.from_user.id, game)
        await cb.message.edit_text(_summary(flt, game), reply_markup=filters_kb(game))
        await cb.answer("Фильтры сброшены")
        return
    await cb.answer()


@router.callback_query(F.data.startswith("fg:"))
async def set_gender(cb: CallbackQuery, session: AsyncSession) -> None:
    _, value, game = cb.data.split(":")
    await update_filter_fields(
        session, cb.from_user.id, game, gender=None if value == "any" else value
    )
    await session.commit()
    flt = await get_filter(session, cb.from_user.id, game)
    await cb.message.edit_text(_summary(flt, game), reply_markup=filters_kb(game))
    await cb.answer("✅ Сохранено")


@router.callback_query(F.data.startswith("freg:"))
async def set_region(cb: CallbackQuery, session: AsyncSession) -> None:
    _, value, game = cb.data.split(":")
    region = None if value == "any" else REGIONS[int(value)]
    await update_filter_fields(session, cb.from_user.id, game, region=region)
    await session.commit()
    flt = await get_filter(session, cb.from_user.id, game)
    await cb.message.edit_text(_summary(flt, game), reply_markup=filters_kb(game))
    await cb.answer("✅ Сохранено")


@router.callback_query(F.data.startswith("frk:"))
async def set_rank(cb: CallbackQuery, session: AsyncSession) -> None:
    _, which, value, game = cb.data.split(":")
    if which == "min":
        rank_min = None if value == "any" else int(value)
        await update_filter_fields(session, cb.from_user.id, game, rank_min=rank_min)
        await session.commit()
        await cb.message.edit_text(
            f"{GAMES[game]['rank_label']}: теперь выбери ВЕРХНЮЮ границу:",
            reply_markup=filter_rank_kb(game, "max", selected_min=rank_min),
        )
        await cb.answer()
    else:  # max
        rank_max = None if value == "any" else int(value)
        await update_filter_fields(session, cb.from_user.id, game, rank_max=rank_max)
        await session.commit()
        flt = await get_filter(session, cb.from_user.id, game)
        await cb.message.edit_text(_summary(flt, game), reply_markup=filters_kb(game))
        await cb.answer("✅ Сохранено")


def _parse_age(text: str) -> int | None:
    """'0'/пусто → None (без ограничения); иначе число в пределах допустимого."""
    text = (text or "").strip()
    if not text.isdigit():
        return None
    val = int(text)
    if val == 0:
        return None
    return max(AGE_MIN, min(AGE_MAX, val))


@router.message(FilterEdit.age_min, F.text)
async def filter_age_min(message: Message, state: FSMContext) -> None:
    await state.update_data(age_min=_parse_age(message.text))
    await state.set_state(FilterEdit.age_max)
    await message.answer("🎂 Максимальный возраст (число) или 0 — без ограничения:")


@router.message(FilterEdit.age_max, F.text)
async def filter_age_max(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    game = data["game"]
    age_min = data.get("age_min")
    age_max = _parse_age(message.text)
    # Если перепутали местами — меняем, чтобы не было пустого диапазона.
    if age_min is not None and age_max is not None and age_min > age_max:
        age_min, age_max = age_max, age_min
    await update_filter_fields(
        session, message.from_user.id, game, age_min=age_min, age_max=age_max
    )
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Возрастной фильтр сохранён.",
        reply_markup=game_menu_kb(game, has_profile=True),
    )
    await _send_menu(message.bot, message.chat.id, session, message.from_user.id, game)
