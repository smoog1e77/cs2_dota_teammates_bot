"""Смоук-тест ядра бота БЕЗ Telegram.

Проверяет то, что обещает ТЗ: раздельные анкеты по играм, лента (не видишь себя
и уже просмотренных), лайк → взаимность, лайк с сообщением, пропуск, жалобы со
скрытием анкеты, оценку совпадения по рангу.

Запуск:  .venv/bin/python -m tests.test_smoke
Использует отдельную временную SQLite-базу, реальную БД бота не трогает.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

# Своя временная БД, чтобы не задеть bot.db. Должно быть выставлено ДО импорта
# config/engine, которые читают переменные окружения.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"

from datetime import timedelta  # noqa: E402

from sqlalchemy import update  # noqa: E402

from database.engine import engine, init_db, session_maker  # noqa: E402
from database.models import GAME_CS2, GAME_DOTA2, GENDER_MALE, Interaction, User  # noqa: E402
from database import queries as q  # noqa: E402
from database.queries import _utcnow  # noqa: E402
from utils.formatting import compatibility_text, render_profile  # noqa: E402

PASSED = 0


def check(label: str, condition: bool) -> None:
    global PASSED
    mark = "✅" if condition else "❌"
    print(f"{mark} {label}")
    assert condition, f"ПРОВАЛ: {label}"
    PASSED += 1


async def _make_profile(session, uid: int, game: str, rank: str, nick: str):
    await q.upsert_user(session, uid, f"user{uid}", nick)
    await q.create_or_update_profile(
        session, user_id=uid, game=game, nickname=nick,
        gender=GENDER_MALE, age=20, rank=rank, about="ищу тиммейтов",
        photo_id="PHOTO_FILE_ID",
    )
    await session.commit()


async def main() -> None:
    await init_db()

    async with session_maker() as s:
        # --- Раздельные анкеты по играм -----------------------------------
        await _make_profile(s, 1, GAME_CS2, "1500–2000", "Alice")
        await _make_profile(s, 1, GAME_DOTA2, "3000–4000", "Alice")
        cs = await q.get_profile(s, 1, GAME_CS2)
        dota = await q.get_profile(s, 1, GAME_DOTA2)
        check("у одного юзера две РАЗНЫЕ анкеты по разным играм", cs.rank == "1500–2000" and dota.rank == "3000–4000")
        check("анкеты не двоятся: CS2-анкета не лезет в Dota2", cs.game == GAME_CS2 and dota.game == GAME_DOTA2)

        # «Пересоздание» CS2-анкеты не плодит дубль (UNIQUE user_id+game)
        await q.create_or_update_profile(
            s, user_id=1, game=GAME_CS2, nickname="Alice2",
            gender=GENDER_MALE, age=21, rank="2500+", about="новое",
            photo_id="PHOTO2",
        )
        await s.commit()
        cs2_again = await q.get_profile(s, 1, GAME_CS2)
        check("пересоздание анкеты обновляет ту же запись (без дублей)", cs2_again.nickname == "Alice2" and cs2_again.rank == "2500+")
        # Dota-анкета при этом не тронута
        dota_after = await q.get_profile(s, 1, GAME_DOTA2)
        check("правка CS2 не задела Dota2-анкету", dota_after.nickname == "Alice")

        # --- Лента: не видишь себя, видишь других --------------------------
        await _make_profile(s, 2, GAME_CS2, "2000–2500", "Bob")
        await _make_profile(s, 3, GAME_CS2, "1000–1500", "Carol")
        nxt = await q.get_next_profile(s, viewer_id=1, game=GAME_CS2)
        check("лента отдаёт чужую анкету, не свою", nxt is not None and nxt.user_id != 1)
        # Dota-лента для юзера 2 (у него нет dota-анкеты — но лента смотрит по игре,
        # тут проверяем, что юзер 1 со своей dota-анкетой не видит сам себя)
        nxt_dota = await q.get_next_profile(s, viewer_id=1, game=GAME_DOTA2)
        check("в Dota2-ленте юзер 1 себя не видит (других нет → None)", nxt_dota is None)

        # --- Пропуск убирает анкету из ленты -------------------------------
        await q.record_simple_interaction(s, 1, 2, GAME_CS2, "skip")
        await s.commit()
        seen_ids = set()
        for _ in range(5):
            p = await q.get_next_profile(s, 1, GAME_CS2)
            if p:
                seen_ids.add(p.user_id)
        check("пропущенная анкета (Bob) больше не показывается", 2 not in seen_ids)
        check("непросмотренная анкета (Carol) показывается", 3 in seen_ids)

        # --- Лайк в одну сторону → liked, не mutual ------------------------
        r1 = await q.record_like(s, 1, 3, GAME_CS2)
        await s.commit()
        check("первый лайк = 'liked' (не взаимный)", r1.status == "liked" and not r1.is_mutual)
        carol = await q.get_profile(s, 3, GAME_CS2)
        check("счётчик лайков цели увеличился", carol.likes_count == 1)

        # Повторный лайк той же анкеты не плодит дубль и не is_mutual
        r1b = await q.record_like(s, 1, 3, GAME_CS2)
        await s.commit()
        check("повторный лайк = 'already_liked'", r1b.status == "already_liked")
        carol = await q.get_profile(s, 3, GAME_CS2)
        check("повторный лайк НЕ накручивает счётчик", carol.likes_count == 1)

        # --- Ответный лайк → mutual для обоих ------------------------------
        r2 = await q.record_like(s, 3, 1, GAME_CS2)
        await s.commit()
        check("ответный лайк = взаимность ('mutual')", r2.status == "mutual" and r2.is_mutual)

        # --- Лайк с сообщением ---------------------------------------------
        await _make_profile(s, 4, GAME_CS2, "1500–2000", "Dave")
        r3 = await q.record_like(s, 4, 1, GAME_CS2, message="привет, го микс?")
        await s.commit()
        inter = await q._get_interaction(s, 4, 1, GAME_CS2)
        check("лайк с сообщением сохраняет текст", inter.message == "привет, го микс?")
        check("лайк с сообщением = 'liked'", r3.status == "liked")

        # --- Жалобы скрывают анкету ----------------------------------------
        from config import settings
        for reporter in range(100, 100 + settings.reports_to_hide):
            await q.record_simple_interaction(s, reporter, 4, GAME_CS2, "report")
        await s.commit()
        cnt = await q.count_reports(s, 4, GAME_CS2)
        check(f"жалобы посчитаны ({cnt} >= {settings.reports_to_hide})", cnt >= settings.reports_to_hide)
        if cnt >= settings.reports_to_hide:
            await q.deactivate_profile(s, 4, GAME_CS2)
            await s.commit()
        dave = await q.get_profile(s, 4, GAME_CS2)
        check("анкета с жалобами деактивирована", dave.is_active is False)
        # И больше не появляется в ленте
        seen2 = set()
        for _ in range(8):
            p = await q.get_next_profile(s, 99, GAME_CS2)
            if p:
                seen2.add(p.user_id)
        check("скрытая анкета не появляется в ленте", 4 not in seen2)

        # --- Кулдаун: пропущенная анкета возвращается через час ------------
        # Юзер 5 листает: пропускает юзера 6, затем юзер 6 пропадает из ленты.
        await _make_profile(s, 5, GAME_CS2, "1500–2000", "Eve")
        await _make_profile(s, 6, GAME_CS2, "1500–2000", "Frank")
        await q.record_simple_interaction(s, 5, 6, GAME_CS2, "skip")
        await s.commit()
        fresh = {p.user_id for _ in range(6) if (p := await q.get_next_profile(s, 5, GAME_CS2))}
        check("сразу после пропуска анкета скрыта (кулдаун)", 6 not in fresh)

        # «Перематываем время»: ставим updated_at на 2 часа назад → кулдаун истёк.
        await s.execute(
            update(Interaction)
            .where(Interaction.actor_id == 5, Interaction.target_id == 6)
            .values(updated_at=_utcnow() - timedelta(hours=2))
        )
        await s.commit()
        # Лента случайная — семплируем достаточно, чтобы наверняка увидеть вернувшуюся анкету.
        later = {p.user_id for _ in range(60) if (p := await q.get_next_profile(s, 5, GAME_CS2))}
        check("через час пропущенная анкета снова попадается", 6 in later)

        # А вот по жалобе анкета НЕ возвращается даже спустя время.
        await q.record_simple_interaction(s, 5, 6, GAME_CS2, "report")
        await s.execute(
            update(Interaction)
            .where(Interaction.actor_id == 5, Interaction.target_id == 6)
            .values(updated_at=_utcnow() - timedelta(hours=2))
        )
        await s.commit()
        after_report = {p.user_id for _ in range(8) if (p := await q.get_next_profile(s, 5, GAME_CS2))}
        check("по жалобе анкета не возвращается даже спустя время", 6 not in after_report)

        # --- Оценка совпадения по рангу ------------------------------------
        check("равный ранг → идеальное совпадение", "Идеальное" in compatibility_text(GAME_CS2, "2500+", "2500+"))
        check("дальние ранги → значительная разница", "Значительная" in compatibility_text(GAME_CS2, "До 1000", "2500+"))

        # --- Карточка анкеты рендерится без падений ------------------------
        card = render_profile(carol, GAME_CS2, compatibility="🎯 тест")
        check("карточка анкеты содержит ник и ранг", "Carol" in card and "1000–1500" in card)
        check("в карточке анкеты больше нет блока статистики", "Лайков" not in card and "Просмотров" not in card)

        # --- Новые поля анкеты: позиция, регион, мультифото ----------------
        await q.upsert_user(s, 200, "u200", "Pos")
        await q.create_or_update_profile(
            s, user_id=200, game=GAME_DOTA2, nickname="Pos", gender=GENDER_MALE,
            age=22, rank="3000–4000", about="x", photo_id="P1",
            position="2 — Мидер (Mid)", region="🏳️ СНГ", extra_photos="P2\nP3",
        )
        await s.commit()
        pos_p = await q.get_profile(s, 200, GAME_DOTA2)
        check("позиция и регион сохраняются", pos_p.position.startswith("2") and "СНГ" in pos_p.region)
        check("all_photos возвращает 3 фото", pos_p.all_photos == ["P1", "P2", "P3"])
        dcard = render_profile(pos_p, GAME_DOTA2)
        check("карточка Dota показывает позицию и регион", "Позиция" in dcard and "Регион" in dcard)

        # --- Фильтры поиска ------------------------------------------------
        await _make_profile(s, 50, GAME_CS2, "1500–2000", "Viewer")  # зритель
        # У анкеты есть FK на users → автор должен существовать (upsert_user).
        await q.upsert_user(s, 51, "u51", "MaleLow")
        await q.create_or_update_profile(
            s, user_id=51, game=GAME_CS2, nickname="MaleLow", gender=GENDER_MALE,
            age=20, rank="1500–2000", about="x", photo_id="P", region="🏳️ СНГ",
        )
        from database.models import GENDER_FEMALE
        await q.upsert_user(s, 52, "u52", "FemHigh")
        await q.create_or_update_profile(
            s, user_id=52, game=GAME_CS2, nickname="FemHigh", gender=GENDER_FEMALE,
            age=30, rank="2500+", about="x", photo_id="P", region="🇪🇺 Европа",
        )
        await s.commit()

        async def feed_ids(viewer):
            # Лента случайная и не «запоминает» показанных без взаимодействия,
            # поэтому семплируем много раз, чтобы собрать всех подходящих кандидатов.
            seen = set()
            for _ in range(60):
                p = await q.get_next_profile(s, viewer, GAME_CS2)
                if p:
                    seen.add(p.user_id)
            return seen

        base = await feed_ids(50)
        check("без фильтра видны оба кандидата", {51, 52} <= base)

        await q.update_filter_fields(s, 50, GAME_CS2, gender=GENDER_FEMALE)
        await s.commit()
        only_f = await feed_ids(50)
        check("фильтр по полу: виден только нужный пол", 52 in only_f and 51 not in only_f)

        await q.reset_filter(s, 50, GAME_CS2)
        await q.update_filter_fields(s, 50, GAME_CS2, age_min=25)
        await s.commit()
        by_age = await feed_ids(50)
        check("фильтр по возрасту 25+: молодой скрыт", 52 in by_age and 51 not in by_age)

        await q.reset_filter(s, 50, GAME_CS2)
        await q.update_filter_fields(s, 50, GAME_CS2, rank_min=4, rank_max=4)  # только «2500+»
        await s.commit()
        by_rank = await feed_ids(50)
        check("фильтр по рангу: виден только высокий ранг", 52 in by_rank and 51 not in by_rank)

        await q.reset_filter(s, 50, GAME_CS2)
        await q.update_filter_fields(s, 50, GAME_CS2, region="🇪🇺 Европа")
        await s.commit()
        by_region = await feed_ids(50)
        check("фильтр по региону: чужой регион скрыт", 52 in by_region and 51 not in by_region)
        await q.reset_filter(s, 50, GAME_CS2)
        await s.commit()

        # --- «Взаимные симпатии» (история мэтчей) --------------------------
        await _make_profile(s, 70, GAME_CS2, "1500–2000", "Target")
        await _make_profile(s, 71, GAME_CS2, "1500–2000", "Liker")
        await q.record_like(s, 71, 70, GAME_CS2, message="го катка")
        await s.commit()
        # Лайк в одну сторону — ещё НЕ взаимная симпатия.
        m_pre = await q.get_recent_matches(s, 70, GAME_CS2)
        check("односторонний лайк не попадает в историю взаимных", not any(p.user_id == 71 for p, _ in m_pre))
        # Ответный лайк → взаимность, попадает в историю обоим.
        await q.record_like(s, 70, 71, GAME_CS2)
        await s.commit()
        m70 = await q.get_recent_matches(s, 70, GAME_CS2)
        m71 = await q.get_recent_matches(s, 71, GAME_CS2)
        check(
            "взаимный лайк попадает в историю обоим",
            any(p.user_id == 71 for p, _ in m70) and any(p.user_id == 70 for p, _ in m71),
        )
        # Старше окна (пары дней) → выпадает из истории.
        await s.execute(
            update(Interaction)
            .where(Interaction.actor_id == 70, Interaction.target_id == 71)
            .values(updated_at=_utcnow() - timedelta(days=3))
        )
        await s.commit()
        m_old = await q.get_recent_matches(s, 70, GAME_CS2)
        check("матч старше пары дней выпадает из истории", not any(p.user_id == 71 for p, _ in m_old))
        # Повторный лайк спустя время обновляет матч → снова в истории как новый.
        await q.record_like(s, 70, 71, GAME_CS2)
        await s.commit()
        m_again = await q.get_recent_matches(s, 70, GAME_CS2)
        check("повторный лайк возвращает матч в историю как новый", any(p.user_id == 71 for p, _ in m_again))

        # --- Свежесть ленты: давно не заходивших прячем -------------------
        await _make_profile(s, 300, GAME_CS2, "1500–2000", "Sleepy")
        await _make_profile(s, 301, GAME_CS2, "1500–2000", "Viewer2")
        fresh = {p.user_id for _ in range(40) if (p := await q.get_next_profile(s, 301, GAME_CS2))}
        check("свежая анкета видна в ленте", 300 in fresh)
        # «Усыпляем» автора: последняя активность — раньше окна.
        await s.execute(
            update(User)
            .where(User.id == 300)
            .values(last_active=_utcnow() - timedelta(days=settings.feed_inactive_days + 5))
        )
        await s.commit()
        stale = {p.user_id for _ in range(40) if (p := await q.get_next_profile(s, 301, GAME_CS2))}
        check("давно не заходившая анкета скрыта из ленты", 300 not in stale)
        # Вернулся (любое действие обновляет активность) → снова в ленте.
        await q.touch_user_activity(s, 300)
        await s.commit()
        back = {p.user_id for _ in range(40) if (p := await q.get_next_profile(s, 301, GAME_CS2))}
        check("после возвращения анкета снова в ленте", 300 in back)

        # --- Платная разблокировка «Взаимных симпатий» --------------------
        await q.upsert_user(s, 500, "u500", "Payer")
        check("по умолчанию доступ к взаимным НЕ оплачен", await q.is_matches_unlocked(s, 500) is False)
        await q.set_matches_unlocked(s, 500)
        await s.commit()
        check("после оплаты доступ разблокирован навсегда", await q.is_matches_unlocked(s, 500) is True)

        # --- Бан скрывает анкеты из лент ------------------------------------
        await _make_profile(s, 80, GAME_CS2, "1500–2000", "BadGuy")
        await q.set_banned(s, 80, True)
        await s.commit()
        banned_feed = set()
        for _ in range(10):
            p = await q.get_next_profile(s, 90, GAME_CS2)
            if p:
                banned_feed.add(p.user_id)
        check("забаненная анкета не показывается в ленте", 80 not in banned_feed)
        stats = await q.get_stats(s)
        check("статистика считает забаненных", stats["banned"] >= 1 and stats["users"] > 0)

    await engine.dispose()
    print(f"\n🎉 Все {PASSED} проверок пройдены. Ядро бота работает.")
    os.unlink(_tmp.name)


if __name__ == "__main__":
    asyncio.run(main())
