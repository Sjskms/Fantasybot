# services/config_service.py
import json
import os
import logging
from database import Database

logger = logging.getLogger(__name__)
CONFIG_PATH = "bot_logging_config.json"

DEFAULT_CONFIG = {
    "max_sessions_per_user": 3,
    "max_export_channels_per_session": 15,
    "max_post_channels_per_session": 3,
    "premium_max_sessions_per_user": 20,
    "premium_max_export_channels_per_session": 100,
    "premium_max_post_channels_per_session": 25,
    "bot_logging": True,
    "telegram_logging": True,
    "console_logging": False,
    "file_logging": True,
    "telegram_log_chat_id": -1004315532241,
    "file_cleanup_period": "1_week",
    "events": {
        "new_user": True,
        "session_added": True,
        "session_removed": True,
        "forward_success": True,
        "forward_filtered": False,
        "forward_error": True,
        "forwarding_enabled": True,
        "forwarding_disabled": True,
        "bot_error": True,
        "other": True
    }
}


def read_full_config() -> dict:
    """Считывает полный JSON-файл конфигурации с сохранением всех секций."""
    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            return dict(DEFAULT_CONFIG)
        except Exception as e:
            logger.error("Не удалось создать %s: %s", CONFIG_PATH, e)
            return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return dict(DEFAULT_CONFIG)
            return data
    except Exception as e:
        logger.error("Ошибка чтения %s: %s", CONFIG_PATH, e)
        return dict(DEFAULT_CONFIG)


def update_limit_in_config(key: str, value: int) -> bool:
    """
    Обновляет конкретный числовой лимит в bot_logging_config.json,
    не затрагивая настройки логирования и другие ключи.
    """
    try:
        data = read_full_config()
        data[key] = int(value)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("Ошибка сохранения лимита %s в %s: %s", key, CONFIG_PATH, e)
        return False


def get_admin_limits() -> dict:
    """Синхронная функция чтения базовых лимитов из JSON (для обратной совместимости)."""
    raw_config = read_full_config()
    return {
        "max_sessions_per_user": raw_config.get("max_sessions_per_user", 1),
        "max_export_channels_per_session": raw_config.get("max_export_channels_per_session", 10),
        "max_post_channels_per_session": raw_config.get("max_post_channels_per_session", 3),
    }


async def is_user_premium(user_id: int) -> bool:
    """Проверяет реальное наличие активного Премиума у пользователя в базе данных."""
    return await Database.is_premium_active(user_id)


async def get_user_limits(user_id: int) -> dict:
    """
    Возвращает актуальные лимиты сессий и каналов для конкретного пользователя
    в зависимости от наличия у него Премиум-доступа.
    """
    default_free = {
        "max_sessions": 1,
        "max_export_channels": 10,
        "max_post_channels": 3,
        "is_premium": False
    }
    default_premium = {
        "max_sessions": 10,
        "max_export_channels": 100,
        "max_post_channels": 25,
        "is_premium": True
    }

    raw_config = read_full_config()
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