"""Админ-панель: статистика, жалобы, бан/разбан, рассылка.

Доступ только для ID из ADMIN_IDS (.env). Роутер целиком закрыт фильтром IsAdmin.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.queries import (
    get_all_user_ids,
    get_profile,
    get_stats,
    get_top_reported,
    set_banned,
)
from keyboards.inline import admin_back_kb, admin_kb, broadcast_confirm_kb
from states.profile_states import AdminFSM
from utils.constants import game_name
from utils.sending import safe_send

logger = logging.getLogger("admin")
router = Router(name="admin")


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in settings.admins


# Весь роутер доступен только администраторам.
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("admin"))
async def admin_home(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_kb())


@router.callback_query(F.data == "adm:home")
async def adm_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:stats")
async def adm_stats(cb: CallbackQuery, session: AsyncSession) -> None:
    s = await get_stats(session)
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"⛔ Забанено: <b>{s['banned']}</b>\n\n"
        f"📋 Анкеты CS2: <b>{s['profiles_cs2']}</b>\n"
        f"📋 Анкеты Dota 2: <b>{s['profiles_dota2']}</b>\n"
        f"✅ Активных анкет: <b>{s['profiles_active']}</b>\n\n"
        f"❤️ Лайков всего: <b>{s['likes']}</b>\n"
        f"🎉 Взаимных мэтчей: <b>{s['matches']}</b>\n"
        f"🚩 Жалоб: <b>{s['reports']}</b>"
    )
    await cb.message.edit_text(text, reply_markup=admin_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:reports")
async def adm_reports(cb: CallbackQuery, session: AsyncSession) -> None:
    rows = await get_top_reported(session, limit=10)
    if not rows:
        await cb.message.edit_text("🚩 Жалоб пока нет.", reply_markup=admin_back_kb())
        await cb.answer()
        return
    lines = ["🚩 <b>Топ анкет по жалобам</b>\n"]
    for target_id, game, cnt in rows:
        profile = await get_profile(session, target_id, game)
        nick = profile.nickname if profile else "—"
        lines.append(f"• <code>{target_id}</code> ({game_name(game)}) — {nick}: {cnt} 🚩")
    lines.append("\nЗабанить: «🔨 Забанить» → введи ID.")
    await cb.message.edit_text("\n".join(lines), reply_markup=admin_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:ban")
async def adm_ban_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.ban)
    await cb.message.edit_text("🔨 Введи <b>user_id</b> для бана:", reply_markup=admin_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:unban")
async def adm_unban_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.unban)
    await cb.message.edit_text("♻️ Введи <b>user_id</b> для разбана:", reply_markup=admin_back_kb())
    await cb.answer()


@router.message(AdminFSM.ban, F.text)
async def adm_ban_do(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _do_ban(message, state, session, banned=True)


@router.message(AdminFSM.unban, F.text)
async def adm_unban_do(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _do_ban(message, state, session, banned=False)


async def _do_ban(
    message: Message, state: FSMContext, session: AsyncSession, banned: bool
) -> None:
    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await message.answer("Нужно число — user_id. Ещё раз:")
        return
    if banned and int(text) in settings.admins:
        await message.answer("Нельзя забанить администратора.", reply_markup=admin_kb())
        await state.clear()
        return
    ok = await set_banned(session, int(text), banned)
    await session.commit()
    await state.clear()
    word = "забанен" if banned else "разбанен"
    if ok:
        await message.answer(f"✅ Пользователь <code>{text}</code> {word}.", reply_markup=admin_kb())
    else:
        await message.answer("Пользователь с таким ID не найден.", reply_markup=admin_kb())


# --------------------------------------------------------------------------- #
#  Рассылка
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "adm:broadcast")
async def adm_broadcast_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.broadcast)
    await cb.message.edit_text(
        "📢 Пришли текст рассылки (поддерживается HTML):", reply_markup=admin_back_kb()
    )
    await cb.answer()


@router.message(AdminFSM.broadcast, F.text)
async def adm_broadcast_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(broadcast_text=message.html_text)
    await message.answer(
        f"📢 <b>Предпросмотр рассылки:</b>\n\n{message.html_text}\n\nОтправить всем?",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(F.data == "adm:bc_yes")
async def adm_broadcast_send(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    text = data.get("broadcast_text")
    await state.clear()
    if not text:
        await cb.message.edit_text("Текст рассылки потерян, повтори.", reply_markup=admin_kb())
        await cb.answer()
        return
    await cb.message.edit_text("📢 Рассылка запущена…")
    await cb.answer()

    user_ids = await get_all_user_ids(session)
    sent, failed = 0, 0
    for uid in user_ids:
        ok = await safe_send(
            lambda uid=uid: cb.bot.send_message(uid, text),
            logger=logger,
            descr=f"broadcast → {uid}",
        )
        if ok is not None:
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 сообщений/сек — в пределах лимитов Telegram
    logger.info("Broadcast finished: sent=%s failed=%s", sent, failed)
    await cb.bot.send_message(
        cb.message.chat.id,
        f"✅ Рассылка завершена.\nОтправлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>",
        reply_markup=admin_kb(),
    )
