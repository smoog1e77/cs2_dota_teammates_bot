"""Слой доступа к данным (репозиторий).

Вся работа с БД собрана здесь — хендлеры не пишут SQL напрямую.
Каждая функция принимает готовую AsyncSession (её прокидывает middleware).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    ACTION_LIKE,
    ACTION_REPORT,
    Interaction,
    Profile,
    SearchFilter,
    User,
)
from utils.constants import RANKS


def _utcnow() -> datetime:
    """Текущее время в UTC без таймзоны (совместимо с хранением в SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
#  Пользователи
# --------------------------------------------------------------------------- #
async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> User:
    """Создать пользователя или обновить его username/имя (для контактов)."""
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id, username=username, first_name=first_name, last_active=_utcnow()
        )
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
        user.last_active = _utcnow()
    await session.flush()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def touch_user_activity(session: AsyncSession, user_id: int) -> None:
    """Отметить пользователя активным сейчас — для «свежести» ленты."""
    await session.execute(
        update(User).where(User.id == user_id).values(last_active=_utcnow())
    )


async def is_matches_unlocked(session: AsyncSession, user_id: int) -> bool:
    """Оплатил ли пользователь разовый доступ к «Взаимным симпатиям»."""
    user = await session.get(User, user_id)
    return bool(user and user.matches_unlocked)


async def set_matches_unlocked(session: AsyncSession, user_id: int) -> None:
    """Открыть доступ к «Взаимным симпатиям» навсегда (после оплаты звёздами)."""
    await session.execute(
        update(User).where(User.id == user_id).values(matches_unlocked=True)
    )


# --------------------------------------------------------------------------- #
#  Анкеты
# --------------------------------------------------------------------------- #
async def get_profile(
    session: AsyncSession, user_id: int, game: str
) -> Profile | None:
    stmt = select(Profile).where(
        Profile.user_id == user_id, Profile.game == game
    )
    return await session.scalar(stmt)


async def create_or_update_profile(
    session: AsyncSession,
    *,
    user_id: int,
    game: str,
    nickname: str,
    gender: str,
    age: int,
    rank: str,
    about: str,
    photo_id: str,
    position: str = "",
    region: str = "",
    extra_photos: str = "",
) -> Profile:
    """Создать анкету или полностью пересоздать существующую (upsert)."""
    profile = await get_profile(session, user_id, game)
    if profile is None:
        profile = Profile(user_id=user_id, game=game)
        session.add(profile)
    profile.nickname = nickname
    profile.gender = gender
    profile.age = age
    profile.rank = rank
    profile.position = position
    profile.region = region
    profile.about = about
    profile.photo_id = photo_id
    profile.extra_photos = extra_photos
    profile.is_active = True
    await session.flush()
    return profile


async def update_profile_field(
    session: AsyncSession, user_id: int, game: str, field: str, value
) -> Profile | None:
    """Обновить одно поле анкеты (имя / возраст / описание / ранг / пол / фото)."""
    profile = await get_profile(session, user_id, game)
    if profile is None:
        return None
    setattr(profile, field, value)
    await session.flush()
    return profile


async def delete_profile(session: AsyncSession, user_id: int, game: str) -> None:
    profile = await get_profile(session, user_id, game)
    if profile is not None:
        await session.delete(profile)
        await session.flush()


async def deactivate_profile(
    session: AsyncSession, user_id: int, game: str
) -> None:
    await session.execute(
        update(Profile)
        .where(Profile.user_id == user_id, Profile.game == game)
        .values(is_active=False)
    )


# --------------------------------------------------------------------------- #
#  Лента анкет
# --------------------------------------------------------------------------- #
def _seen_subquery(viewer_id: int, game: str):
    """Анкеты, которые сейчас НЕ нужно показывать зрителю.

    - Жалоба (report) скрывает анкету для пожаловавшегося навсегда.
    - Лайк/пропуск скрывают анкету лишь на время кулдауна; после него анкета
      снова попадётся в ленте (REBROWSE_COOLDOWN_MINUTES, по умолчанию 60 мин).
    """
    cutoff = _utcnow() - timedelta(minutes=settings.rebrowse_cooldown_minutes)
    return select(Interaction.target_id).where(
        Interaction.actor_id == viewer_id,
        Interaction.game == game,
        or_(
            Interaction.type == ACTION_REPORT,
            Interaction.updated_at >= cutoff,
        ),
    )


def _filter_conditions(flt: SearchFilter | None, game: str) -> list:
    """SQL-условия фильтров поиска (пустой список = без ограничений)."""
    if flt is None:
        return []
    conds = []
    if flt.gender:
        conds.append(Profile.gender == flt.gender)
    if flt.age_min is not None:
        conds.append(Profile.age >= flt.age_min)
    if flt.age_max is not None:
        conds.append(Profile.age <= flt.age_max)
    if flt.region:
        # Анкеты без указанного региона показываем всегда (не прячем «старые»).
        conds.append(or_(Profile.region == flt.region, Profile.region == ""))
    if flt.rank_min is not None or flt.rank_max is not None:
        ranks = RANKS[game]
        lo = flt.rank_min if flt.rank_min is not None else 0
        hi = flt.rank_max if flt.rank_max is not None else len(ranks) - 1
        allowed = ranks[lo : hi + 1]
        if allowed:
            conds.append(Profile.rank.in_(allowed))
    return conds


def _feed_base(viewer_id: int, game: str, flt: SearchFilter | None):
    """Общие условия ленты: активна, не своя, не забанен и не «уснувший» автор, не «свежевиденная»."""
    active_cutoff = _utcnow() - timedelta(days=settings.feed_inactive_days)
    return (
        Profile.game == game,
        Profile.is_active.is_(True),
        Profile.user_id != viewer_id,
        User.is_banned.is_(False),
        # Прячем давно не заходивших — вернутся в ленту, как только снова зайдут.
        User.last_active >= active_cutoff,
        Profile.user_id.notin_(_seen_subquery(viewer_id, game)),
        *_filter_conditions(flt, game),
    )


async def get_next_profile(
    session: AsyncSession, viewer_id: int, game: str
) -> Profile | None:
    """Следующая анкета для зрителя с учётом его фильтров поиска.

    ORDER BY random() даёт разнообразие и отлично работает на тысячах анкет.
    Для десятков тысяч+ стоит заменить на курсорную пагинацию по id.
    """
    flt = await get_filter(session, viewer_id, game)
    stmt = (
        select(Profile)
        .join(User, User.id == Profile.user_id)
        .where(*_feed_base(viewer_id, game, flt))
        .order_by(func.random())
        .limit(1)
    )
    return await session.scalar(stmt)


async def count_available(
    session: AsyncSession, viewer_id: int, game: str
) -> int:
    flt = await get_filter(session, viewer_id, game)
    stmt = (
        select(func.count(Profile.id))
        .join(User, User.id == Profile.user_id)
        .where(*_feed_base(viewer_id, game, flt))
    )
    return int(await session.scalar(stmt) or 0)


# --------------------------------------------------------------------------- #
#  Фильтры поиска
# --------------------------------------------------------------------------- #
async def get_filter(
    session: AsyncSession, user_id: int, game: str
) -> SearchFilter | None:
    stmt = select(SearchFilter).where(
        SearchFilter.user_id == user_id, SearchFilter.game == game
    )
    return await session.scalar(stmt)


async def get_or_create_filter(
    session: AsyncSession, user_id: int, game: str
) -> SearchFilter:
    flt = await get_filter(session, user_id, game)
    if flt is None:
        flt = SearchFilter(user_id=user_id, game=game)
        session.add(flt)
        await session.flush()
    return flt


async def update_filter_fields(
    session: AsyncSession, user_id: int, game: str, **fields
) -> SearchFilter:
    """Обновить поля фильтра (значение None очищает ограничение)."""
    flt = await get_or_create_filter(session, user_id, game)
    for key, value in fields.items():
        setattr(flt, key, value)
    await session.flush()
    return flt


async def reset_filter(session: AsyncSession, user_id: int, game: str) -> None:
    await session.execute(
        update(SearchFilter)
        .where(SearchFilter.user_id == user_id, SearchFilter.game == game)
        .values(
            gender=None, age_min=None, age_max=None,
            rank_min=None, rank_max=None, region=None,
        )
    )


async def increment_views(session: AsyncSession, profile_id: int) -> None:
    await session.execute(
        update(Profile)
        .where(Profile.id == profile_id)
        .values(views_count=Profile.views_count + 1)
    )


async def _increment_likes(session: AsyncSession, target_id: int, game: str) -> None:
    await session.execute(
        update(Profile)
        .where(Profile.user_id == target_id, Profile.game == game)
        .values(likes_count=Profile.likes_count + 1)
    )


# --------------------------------------------------------------------------- #
#  Лайки / пропуски / жалобы
# --------------------------------------------------------------------------- #
@dataclass
class LikeResult:
    """Результат лайка для выбора правильного уведомления."""

    status: str  # "mutual" | "liked" | "already_liked" | "already_mutual"

    @property
    def is_mutual(self) -> bool:
        """Взаимность возникла ТОЛЬКО ЧТО (этим лайком). Для ленты: одиночный лайк
        уже мэтчившейся пары (already_mutual) сюда НЕ попадает — это обычный лайк."""
        return self.status == "mutual"

    @property
    def matched(self) -> bool:
        """Пара взаимна — новый мэтч ИЛИ уже бывший. Нужно для кнопки «Ответить
        взаимностью»: явное согласие ВСЕГДА ведёт к мэтчу, а не к новому лайку
        (иначе ответ зацикливается между двумя уже мэтчившимися людьми)."""
        return self.status in ("mutual", "already_mutual")


async def _get_interaction(
    session: AsyncSession, actor_id: int, target_id: int, game: str
) -> Interaction | None:
    stmt = select(Interaction).where(
        Interaction.actor_id == actor_id,
        Interaction.target_id == target_id,
        Interaction.game == game,
    )
    return await session.scalar(stmt)


async def record_like(
    session: AsyncSession,
    actor_id: int,
    target_id: int,
    game: str,
    message: str | None = None,
) -> LikeResult:
    """Поставить лайк (опционально с сообщением). Определяет взаимность."""
    existing = await _get_interaction(session, actor_id, target_id, game)
    reverse = await _get_interaction(session, target_id, actor_id, game)
    reverse_is_like = reverse is not None and reverse.type == ACTION_LIKE

    # Уже лайкали раньше
    if existing is not None and existing.type == ACTION_LIKE:
        existing.updated_at = _utcnow()  # отодвигаем кулдаун повторного показа
        if message:
            existing.message = message
        if existing.is_mutual:
            await session.flush()
            return LikeResult("already_mutual")
        if reverse_is_like:
            existing.is_mutual = True
            reverse.is_mutual = True
            await session.flush()
            return LikeResult("mutual")
        await session.flush()
        return LikeResult("already_liked")

    # Новый лайк (или превращаем прошлый skip в like)
    if existing is not None:
        existing.type = ACTION_LIKE
        existing.message = message
    else:
        existing = Interaction(
            actor_id=actor_id,
            target_id=target_id,
            game=game,
            type=ACTION_LIKE,
            message=message,
        )
        session.add(existing)

    await _increment_likes(session, target_id, game)

    if reverse_is_like:
        existing.is_mutual = True
        reverse.is_mutual = True
        await session.flush()
        return LikeResult("mutual")

    await session.flush()
    return LikeResult("liked")


async def record_simple_interaction(
    session: AsyncSession, actor_id: int, target_id: int, game: str, action: str
) -> None:
    """Записать пропуск или жалобу (upsert по уникальному ключу)."""
    existing = await _get_interaction(session, actor_id, target_id, game)
    if existing is not None:
        # Не понижаем лайк до скипа; жалобу фиксируем всегда. Но в любом случае
        # сдвигаем кулдаун, чтобы анкета не вернулась в ленту сразу же.
        if existing.type == ACTION_LIKE and action != ACTION_REPORT:
            existing.updated_at = _utcnow()
            await session.flush()
            return
        existing.type = action
        existing.updated_at = _utcnow()
    else:
        session.add(
            Interaction(
                actor_id=actor_id, target_id=target_id, game=game, type=action
            )
        )
    await session.flush()


async def count_reports(session: AsyncSession, target_id: int, game: str) -> int:
    stmt = select(func.count(Interaction.id)).where(
        Interaction.target_id == target_id,
        Interaction.game == game,
        Interaction.type == ACTION_REPORT,
    )
    return int(await session.scalar(stmt) or 0)


# --------------------------------------------------------------------------- #
#  Взаимные симпатии («история мэтчей»)
# --------------------------------------------------------------------------- #
async def get_recent_matches(
    session: AsyncSession, user_id: int, game: str
) -> list[tuple[Profile, User]]:
    """Анкеты, с которыми у пользователя взаимная симпатия за последние дни.

    Берём свои лайки, ставшие взаимными, не старше окна MATCHES_HISTORY_DAYS —
    так список сам чистится и не растёт бесконечно. Повторный лайк той же анкеты
    обновляет время мэтча (record_like → updated_at), поэтому старый матч после
    нового лайка снова всплывает как свежий; спама нет, ведь та же анкета и так
    возвращается в ленту лишь через час (кулдаун). Отдаём пары (анкета, автор)
    для показа карточки и контакта, от самых свежих к старым.
    """
    cutoff = _utcnow() - timedelta(days=settings.matches_history_days)
    stmt = (
        select(Profile, User)
        .join(
            Interaction,
            and_(
                Interaction.target_id == Profile.user_id,
                Interaction.game == game,
                Interaction.actor_id == user_id,
                Interaction.is_mutual.is_(True),
                Interaction.updated_at >= cutoff,
            ),
        )
        .join(User, User.id == Profile.user_id)
        .where(
            Profile.game == game,
            Profile.is_active.is_(True),
            User.is_banned.is_(False),
        )
        .order_by(Interaction.updated_at.desc())
    )
    rows = await session.execute(stmt)
    return [(profile, user) for profile, user in rows]


# --------------------------------------------------------------------------- #
#  Админ-панель
# --------------------------------------------------------------------------- #
async def set_banned(session: AsyncSession, user_id: int, banned: bool) -> bool:
    """Забанить/разбанить пользователя. True, если пользователь найден."""
    user = await session.get(User, user_id)
    if user is None:
        return False
    user.is_banned = banned
    if banned:
        # Бан скрывает все анкеты пользователя из лент.
        await session.execute(
            update(Profile).where(Profile.user_id == user_id).values(is_active=False)
        )
    await session.flush()
    return True


async def get_stats(session: AsyncSession) -> dict:
    """Сводная статистика бота для админа."""
    async def _scalar(stmt) -> int:
        return int(await session.scalar(stmt) or 0)

    total_users = await _scalar(select(func.count(User.id)))
    banned = await _scalar(select(func.count(User.id)).where(User.is_banned.is_(True)))
    cs2 = await _scalar(select(func.count(Profile.id)).where(Profile.game == "cs2"))
    dota2 = await _scalar(select(func.count(Profile.id)).where(Profile.game == "dota2"))
    active = await _scalar(
        select(func.count(Profile.id)).where(Profile.is_active.is_(True))
    )
    likes = await _scalar(
        select(func.count(Interaction.id)).where(Interaction.type == ACTION_LIKE)
    )
    mutual_rows = await _scalar(
        select(func.count(Interaction.id)).where(Interaction.is_mutual.is_(True))
    )
    reports = await _scalar(
        select(func.count(Interaction.id)).where(Interaction.type == ACTION_REPORT)
    )
    return {
        "users": total_users,
        "banned": banned,
        "profiles_cs2": cs2,
        "profiles_dota2": dota2,
        "profiles_active": active,
        "likes": likes,
        "matches": mutual_rows // 2,  # взаимность учтена с обеих сторон
        "reports": reports,
    }


async def get_top_reported(session: AsyncSession, limit: int = 10) -> list[tuple]:
    """Топ анкет по числу жалоб: [(target_id, game, count), ...]."""
    stmt = (
        select(
            Interaction.target_id,
            Interaction.game,
            func.count(Interaction.id).label("cnt"),
        )
        .where(Interaction.type == ACTION_REPORT)
        .group_by(Interaction.target_id, Interaction.game)
        .order_by(func.count(Interaction.id).desc())
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return [(r.target_id, r.game, r.cnt) for r in rows]


async def get_all_user_ids(session: AsyncSession, include_banned: bool = False) -> list[int]:
    """ID всех пользователей (для рассылки)."""
    stmt = select(User.id)
    if not include_banned:
        stmt = stmt.where(User.is_banned.is_(False))
    rows = await session.execute(stmt)
    return [r[0] for r in rows]
