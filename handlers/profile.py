"""Создание, просмотр и редактирование анкеты.

Анкета привязана к паре (пользователь, игра): данные CS2 и Dota 2 не смешиваются.
Создание идёт пошаговым мастером: прогресс «Шаг N/M», кнопка «⬅️ Назад» на каждом
шаге, мультизагрузка фото (до 3) и предпросмотр анкеты перед публикацией.
"""
from __future__ import annotations

from types import SimpleNamespace

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.queries import (
    create_or_update_profile,
    delete_profile,
    get_profile,
    update_profile_field,
    upsert_user,
)
from keyboards.inline import (
    delete_confirm_kb,
    edit_photos_done_kb,
    gender_edit_kb,
    my_profile_kb,
    position_edit_kb,
    preview_kb,
    rank_edit_kb,
    region_edit_kb,
    wizard_step_kb,
)
from keyboards.reply import (
    BTN_BACK,
    BTN_PHOTOS_DONE,
    PREFIX_CREATE,
    PREFIX_MY,
    cancel_kb,
    create_photos_kb,
    game_menu_kb,
)
from states.profile_states import ProfileCreation, ProfileEdit
from utils.constants import (
    ABOUT_MAX,
    AGE_MAX,
    AGE_MIN,
    DOTA_POSITIONS,
    GAMES,
    MAX_PHOTOS,
    NICK_MAX,
    RANKS,
    REGIONS,
    detect_game,
    game_name,
)
from utils.formatting import render_profile

router = Router(name="profile")


# --------------------------------------------------------------------------- #
#  Мастер создания анкеты: порядок шагов
# --------------------------------------------------------------------------- #
WIZARD_STEPS: dict[str, list[str]] = {
    "cs2": ["nickname", "gender", "age", "rank", "region", "about", "photos"],
    "dota2": ["nickname", "gender", "age", "rank", "position", "region", "about", "photos"],
}
STEP_STATE = {
    "nickname": ProfileCreation.nickname,
    "gender": ProfileCreation.gender,
    "age": ProfileCreation.age,
    "rank": ProfileCreation.rank,
    "position": ProfileCreation.position,
    "region": ProfileCreation.region,
    "about": ProfileCreation.about,
    "photos": ProfileCreation.photos,
}


def _step_text(step: str, idx: int, total: int, game: str) -> str:
    p = f"📝 <b>Шаг {idx + 1}/{total}</b>"
    if step == "nickname":
        return f"{p} · Никнейм\n\nВведи свой игровой ник:"
    if step == "gender":
        return f"{p} · Пол\n\nУкажи свой пол:"
    if step == "age":
        return f"{p} · Возраст\n\n🎂 Сколько тебе лет? Введи число:"
    if step == "rank":
        label = GAMES[game]["rank_label"]
        return f"{p} · {label}\n\nВыбери свой {label}:"
    if step == "position":
        return f"{p} · Позиция\n\n🎯 На каких позициях играешь в Dota 2?"
    if step == "region":
        return f"{p} · Регион\n\n🌍 Выбери свой регион:"
    if step == "about":
        return f"{p} · О себе\n\nРасскажи о себе (укажи свою роль в команде и кого ищешь):"
    if step == "photos":
        return (
            f"{p} · Фото\n\n🖼 Отправь от 1 до {MAX_PHOTOS} фото по одному сообщению.\n"
            "Когда будет достаточно — нажми «✅ Готово»."
        )
    return p


async def _show_step(bot: Bot, chat_id: int, state: FSMContext, step: str) -> None:
    data = await state.get_data()
    game = data["game"]
    steps = WIZARD_STEPS[game]
    idx = steps.index(step)
    can_back = idx > 0
    await state.set_state(STEP_STATE[step])
    await state.update_data(step=step)

    prev = data.get("wiz_msg_id")
    if prev:
        try:
            await bot.delete_message(chat_id, prev)
        except Exception:
            pass

    if step == "photos":
        # «Готово» и «Назад» вынесены на нижнюю reply-клавиатуру рядом с «Отмена».
        markup = create_photos_kb()
    else:
        markup = wizard_step_kb(step, game, can_back=can_back)
    sent = await bot.send_message(
        chat_id, _step_text(step, idx, len(steps), game), reply_markup=markup
    )
    await state.update_data(wiz_msg_id=sent.message_id)


def _next_step(game: str, current: str) -> str:
    steps = WIZARD_STEPS[game]
    i = steps.index(current)
    return steps[i + 1] if i + 1 < len(steps) else "preview"


async def _advance(bot: Bot, chat_id: int, state: FSMContext, current: str) -> None:
    data = await state.get_data()
    nxt = _next_step(data["game"], current)
    if nxt == "preview":
        await _show_preview(bot, chat_id, state)
    else:
        await _show_step(bot, chat_id, state, nxt)


async def _show_preview(bot: Bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    game = data["game"]
    photos = data.get("photos", [])
    await state.set_state(ProfileCreation.preview)
    await state.update_data(step="preview")

    prev = data.get("wiz_msg_id")
    if prev:
        try:
            await bot.delete_message(chat_id, prev)
        except Exception:
            pass

    ns = SimpleNamespace(
        nickname=data["nickname"],
        gender=data["gender"],
        age=data["age"],
        rank=data["rank"],
        position=data.get("position", ""),
        region=data.get("region", ""),
        about=data.get("about", ""),
        all_photos=photos,
        photo_id=photos[0] if photos else "",
    )
    caption = "👀 <b>Так будет выглядеть твоя анкета:</b>\n\n" + render_profile(ns, game)
    sent = await bot.send_photo(
        chat_id, photos[0], caption=caption, reply_markup=preview_kb()
    )
    await state.update_data(wiz_msg_id=sent.message_id)


async def _begin_creation(
    bot: Bot, chat_id: int, state: FSMContext, game: str, title: str
) -> None:
    await state.clear()
    await state.update_data(game=game, photos=[])
    await bot.send_message(chat_id, title, reply_markup=cancel_kb())
    await _show_step(bot, chat_id, state, "nickname")


# --------------------------------------------------------------------------- #
#  Показ собственной анкеты
# --------------------------------------------------------------------------- #
async def show_my_profile(
    bot: Bot, chat_id: int, session: AsyncSession, user_id: int, game: str
) -> None:
    profile = await get_profile(session, user_id, game)
    if profile is None:
        await bot.send_message(
            chat_id,
            "У тебя пока нет анкеты в этой игре. Нажми «📝 Создать анкету».",
            reply_markup=game_menu_kb(game, has_profile=False),
        )
        return
    await bot.send_photo(
        chat_id,
        profile.photo_id,
        caption=render_profile(profile, game),
        reply_markup=my_profile_kb(game),
    )


@router.message(F.text.startswith(PREFIX_MY))
async def my_profile(message: Message, session: AsyncSession) -> None:
    game = detect_game(message.text)
    await show_my_profile(message.bot, message.chat.id, session, message.from_user.id, game)


# --------------------------------------------------------------------------- #
#  Старт создания
# --------------------------------------------------------------------------- #
@router.message(F.text.startswith(PREFIX_CREATE))
async def create_start(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    game = detect_game(message.text)
    if await get_profile(session, message.from_user.id, game) is not None:
        await message.answer(
            "У тебя уже есть анкета для этой игры 👇\n"
            "Её можно изменить или пересоздать кнопками под анкетой.",
            reply_markup=game_menu_kb(game, has_profile=True),
        )
        await show_my_profile(
            message.bot, message.chat.id, session, message.from_user.id, game
        )
        return

    await upsert_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await session.commit()

    await _begin_creation(
        message.bot,
        message.chat.id,
        state,
        game,
        f"📝 Создаём анкету <b>{game_name(game)}</b>.\n"
        "На любом шаге можно вернуться кнопкой «⬅️ Назад» или выйти «❌ Отмена».",
    )


# --------------------------------------------------------------------------- #
#  Шаги мастера
# --------------------------------------------------------------------------- #
@router.message(ProfileCreation.nickname, F.text)
async def cr_nickname(message: Message, state: FSMContext) -> None:
    nick = (message.text or "").strip()
    if not 1 <= len(nick) <= NICK_MAX:
        await message.answer(f"Никнейм должен быть 1–{NICK_MAX} символов. Ещё раз:")
        return
    await state.update_data(nickname=nick)
    await _advance(message.bot, message.chat.id, state, "nickname")


@router.message(ProfileCreation.nickname)
async def cr_nickname_invalid(message: Message) -> None:
    await message.answer("Введи никнейм текстом:")


@router.callback_query(ProfileCreation.gender, F.data.startswith("cg:"))
async def cr_gender(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(gender=cb.data.split(":")[1])
    await _advance(cb.bot, cb.message.chat.id, state, "gender")
    await cb.answer()


@router.message(ProfileCreation.age, F.text)
async def cr_age(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not AGE_MIN <= int(text) <= AGE_MAX:
        await message.answer(f"Возраст числом от {AGE_MIN} до {AGE_MAX}. Ещё раз:")
        return
    await state.update_data(age=int(text))
    await _advance(message.bot, message.chat.id, state, "age")


@router.message(ProfileCreation.age)
async def cr_age_invalid(message: Message) -> None:
    await message.answer("Введи возраст числом, например 18:")


@router.callback_query(ProfileCreation.rank, F.data.startswith("cr:"))
async def cr_rank(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    ranks = RANKS[data["game"]]
    idx = int(cb.data.split(":")[1])
    if idx >= len(ranks):
        await cb.answer("Неверный выбор")
        return
    await state.update_data(rank=ranks[idx])
    await _advance(cb.bot, cb.message.chat.id, state, "rank")
    await cb.answer()


@router.callback_query(ProfileCreation.position, F.data.startswith("cpos:"))
async def cr_position(cb: CallbackQuery, state: FSMContext) -> None:
    idx = int(cb.data.split(":")[1])
    if idx >= len(DOTA_POSITIONS):
        await cb.answer("Неверный выбор")
        return
    await state.update_data(position=DOTA_POSITIONS[idx])
    await _advance(cb.bot, cb.message.chat.id, state, "position")
    await cb.answer()


@router.callback_query(ProfileCreation.region, F.data.startswith("creg:"))
async def cr_region(cb: CallbackQuery, state: FSMContext) -> None:
    idx = int(cb.data.split(":")[1])
    if idx >= len(REGIONS):
        await cb.answer("Неверный выбор")
        return
    await state.update_data(region=REGIONS[idx])
    await _advance(cb.bot, cb.message.chat.id, state, "region")
    await cb.answer()


@router.message(ProfileCreation.about, F.text)
async def cr_about(message: Message, state: FSMContext) -> None:
    await state.update_data(about=(message.text or "").strip()[:ABOUT_MAX])
    await _advance(message.bot, message.chat.id, state, "about")


@router.message(ProfileCreation.about)
async def cr_about_invalid(message: Message) -> None:
    await message.answer("Опиши себя текстом:")


@router.message(ProfileCreation.photos, F.photo)
async def cr_photo_add(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("photos", []))
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"Уже {MAX_PHOTOS} фото — это максимум. Жми «{BTN_PHOTOS_DONE}» внизу.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"📷 Готово, {MAX_PHOTOS}/{MAX_PHOTOS} фото. Жми «{BTN_PHOTOS_DONE}» внизу.")
    else:
        await message.answer(
            f"📷 Фото добавлено ({len(photos)}/{MAX_PHOTOS}). Ещё одно или «{BTN_PHOTOS_DONE}» внизу."
        )


@router.message(ProfileCreation.photos, F.text == BTN_PHOTOS_DONE)
async def cr_photos_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("photos"):
        await message.answer("Добавь хотя бы одно фото 🙂")
        return
    # Возвращаем обычную клавиатуру с «Отмена» перед предпросмотром.
    await message.answer("✅ Фото приняты.", reply_markup=cancel_kb())
    await _show_preview(message.bot, message.chat.id, state)


@router.message(ProfileCreation.photos, F.text == BTN_BACK)
async def cr_photos_back(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    game = data.get("game")
    if not game:
        await message.answer("Сессия истекла, начни заново.", reply_markup=cancel_kb())
        return
    steps = WIZARD_STEPS[game]
    target = steps[steps.index("photos") - 1]  # предыдущий шаг («О себе»)
    # Возвращаем «Отмена»-клавиатуру: на текстовом шаге «Готово»/«Назад» не нужны.
    await message.answer("⬅️ Назад.", reply_markup=cancel_kb())
    await _show_step(message.bot, message.chat.id, state, target)


@router.message(ProfileCreation.photos)
async def cr_photos_invalid(message: Message) -> None:
    await message.answer(f"Отправь фото 🖼 или нажми «{BTN_PHOTOS_DONE}» внизу.")


@router.callback_query(F.data == "wback")
async def cr_back(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    game = data.get("game")
    current = data.get("step")
    if not game or not current:
        await cb.answer("Сессия истекла, начни заново.", show_alert=True)
        return
    if current == "preview":
        target = "photos"
    else:
        steps = WIZARD_STEPS[game]
        i = steps.index(current)
        target = steps[i - 1] if i > 0 else current
    await _show_step(cb.bot, cb.message.chat.id, state, target)
    await cb.answer()


@router.callback_query(ProfileCreation.preview, F.data == "cpub")
async def cr_publish(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    game = data["game"]
    photos = data.get("photos", [])
    if not photos:
        await cb.answer("Добавь хотя бы одно фото.", show_alert=True)
        return
    await upsert_user(session, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await create_or_update_profile(
        session,
        user_id=cb.from_user.id,
        game=game,
        nickname=data["nickname"],
        gender=data["gender"],
        age=data["age"],
        rank=data["rank"],
        position=data.get("position", ""),
        region=data.get("region", ""),
        about=data.get("about", ""),
        photo_id=photos[0],
        extra_photos="\n".join(photos[1:]),
    )
    await session.commit()
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(
        "✅ Анкета опубликована!", reply_markup=game_menu_kb(game, has_profile=True)
    )
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)
    await cb.answer()


# --------------------------------------------------------------------------- #
#  Редактирование анкеты
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("ed:"))
async def on_edit(cb: CallbackQuery, state: FSMContext) -> None:
    _, action, game = cb.data.split(":")
    await cb.answer()
    if action == "name":
        await state.set_state(ProfileEdit.nickname)
        await state.update_data(game=game)
        await cb.message.answer("✏️ Введи новый никнейм:", reply_markup=cancel_kb())
    elif action == "age":
        await state.set_state(ProfileEdit.age)
        await state.update_data(game=game)
        await cb.message.answer("🎂 Введи новый возраст:", reply_markup=cancel_kb())
    elif action == "about":
        await state.set_state(ProfileEdit.about)
        await state.update_data(game=game)
        await cb.message.answer(
            "📝 Введи новое описание (роль в команде и кого ищешь):",
            reply_markup=cancel_kb(),
        )
    elif action == "photo":
        await state.set_state(ProfileEdit.photos)
        await state.update_data(game=game, ephotos=[])
        await cb.message.answer(
            f"🖼 Отправь 1–{MAX_PHOTOS} новых фото по одному. Затем нажми «✅ Готово».",
            reply_markup=cancel_kb(),
        )
        await cb.message.answer("Жду фото…", reply_markup=edit_photos_done_kb(0))
    elif action == "rank":
        label = GAMES[game]["rank_label"]
        await cb.message.answer(f"Выбери новый {label}:", reply_markup=rank_edit_kb(game))
    elif action == "gender":
        await cb.message.answer("Укажи пол:", reply_markup=gender_edit_kb(game))
    elif action == "position":
        await cb.message.answer("🎯 Выбери позицию:", reply_markup=position_edit_kb(game))
    elif action == "region":
        await cb.message.answer("🌍 Выбери регион:", reply_markup=region_edit_kb(game))
    elif action == "recreate":
        await _begin_creation(
            cb.bot,
            cb.message.chat.id,
            state,
            game,
            f"♻️ Пересоздаём анкету <b>{game_name(game)}</b>.\n"
            "На любом шаге — «⬅️ Назад» или «❌ Отмена».",
        )
    elif action == "delete":
        await cb.message.answer(
            "Точно удалить анкету? Действие необратимо.",
            reply_markup=delete_confirm_kb(game),
        )


async def _save_field_and_show(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    field: str,
    value,
    ok_text: str,
) -> None:
    data = await state.get_data()
    game = data["game"]
    await update_profile_field(session, message.from_user.id, game, field, value)
    await session.commit()
    await state.clear()
    await message.answer(ok_text, reply_markup=game_menu_kb(game, has_profile=True))
    await show_my_profile(message.bot, message.chat.id, session, message.from_user.id, game)


@router.message(ProfileEdit.nickname, F.text)
async def ed_nickname(message: Message, state: FSMContext, session: AsyncSession) -> None:
    nick = (message.text or "").strip()
    if not 1 <= len(nick) <= NICK_MAX:
        await message.answer(f"Никнейм 1–{NICK_MAX} символов. Ещё раз:")
        return
    await _save_field_and_show(message, state, session, "nickname", nick, "✅ Имя обновлено!")


@router.message(ProfileEdit.age, F.text)
async def ed_age(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not AGE_MIN <= int(text) <= AGE_MAX:
        await message.answer(f"Возраст числом от {AGE_MIN} до {AGE_MAX}. Ещё раз:")
        return
    await _save_field_and_show(message, state, session, "age", int(text), "✅ Возраст обновлён!")


@router.message(ProfileEdit.about, F.text)
async def ed_about(message: Message, state: FSMContext, session: AsyncSession) -> None:
    about = (message.text or "").strip()[:ABOUT_MAX]
    await _save_field_and_show(message, state, session, "about", about, "✅ Описание обновлено!")


@router.message(ProfileEdit.photos, F.photo)
async def ed_photo_add(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("ephotos", []))
    if len(photos) >= MAX_PHOTOS:
        await message.answer(f"Уже {MAX_PHOTOS} фото — максимум. Жми «✅ Готово».")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(ephotos=photos)
    await message.answer(
        f"📷 Фото {len(photos)}/{MAX_PHOTOS}. Ещё или «✅ Готово».",
        reply_markup=edit_photos_done_kb(len(photos)),
    )


@router.callback_query(ProfileEdit.photos, F.data == "ephdone")
async def ed_photos_done(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    photos = data.get("ephotos", [])
    game = data["game"]
    if not photos:
        await cb.answer("Добавь хотя бы одно фото 🙂", show_alert=True)
        return
    await update_profile_field(session, cb.from_user.id, game, "photo_id", photos[0])
    await update_profile_field(
        session, cb.from_user.id, game, "extra_photos", "\n".join(photos[1:])
    )
    await session.commit()
    await state.clear()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer("✅ Фото обновлены")
    await cb.message.answer("✅ Фото обновлены!", reply_markup=game_menu_kb(game, has_profile=True))
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)


@router.message(ProfileEdit.photos)
async def ed_photos_invalid(message: Message) -> None:
    await message.answer("Отправь фото 🖼 или нажми «✅ Готово».")


@router.callback_query(F.data.startswith("er:"))
async def ed_rank_choose(cb: CallbackQuery, session: AsyncSession) -> None:
    _, idx_s, game = cb.data.split(":")
    ranks = RANKS[game]
    idx = int(idx_s)
    if idx >= len(ranks):
        await cb.answer("Неверный выбор")
        return
    await update_profile_field(session, cb.from_user.id, game, "rank", ranks[idx])
    await session.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅ Обновлено")
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("eg:"))
async def ed_gender_choose(cb: CallbackQuery, session: AsyncSession) -> None:
    _, gender, game = cb.data.split(":")
    await update_profile_field(session, cb.from_user.id, game, "gender", gender)
    await session.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅ Пол обновлён")
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("epos:"))
async def ed_position_choose(cb: CallbackQuery, session: AsyncSession) -> None:
    _, idx_s, game = cb.data.split(":")
    idx = int(idx_s)
    if idx >= len(DOTA_POSITIONS):
        await cb.answer("Неверный выбор")
        return
    await update_profile_field(session, cb.from_user.id, game, "position", DOTA_POSITIONS[idx])
    await session.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅ Позиция обновлена")
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("ereg:"))
async def ed_region_choose(cb: CallbackQuery, session: AsyncSession) -> None:
    _, idx_s, game = cb.data.split(":")
    idx = int(idx_s)
    if idx >= len(REGIONS):
        await cb.answer("Неверный выбор")
        return
    await update_profile_field(session, cb.from_user.id, game, "region", REGIONS[idx])
    await session.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅ Регион обновлён")
    await show_my_profile(cb.bot, cb.message.chat.id, session, cb.from_user.id, game)


@router.callback_query(F.data.startswith("del:yes:"))
async def del_yes(cb: CallbackQuery, session: AsyncSession) -> None:
    game = cb.data.split(":")[2]
    await delete_profile(session, cb.from_user.id, game)
    await session.commit()
    await cb.message.edit_text("🗑 Анкета удалена.")
    await cb.message.answer(
        "Готово. Создать новую можно в любой момент.",
        reply_markup=game_menu_kb(game, has_profile=False),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("del:no:"))
async def del_no(cb: CallbackQuery) -> None:
    await cb.message.edit_text("Удаление отменено 👍")
    await cb.answer()
