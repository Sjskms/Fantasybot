# services/config_service.py
import json
import os
import logging

logger = logging.getLogger(__name__)
CONFIG_PATH = "bot_logging_config.json"


async def is_user_premium(user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя активный Премиум-доступ.
    СЕЙЧАС: Всегда True (для всех пользователей включен премиум).
    В БУДУЩЕМ: Здесь будет проверка срока подписки в БД.
    """
    # return await Database.is_premium_active(user_id)
    return True


async def get_user_limits(user_id: int) -> dict:
    """
    Возвращает актуальные лимиты сессий и каналов для конкретного пользователя
    в зависимости от наличия у него Премиум-доступа.
    """
    # Стандартные дефолты на случай отсутствия файла
    default_free = {
        "max_sessions": 3,
        "max_export_channels": 10,
        "max_post_channels": 5,
        "is_premium": False
    }
    default_premium = {
        "max_sessions": 10,
        "max_export_channels": 50,
        "max_post_channels": 25,
        "is_premium": True
    }

    raw_config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw_config = json.load(f)
        except Exception as e:
            logger.error(f"Ошибка чтения {CONFIG_PATH}: {e}")

    has_premium = await is_user_premium(user_id)

    if has_premium:
        return {
            "max_sessions": raw_config.get("premium_max_sessions_per_user", default_premium["max_sessions"]),
            "max_export_channels": raw_config.get("premium_max_export_channels_per_session", default_premium["max_export_channels"]),
            "max_post_channels": raw_config.get("premium_max_post_channels_per_session", default_premium["max_post_channels"]),
            "is_premium": True
        }
    else:
        return {
            "max_sessions": raw_config.get("max_sessions_per_user", default_free["max_sessions"]),
            "max_export_channels": raw_config.get("max_export_channels_per_session", default_free["max_export_channels"]),
            "max_post_channels": raw_config.get("max_post_channels_per_session", default_free["max_post_channels"]),
            "is_premium": False
        }