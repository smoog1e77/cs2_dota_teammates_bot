"""Лента анкет: лайк, лайк+сообщение, пропуск, жалоба и взаимная симпатия.

Лента работает по принципу «одна активная карточка»: перед показом следующей
анкеты предыдущая удаляется. Поэтому «устаревших» кнопок не остаётся и нажатия
никогда не конфликтуют. Каждая inline-кнопка несёт игру и id цели в callback_data.
"""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InputMediaPhoto,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import ACTION_REPORT, ACTION_SKIP
from database.queries import (
    LikeResult,
    count_reports,
    deactivate_profile,
    get_next_profile,
    get_profile,
    get_recent_matches,
    get_user,
    increment_views,
    is_matches_unlocked,
    record_like,
    record_simple_interaction,
    set_banned,
    set_matches_unlocked,
    upsert_user,
)
from keyboards.inline import browse_kb, like_response_kb
from keyboards.reply import PREFIX_BROWSE, PREFIX_MATCHES, game_menu_kb
from states.profile_states import Browse
from utils.constants import GAMES, MESSAGE_MAX, detect_game, game_name
from utils.formatting import compatibility_text, contact_link, render_profile
from utils.sending import safe_send
from utils.throttling import like_limiter

router = Router(name="browse")
logger = logging.getLogger("browse")


def _throttle_msg(user_id: int) -> str:
    return f"⏳ Слишком много действий. Подожди {like_limiter.retry_after(user_id)} сек."


# --------------------------------------------------------------------------- #
#  Показ следующей анкеты
# --------------------------------------------------------------------------- #
async def _send_next_profile(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    viewer_id: int,
    game: str,
) -> None:
    data = await state.get_data()

    # Удаляем предыдущую карточку — на экране всегда одна активная анкета.
    prev_id = data.get("browse_msg_id")
    if prev_id:
        try:
            await bot.delete_message(chat_id, prev_id)
        except Exception:
            pass

    target = await get_next_profile(session, viewer_id, game)
    if target is None:
        await state.update_data(browse_msg_id=None, current_target=None)
        await state.set_state(None)
        await bot.send_message(
            chat_id,
            "😔 Анкеты закончились. Загляни позже — появятся новые игроки!",
            reply_markup=game_menu_kb(game, has_profile=True),
        )
        return

    await increment_views(session, target.id)
    await session.commit()

    viewer = await get_profile(session, viewer_id, game)
    comp = compatibility_text(game, viewer.rank, target.rank) if viewer else ""
    photos = target.all_photos
    is_admin = viewer_id in settings.admins
    caption = render_profile(target, game, compatibility=comp)
    if is_admin:
        caption += f"\n\n🛠 <i>ID для бана:</i> <code>{target.user_id}</code>"
    sent = await bot.send_photo(
        chat_id,
        photos[0],
        caption=caption,
        reply_markup=browse_kb(
            target.user_id, game, photo_count=len(photos), photo_idx=0, is_admin=is_admin
        ),
    )
    await state.update_data(browse_msg_id=sent.message_id, current_target=target.user_id)


@router.callback_query(F.data.startswith("br:ph:"))
async def cb_photo_cycle(cb: CallbackQuery, session: AsyncSession) -> None:
    """Листание фото анкеты в ленте через редактирование медиа."""
    _, _, target_s, game, idx_s = cb.data.split(":")
    target = await get_profile(session, int(target_s), game)
    if target is None:
        await cb.answer()
        return
    photos = target.all_photos
    idx = int(idx_s) % len(photos)
    viewer = await get_profile(session, cb.from_user.id, game)
    comp = compatibility_text(game, viewer.rank, target.rank) if viewer else ""
    is_admin = cb.from_user.id in settings.admins
    caption = render_profile(target, game, compatibility=comp)
    if is_admin:
        caption += f"\n\n🛠 <i>ID для бана:</i> <code>{target.user_id}</code>"
    try:
        await cb.message.edit_media(
            InputMediaPhoto(media=photos[idx], caption=caption),
            reply_markup=browse_kb(target.user_id, game, len(photos), idx, is_admin=is_admin),
        )
    except Exception:
        pass
    await cb.answer()


@router.message(F.text.startswith(PREFIX_BROWSE))
async def start_browse(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    game = detect_game(message.text)
    if await get_profile(session, message.from_user.id, game) is None:
        await message.answer(
            "Сначала создай анкету — без неё нельзя смотреть ленту 🙂",
            reply_markup=game_menu_kb(game, has_profile=False),
        )
        return
    await state.set_state(Browse.browsing)
    await state.update_data(game=game, browse_msg_id=None, current_target=None)
    await message.answer(
        f"🔎 Ищем тиммейтов в <b>{game_name(game)}</b>…",
        reply_markup=game_menu_kb(game, has_profile=True),
    )
    await _send_next_profile(
        message.bot, message.chat.id, state, session, message.from_user.id, game
    )


# --------------------------------------------------------------------------- #
#  Уведомления о лайках
# --------------------------------------------------------------------------- #
async def notify_like(
    bot: Bot,
    session: AsyncSession,
    liker_id: int,
    target_id: int,
    game: str,
    message_text: str | None,
) -> None:
    liker = await get_profile(session, liker_id, game)
    if liker is None:
        return
    g = GAMES[game]
    caption = f"❤️ <b>Твоя анкета {g['name']} кому-то понравилась!</b>\n"
    if message_text:
        caption += f"\n💌 <b>Сообщение:</b>\n«{escape(message_text)}»\n"
    # Полная карточка лайкнувшего: ник, пол, возраст, ранг, регион, о себе
    # (а для Dota 2 ещё и позиция) — render_profile сам подставляет всё, что есть.
    caption += "\n" + render_profile(liker, game) + "\n\nХочешь ответить взаимностью?"
    await safe_send(
        lambda: bot.send_photo(
            target_id,
            liker.photo_id,
            caption=caption,
            reply_markup=like_response_kb(liker_id, game),
        ),
        logger=logger,
        descr=f"notify_like → {target_id}",
    )


async def _send_match(
    bot: Bot, to_id: int, other_profile, other_user, other_id: int, game: str
) -> None:
    g = GAMES[game]
    header = f"🎉 <b>Взаимная симпатия в {g['name']}!</b>\nВы понравились друг другу 🎮\n"
    contact = f"\n📨 <b>Контакт:</b> {contact_link(other_user, other_id)}\n"
    footer = "\nНапиши первым и зовите в каток! 🎮"
    if other_profile:
        # Полная карточка анкеты тиммейта: ник, пол, возраст, ранг, о себе.
        caption = header + "\n" + render_profile(other_profile, game) + "\n" + contact + footer
        await safe_send(
            lambda: bot.send_photo(to_id, other_profile.photo_id, caption=caption),
            logger=logger,
            descr=f"notify_mutual → {to_id}",
        )
    else:
        await safe_send(
            lambda: bot.send_message(to_id, header + contact + footer),
            logger=logger,
            descr=f"notify_mutual → {to_id}",
        )


async def notify_mutual(
    bot: Bot, session: AsyncSession, a_id: int, b_id: int, game: str
) -> None:
    a_user = await get_user(session, a_id)
    b_user = await get_user(session, b_id)
    a_profile = await get_profile(session, a_id, game)
    b_profile = await get_profile(session, b_id, game)
    await _send_match(bot, a_id, b_profile, b_user, b_id, game)
    await _send_match(bot, b_id, a_profile, a_user, a_id, game)


async def _post_like_notify(
    bot: Bot,
    session: AsyncSession,
    actor_id: int,
    target_id: int,
    game: str,
    result: LikeResult,
    message_text: str | None,
) -> None:
    if result.is_mutual:
        await notify_mutual(bot, session, actor_id, target_id, game)
    elif result.status == "liked" or (message_text and result.status == "already_liked"):
        await notify_like(bot, session, actor_id, target_id, game, message_text)


# --------------------------------------------------------------------------- #
#  Действия в ленте
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("br:like:"))
async def cb_like(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, _, target_s, game = cb.data.split(":")
    target_id = int(target_s)
    if not like_limiter.allow(cb.from_user.id):
        await cb.answer(_throttle_msg(cb.from_user.id), show_alert=True)
        return
    await upsert_user(session, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    result = await record_like(session, cb.from_user.id, target_id, game)
    await session.commit()
    await cb.answer("🎉 Взаимность!" if result.is_mutual else "❤️ Лайк отправлен!")
    await _post_like_notify(cb.bot, session, cb.from_user.id, target_id, game, result, None)
    await _send_next_profile(cb.bot, cb.message.chat.id, state, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("br:msg:"))
async def cb_message_start(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, target_s, game = cb.data.split(":")
    # Снимаем кнопки с текущей карточки, чтобы не нажали повторно во время ввода.
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.set_state(Browse.awaiting_message)
    prompt = await cb.message.answer("✍️ Напиши сообщение — оно уйдёт вместе с лайком:")
    await state.update_data(
        msg_target=int(target_s), msg_game=game, msg_prompt_id=prompt.message_id
    )
    await cb.answer()


@router.message(Browse.awaiting_message, F.text)
async def cb_message_receive(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    target_id = data.get("msg_target")
    game = data.get("msg_game")
    if target_id is None or game is None:
        await state.set_state(Browse.browsing)
        return

    if not like_limiter.allow(message.from_user.id):
        await message.answer(_throttle_msg(message.from_user.id))
        return

    text = (message.text or "").strip()[:MESSAGE_MAX]
    await upsert_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name)
    result = await record_like(session, message.from_user.id, target_id, game, message=text)
    await session.commit()

    prompt_id = data.get("msg_prompt_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass

    await message.answer(
        "🎉 Взаимность!" if result.is_mutual else "✅ Сообщение и лайк отправлены!"
    )
    await _post_like_notify(
        message.bot, session, message.from_user.id, target_id, game, result, text
    )
    await state.set_state(Browse.browsing)
    await state.update_data(msg_target=None, msg_game=None, msg_prompt_id=None)
    await _send_next_profile(
        message.bot, message.chat.id, state, session, message.from_user.id, game
    )


@router.message(Browse.awaiting_message)
async def cb_message_invalid(message: Message) -> None:
    await message.answer("Отправь сообщение текстом или вернись в меню кнопкой ниже.")


@router.callback_query(F.data.startswith("br:next:"))
async def cb_next(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    game = cb.data.split(":")[2]
    data = await state.get_data()
    current = data.get("current_target")
    if current:
        await record_simple_interaction(session, cb.from_user.id, current, game, ACTION_SKIP)
        await session.commit()
    await cb.answer()
    await _send_next_profile(cb.bot, cb.message.chat.id, state, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("br:report:"))
async def cb_report(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, _, target_s, game = cb.data.split(":")
    target_id = int(target_s)
    await record_simple_interaction(session, cb.from_user.id, target_id, game, ACTION_REPORT)
    if await count_reports(session, target_id, game) >= settings.reports_to_hide:
        await deactivate_profile(session, target_id, game)
    await session.commit()
    await cb.answer("🚫 Жалоба отправлена. Спасибо!")
    await _send_next_profile(cb.bot, cb.message.chat.id, state, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("br:ban:"))
async def cb_admin_ban(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Мгновенный бан анкеты из ленты — только для администраторов."""
    _, _, target_s, game = cb.data.split(":")
    if cb.from_user.id not in settings.admins:
        await cb.answer("Только для администраторов.", show_alert=True)
        return
    target_id = int(target_s)
    if target_id in settings.admins:
        await cb.answer("Нельзя забанить администратора.", show_alert=True)
        return
    await set_banned(session, target_id, True)  # бан + скрытие всех его анкет
    await session.commit()
    await cb.answer(f"🔨 Пользователь {target_id} забанен.", show_alert=True)
    await _send_next_profile(cb.bot, cb.message.chat.id, state, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("br:stop:"))
async def cb_stop(cb: CallbackQuery, state: FSMContext) -> None:
    game = cb.data.split(":")[2]
    try:
        await cb.message.delete()
    except Exception:
        pass
    await state.set_state(None)
    await state.update_data(browse_msg_id=None, current_target=None)
    await cb.message.answer("Вышли из ленты 👋", reply_markup=game_menu_kb(game, has_profile=True))
    await cb.answer()


# --------------------------------------------------------------------------- #
#  «Взаимные симпатии» — история мэтчей за последние дни
# --------------------------------------------------------------------------- #
MATCHES_LIMIT = 10  # сколько карточек показываем за один раз (свежие — первыми)


@router.message(F.text.startswith(PREFIX_MATCHES))
async def start_matches(message: Message, session: AsyncSession) -> None:
    game = detect_game(message.text)
    if await get_profile(session, message.from_user.id, game) is None:
        await message.answer(
            "Сначала создай анкету 🙂",
            reply_markup=game_menu_kb(game, has_profile=False),
        )
        return
    # Платный доступ (если включён и ещё не оплачен) — показываем счёт на звёзды.
    if settings.matches_paid and not await is_matches_unlocked(session, message.from_user.id):
        await message.answer_invoice(
            title="💞 Взаимные симпатии",
            description=(
                "Разовая разблокировка навсегда: смотри всех, у кого с тобой взаимная "
                "симпатия, и сразу получай их контакты."
            ),
            payload="unlock_matches",
            provider_token="",  # для Telegram Stars платёжный токен не нужен
            currency="XTR",
            prices=[LabeledPrice(label="Доступ навсегда", amount=settings.matches_price_stars)],
        )
        return
    matches = await get_recent_matches(session, message.from_user.id, game)
    if not matches:
        await message.answer(
            "💞 Пока нет взаимных симпатий за последние дни.\n"
            "Лайкай анкеты в ленте — при взаимности они появятся здесь!",
            reply_markup=game_menu_kb(game, has_profile=True),
        )
        return
    await message.answer(
        f"💞 <b>Твои взаимные симпатии</b> ({len(matches)}) 👇\n"
        "Кого можно звать в каток прямо сейчас:"
    )
    for profile, user in matches[:MATCHES_LIMIT]:
        caption = (
            render_profile(profile, game)
            + f"\n\n📨 <b>Контакт:</b> {contact_link(user, profile.user_id)}"
        )
        try:
            await message.bot.send_photo(message.chat.id, profile.photo_id, caption=caption)
        except Exception:
            pass
        await asyncio.sleep(0.05)  # бережём лимиты Telegram при списке карточек
    if len(matches) > MATCHES_LIMIT:
        await message.answer(
            f"…и ещё {len(matches) - MATCHES_LIMIT}. Показал самые свежие — загляни позже.",
            reply_markup=game_menu_kb(game, has_profile=True),
        )


# --------------------------------------------------------------------------- #
#  Оплата «Взаимных симпатий» за Telegram Stars
# --------------------------------------------------------------------------- #
@router.pre_checkout_query()
async def matches_pre_checkout(pcq: PreCheckoutQuery) -> None:
    # Обязательный шаг Telegram: подтверждаем, что готовы принять оплату.
    await pcq.answer(ok=True)


@router.message(F.successful_payment)
async def matches_payment_ok(message: Message, session: AsyncSession) -> None:
    await set_matches_unlocked(session, message.from_user.id)
    await session.commit()
    logger.info("matches unlocked (оплата) для %s", message.from_user.id)
    await message.answer(
        "✅ <b>Доступ открыт навсегда!</b>\n"
        "Загляни в «💞 Взаимные симпатии» — теперь всё видно."
    )


# --------------------------------------------------------------------------- #
#  Ответ на лайк (из уведомления «твоя анкета понравилась»)
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("lk:yes:"))
async def cb_like_yes(cb: CallbackQuery, session: AsyncSession) -> None:
    _, _, liker_s, game = cb.data.split(":")
    liker_id = int(liker_s)
    if not like_limiter.allow(cb.from_user.id):
        await cb.answer(_throttle_msg(cb.from_user.id), show_alert=True)
        return
    await upsert_user(session, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    result = await record_like(session, cb.from_user.id, liker_id, game)
    await session.commit()
    try:
        await cb.message.delete()
    except Exception:
        pass
    if result.is_mutual:
        await notify_mutual(cb.bot, session, cb.from_user.id, liker_id, game)
        await cb.answer("🎉 Взаимность! Анкета — в разделе «Взаимные симпатии».")
    else:
        await notify_like(cb.bot, session, cb.from_user.id, liker_id, game, None)
        await cb.answer("❤️ Лайк отправлен!")


@router.callback_query(F.data.startswith("lk:no:"))
async def cb_like_no(cb: CallbackQuery, session: AsyncSession) -> None:
    _, _, liker_s, game = cb.data.split(":")
    await record_simple_interaction(session, cb.from_user.id, int(liker_s), game, ACTION_SKIP)
    await session.commit()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer("Пропущено")
