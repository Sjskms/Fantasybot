# services/logging_service.py
import asyncio
import datetime
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import aiofiles
from aiogram import Bot

from database import Database

logger = logging.getLogger(__name__)

# Фиксированный путь к файлам логирования в корне проекта
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "bot_logging_config.json")
LOG_FILE_PATH = os.path.join(BASE_DIR, "bot_events.log")

DEFAULT_LOGGING_CONFIG: Dict[str, Any] = {
    "bot_logging": True,
    "telegram_logging": True,
    "console_logging": False,
    "file_logging": False,
    "telegram_log_chat_id": None,
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
        "other": True,
    },
}

_current_config: Dict[str, Any] = DEFAULT_LOGGING_CONFIG.copy()
_bot_ref: Optional[Bot] = None
_last_cleanup_time: float = 0.0


def set_logging_bot_instance(bot: Bot):
    """Привязка экземпляра бота для рассылки логов."""
    global _bot_ref
    _bot_ref = bot


def get_logging_bot_instance() -> Optional[Bot]:
    """Возвращает привязанный экземпляр Aiogram Bot."""
    return _bot_ref


def strip_html_tags(text: str) -> str:
    """Удаляет любые HTML-теги из строки для консоли и файла."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


async def load_global_logging_config() -> Dict[str, Any]:
    """
    Загружает глобальные настройки логирования из JSON-файла.
    ВНИМАНИЕ: Если файл существует, существующие настройки пользователя сохраняются 
    и НЕ перезаписываются дефолтными значениями!
    """
    global _current_config

    if not os.path.exists(CONFIG_FILE):
        _current_config = DEFAULT_LOGGING_CONFIG.copy()
        await save_global_logging_config(_current_config)
        return _current_config

    try:
        async with aiofiles.open(CONFIG_FILE, mode="r", encoding="utf-8") as f:
            content = await f.read()
            if not content.strip():
                _current_config = DEFAULT_LOGGING_CONFIG.copy()
                await save_global_logging_config(_current_config)
                return _current_config
            loaded = json.loads(content)

        # Берем сохраненный конфиг пользователя за основу
        merged = DEFAULT_LOGGING_CONFIG.copy()
        merged.update(loaded)

        # Отдельно сохраняем значения вложенных событий, не затирая пользовательские тумблеры
        if "events" in loaded and isinstance(loaded["events"], dict):
            user_events = DEFAULT_LOGGING_CONFIG["events"].copy()
            user_events.update(loaded["events"])
            merged["events"] = user_events

        _current_config = merged
        return _current_config

    except Exception as e:
        logger.error("Ошибка при чтении %s: %s. Конфиг НЕ сброшен.", CONFIG_FILE, e)
        return _current_config


async def save_global_logging_config(cfg: Dict[str, Any]):
    """Сохраняет глобальные настройки логирования в JSON-файл."""
    global _current_config
    _current_config = cfg
    try:
        async with aiofiles.open(CONFIG_FILE, mode="w", encoding="utf-8") as f:
            await f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error("Ошибка при записи %s: %s", CONFIG_FILE, e)


def get_cached_logging_config() -> Dict[str, Any]:
    """Возвращает кэшированный конфиг логирования."""
    return _current_config


async def append_to_log_file(event_key: str, message: str):
    """Быстрая запись строки лога в текстовый файл."""
    clean_text = strip_html_tags(message).strip()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] [{event_key.upper()}] {clean_text}\n"

    try:
        async with aiofiles.open(LOG_FILE_PATH, mode="a", encoding="utf-8") as f:
            await f.write(log_line)
    except Exception as e:
        logger.error("Ошибка записи в файл логов: %s", e)


async def cleanup_log_file(period: Optional[str] = None, force: bool = False):
    """Очищает файл логов от устаревших записей (не чаще раза в 6 часов)."""
    global _last_cleanup_time
    now = asyncio.get_event_loop().time()

    if not force and (now - _last_cleanup_time < 21600):
        return

    _last_cleanup_time = now

    if not os.path.exists(LOG_FILE_PATH):
        return

    cfg = get_cached_logging_config()
    target_period = period or cfg.get("file_cleanup_period", "1_week")

    if target_period == "delete_now":
        try:
            os.remove(LOG_FILE_PATH)
            logger.info("Файл логов удален.")
        except Exception as e:
            logger.error("Ошибка при удалении файла логов: %s", e)
        return

    if target_period == "never":
        return

    days_map = {
        "1_day": 1,
        "3_days": 3,
        "1_week": 7,
        "2_weeks": 14,
        "1_month": 30,
    }
    max_days = days_map.get(target_period, 7)
    cutoff = datetime.datetime.now() - datetime.timedelta(days=max_days)

    try:
        async with aiofiles.open(LOG_FILE_PATH, mode="r", encoding="utf-8") as f:
            lines = await f.readlines()

        new_lines = []
        for line in lines:
            if line.startswith("[") and "]" in line:
                date_part = line[1:19]
                try:
                    line_dt = datetime.datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
                    if line_dt >= cutoff:
                        new_lines.append(line)
                except ValueError:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        async with aiofiles.open(LOG_FILE_PATH, mode="w", encoding="utf-8") as f:
            await f.writelines(new_lines)
    except Exception as e:
        logger.error("Ошибка очистки файла логов: %s", e)


async def get_user_info_block(user_id: Optional[int]) -> str:
    """Динамически достает информацию о пользователе из базы данных для логов админа."""
    if not user_id:
        return ""
    
    try:
        user_data = await Database.get_user(user_id)
        if user_data:
            name = user_data[1] if len(user_data) > 1 else "Не указано"
            username = user_data[2] if len(user_data) > 2 else None
            username_str = f"@{username}" if username else "нет"
            return f"\n👤 <b>Пользователь:</b> {name} | ID: <code>{user_id}</code> | Username: {username_str}"
    except Exception as e:
        logger.debug("Не удалось получить информацию о пользователе %s: %s", user_id, e)
    
    return f"\n👤 <b>Пользователь ID:</b> <code>{user_id}</code>"


async def log_event(event_key: str, message: str, extra_console: str = "", user_id: Optional[int] = None):
    """
    Глобальное логирование для администраторов.
    1. Пишет в консоль (если включено console_logging).
    2. Дописывает в текстовый файл bot_events.log (если включено file_logging).
    3. Отправляет в Telegram (если включено telegram_logging):
       - В целевой канал/чат (если задан telegram_log_chat_id).
       - Админу из config.py (если chat_id не задан).
    """
    cfg = get_cached_logging_config()

    if not cfg.get("bot_logging", True):
        return

    events_cfg = cfg.get("events", {})
    if not events_cfg.get(event_key, True):
        return

    user_info_str = await get_user_info_block(user_id)
    final_message = message + user_info_str

    # 1. Логирование в консоль
    if cfg.get("console_logging", True):
        console_text = extra_console if extra_console else strip_html_tags(final_message).strip()
        logger.info("[LOG:%s] %s", event_key.upper(), console_text)

    # 2. Логирование в файл
    if cfg.get("file_logging", True):
        await append_to_log_file(event_key, final_message)
        asyncio.create_task(cleanup_log_file())

    # 3. Логирование в Telegram
    if cfg.get("telegram_logging", True) and _bot_ref:
        safe_msg = final_message if len(final_message) <= 4000 else final_message[:3990] + "..."
        target_chat_id = cfg.get("telegram_log_chat_id")

        if target_chat_id:
            try:
                await _bot_ref.send_message(
                    chat_id=target_chat_id,
                    text=safe_msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error("Ошибка отправки глобального лога в чат %s: %s", target_chat_id, e)
        else:
            # Отправка строго администраторам из config.py
            try:
                from my_filters.admin_filter import get_config_admin_ids
                admin_ids = list(get_config_admin_ids())
            except Exception:
                admin_ids = []

            for admin_id in admin_ids:
                try:
                    await _bot_ref.send_message(
                        chat_id=admin_id,
                        text=safe_msg,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(0.04)
                except Exception:
                    pass
