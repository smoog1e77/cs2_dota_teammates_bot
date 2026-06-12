"""Навигация: /start, главное меню, выбор игры, помощь, отмена.

Этот роутер подключается ПЕРВЫМ, поэтому его кнопки имеют приоритет над
обработчиками ввода в FSM — пользователь всегда может выйти в меню.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.queries import get_profile, upsert_user
from keyboards.reply import (
    BTN_CANCEL,
    BTN_HELP,
    BTN_MAIN_MENU,
    game_menu_kb,
    main_menu_kb,
)
from utils.constants import GAMES, HELP_TEXT, WELCOME

router = Router(name="start")

# Кнопки выбора игры из главного меню: "🔫 Counter-Strike 2" / "🎮 Dota 2"
GAME_TITLE_BUTTONS: dict[str, str] = {
    f"{g['emoji']} {g['title']}": code for code, g in GAMES.items()
}


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await upsert_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await session.commit()
    await message.answer(WELCOME, reply_markup=main_menu_kb())


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def show_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(F.text == BTN_MAIN_MENU)
async def to_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🏠 Главное меню. Выбери игру:", reply_markup=main_menu_kb())


@router.message(F.text.in_(set(GAME_TITLE_BUTTONS)))
async def choose_game(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    game = GAME_TITLE_BUTTONS[message.text]
    g = GAMES[game]
    has_profile = await get_profile(session, message.from_user.id, game) is not None
    await message.answer(
        f"{g['emoji']} <b>{g['title']}</b>\nЧто делаем?",
        reply_markup=game_menu_kb(game, has_profile),
    )


@router.message(F.text == BTN_CANCEL)
@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    game = data.get("game")
    await state.clear()
    if game:
        has_profile = await get_profile(session, message.from_user.id, game) is not None
        await message.answer("Отменено.", reply_markup=game_menu_kb(game, has_profile))
    else:
        await message.answer("Отменено.", reply_markup=main_menu_kb())
