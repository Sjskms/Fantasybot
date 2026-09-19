# my_filters/admin_filter.py
from typing import Union
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

import config
from database import Database


def get_config_admin_ids() -> set[int]:
    """
    Извлекает список ID администраторов из config.py.
    Поддерживает: ADMIN_ID (int/str) и ADMIN_IDS (list/set/tuple/str).
    """
    admins = set()

    single_admin = getattr(config, "ADMIN_ID", None)
    if single_admin is not None:
        try:
            admins.add(int(single_admin))
        except (ValueError, TypeError):
            pass

    multiple_admins = getattr(config, "ADMIN_IDS", None)
    if multiple_admins is not None:
        if isinstance(multiple_admins, (list, tuple, set)):
            for a_id in multiple_admins:
                try:
                    admins.add(int(a_id))
                except (ValueError, TypeError):
                    pass
        elif isinstance(multiple_admins, (int, str)):
            try:
                admins.add(int(multiple_admins))
            except (ValueError, TypeError):
                pass

    return admins


class IsAdmin(BaseFilter):
    """
    Проверяет, является ли пользователь администратором (через config.py или БД).
    """
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False

        user_id = user.id

        if user_id in get_config_admin_ids():
            return True

        try:
            return await Database.is_admin(user_id)
        except Exception:
            return False


class IsSuperAdmin(BaseFilter):
    """
    Проверяет, является ли пользователь ГЛАВНЫМ администратором из config.py.
    Только супер-админ может назначать и снимать других администраторов.
    """
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        user = getattr(event, "from_user", None)
        if not user:
            return False

        return user.id in get_config_admin_ids()
