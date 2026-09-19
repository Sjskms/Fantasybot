# services/logging_service.py
import datetime
import json
import logging
import os
from typing import Any, Dict, Optional

import aiofiles
from aiogram import Bot
from pyrogram.enums import ParseMode

from database import Database

logger = logging.getLogger(__name__)

CONFIG_FILE = "bot_logging_config.json"
LOG_FILE_PATH = "bot_events.log"

DEFAULT_LOGGING_CONFIG: Dict[str, Any] = {
    # Главные тумблеры
    "bot_logging": True,
    "telegram_logging": True,
    "console_logging": True,
    "file_logging": True,
    # Настройки назначения
    "telegram_log_chat_id": None,  # int/str ID чата/канала или None (все админы)
    "file_cleanup_period": "1_week",  # "1_day", "3_days", "1_week", "2_weeks", "1_month", "never"
    # Детальные типы событий
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


def set_logging_bot_instance(bot: Bot):
    """Привязка экземпляра бота для рассылки логов."""
    global _bot_ref
    _bot_ref = bot


def get_logging_bot_instance() -> Optional[Bot]:
    """Возвращает привязанный экземпляр Aiogram Bot."""
    return _bot_ref


async def load_global_logging_config() -> Dict[str, Any]:
    """Загружает глобальные настройки логирования из JSON-файла."""
    global _current_config

    if not os.path.exists(CONFIG_FILE):
        _current_config = DEFAULT_LOGGING_CONFIG.copy()
        await save_global_logging_config(_current_config)
        return _current_config

    try:
        async with aiofiles.open(CONFIG_FILE, mode="r", encoding="utf-8") as f:
            content = await f.read()
            loaded = json.loads(content)
            merged = DEFAULT_LOGGING_CONFIG.copy()
            merged.update(loaded)
            if "events" in loaded and isinstance(loaded["events"], dict):
                merged["events"] = DEFAULT_LOGGING_CONFIG["events"].copy()
                merged["events"].update(loaded["events"])
            _current_config = merged
            return _current_config
    except Exception as e:
        logger.error("Ошибка при чтении %s: %s", CONFIG_FILE, e)
        _current_config = DEFAULT_LOGGING_CONFIG.copy()
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
    """Запись строки лога в текстовый файл bot_events.log с отметкой времени."""
    clean_text = (
        message.replace("<b>", "")
        .replace("</b>", "")
        .replace("<code>", "")
        .replace("</code>", "")
        .replace("<i>", "")
        .replace("</i>", "")
    )
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now_str}] [{event_key.upper()}] {clean_text}\n"

    try:
        async with aiofiles.open(LOG_FILE_PATH, mode="a", encoding="utf-8") as f:
            await f.write(log_line)
    except Exception as e:
        logger.error("Ошибка записи в файл логов: %s", e)


async def cleanup_log_file(period: Optional[str] = None):
    """Очищает файл логов от устаревших записей за указанный период."""
    if not os.path.exists(LOG_FILE_PATH):
        return

    cfg = get_cached_logging_config()
    target_period = period or cfg.get("file_cleanup_period", "1_week")

    if target_period == "delete_now":
        try:
            os.remove(LOG_FILE_PATH)
            logger.info("Файл логов сразу удален.")
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
    max_days = days_map.get(target_period)
    if not max_days:
        return

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


async def log_event(event_key: str, message: str, extra_console: str = ""):
    """
    ГЛОБАЛЬНОЕ логирование системы (для Администратора):
    Сверяет event_key с настройками JSON:
    1. Выводит в консоль (если включено console_logging)
    2. Записывает в текстовый файл (если включено file_logging)
    3. Отправляет в Telegram админам или в указанный админский чат
    """
    cfg = get_cached_logging_config()

    if not cfg.get("bot_logging", True):
        return

    events_cfg = cfg.get("events", {})
    is_event_enabled = events_cfg.get(event_key, True)

    if not is_event_enabled:
        return

    # 1. Логирование в консоль
    if cfg.get("console_logging", True):
        console_text = (
            extra_console
            if extra_console
            else message.replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
        )
        logger.info("[LOG:%s] %s", event_key.upper(), console_text)

    # 2. Логирование в текстовый файл
    if cfg.get("file_logging", True):
        await append_to_log_file(event_key, message)
        await cleanup_log_file()

    # 3. Логирование в Telegram для Администрации
    if cfg.get("telegram_logging", True) and _bot_ref:
        target_chat_id = cfg.get("telegram_log_chat_id")

        if target_chat_id:
            try:
                await _bot_ref.send_message(
                    chat_id=target_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error("Ошибка отправки глобального лога в чат %s: %s", target_chat_id, e)
        else:
            admin_ids = []
            try:
                admin_ids = await Database.get_all_admin_ids()
            except Exception as e:
                logger.debug("Не удалось получить админов из БД: %s", e)

            try:
                from my_filters.admin_filter import get_config_admin_ids
                for ca in get_config_admin_ids():
                    if ca not in admin_ids:
                        admin_ids.append(ca)
            except Exception:
                pass

            for admin_id in admin_ids:
                try:
                    await _bot_ref.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass
