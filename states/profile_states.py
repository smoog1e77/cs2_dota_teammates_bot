"""FSM-состояния: создание/редактирование анкеты, лента, фильтры, админка."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ProfileCreation(StatesGroup):
    nickname = State()
    gender = State()
    age = State()
    rank = State()
    position = State()   # только Dota 2
    region = State()
    about = State()
    photos = State()
    preview = State()


class ProfileEdit(StatesGroup):
    nickname = State()
    age = State()
    about = State()
    photos = State()


class Browse(StatesGroup):
    browsing = State()
    awaiting_message = State()


class FilterEdit(StatesGroup):
    age_min = State()
    age_max = State()


class AdminFSM(StatesGroup):
    ban = State()
    unban = State()
    broadcast = State()
