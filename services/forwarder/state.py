# services/forwarder/state.py
import asyncio
from pyrogram import Client
from typing import Dict, Tuple, Set

# --- ГЛОБАЛЬНЫЕ РЕЕСТРЫ СОСТОЯНИЯ ---
active_forwarder_tasks: Dict[Tuple[int, str], asyncio.Task] = {}
loaded_configs: Dict[Tuple[int, str], dict] = {}
running_configs: Dict[Tuple[int, str], dict] = {}
media_group_buffers: Dict[str, dict] = {}
client_instances: Dict[Tuple[int, str], Client] = {}

# Защита от параллельного запуска одной и той же сессии
client_start_locks: Dict[Tuple[int, str], asyncio.Lock] = {}

# Защита от дублей при параллельной работе MessageHandler и поллера
processed_messages: Set[Tuple[int, int]] = set()
processing_messages: Set[Tuple[int, int]] = set()
message_claim_lock = asyncio.Lock()
last_known_msg_ids: Dict[int, int] = {}

bot_instance = None


def set_bot_instance(bot):
    """Устанавливает экземпляр Aiogram-бота для отправки логов."""
    global bot_instance
    bot_instance = bot
    try:
        from services.logging_service import set_logging_bot_instance
        set_logging_bot_instance(bot)
    except Exception:
        pass


def set_running_config(user_id: int, session_name: str, config: dict):
    """Фиксирует точный снимок конфигурации, на которой запущен юзербот."""
    import copy
    running_configs[(user_id, session_name)] = copy.deepcopy(config or {})


def has_session_unapplied_changes(
    user_id: int,
    session_name: str,
    current_db_config: dict,
) -> bool:
    """Проверяет, есть ли не примененные изменения между БД и запущенным процессом."""
    running = running_configs.get((user_id, session_name))
    if running is None:
        return False
    return running != (current_db_config or {})


def get_client_start_lock(user_id: int, session_name: str) -> asyncio.Lock:
    """Возвращает уникальный асинхронный лок для запуска конкретной сессии."""
    key = (user_id, session_name)
    if key not in client_start_locks:
        client_start_locks[key] = asyncio.Lock()
    return client_start_locks[key]


async def claim_message(chat_id: int, message_id: int) -> bool:
    """Атомарно захватывает сообщение в обработку."""
    key = (chat_id, message_id)
    async with message_claim_lock:
        if key in processed_messages or key in processing_messages:
            return False
        processing_messages.add(key)
        return True


async def finish_message(chat_id: int, message_id: int):
    """Помечает сообщение как окончательно завершенное."""
    key = (chat_id, message_id)
    async with message_claim_lock:
        processing_messages.discard(key)
        processed_messages.add(key)
        if len(processed_messages) > 20000:
            copy_list = list(processed_messages)
            processed_messages.clear()
            processed_messages.update(copy_list[-10000:])
