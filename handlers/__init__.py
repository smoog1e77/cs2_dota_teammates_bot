"""Сборка всех роутеров. Порядок ВКЛЮЧЕНИЯ важен:

navigation (start) идёт первым — его кнопки-навигация (Главное меню, Отмена,
выбор игры) перехватываются раньше любых state-обработчиков ввода. Это и есть
защита от «залипания» в состоянии и конфликтов кнопок.
"""
from aiogram import Router

from handlers import admin, browse, filters, profile, start


def setup_routers() -> Router:
    root = Router()
    root.include_router(start.router)     # навигация и /start — приоритет
    root.include_router(admin.router)     # админ-панель (только для ADMIN_IDS)
    root.include_router(profile.router)   # создание/редактирование анкеты
    root.include_router(filters.router)   # фильтры поиска
    root.include_router(browse.router)    # лента, лайки, сообщения, входящие
    return root
