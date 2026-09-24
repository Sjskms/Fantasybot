# services/forwarder/supervisor.py
import asyncio
import logging
import aiosqlite
from pyrogram import Client
from pyrogram.handlers import MessageHandler

from config import API_ID, API_HASH
from database import Database
from services.logging_service import log_event
from services.forwarder.state import (
    active_forwarder_tasks,
    loaded_configs,
    running_configs,
    client_instances,
    set_running_config,
    get_client_start_lock,
)
from services.forwarder.engine import handle_incoming_message, keep_channels_alive

logger = logging.getLogger(__name__)


async def update_live_config(user_id: int, session_name: str):
    """Загружает свежую конфигурацию сессии из БД в память."""
    configs = await Database.get_session_configs(user_id, session_name)
    loaded_configs[(user_id, session_name)] = configs or {}


async def stop_forwarder(user_id: int, session_name: str) -> None:
    """Безопасно останавливает Pyrogram-клиент."""
    task_key = (user_id, session_name)
    client = client_instances.pop(task_key, None)

    if client is None:
        return

    try:
        if client.is_connected:
            await client.stop()
        
        await log_event(
            "forwarding_disabled", 
            f"🔴 <b>Сессия остановлена</b>\nПользователь: <code>{user_id}</code>\nИмя: <code>{session_name}</code>",
            user_id=user_id
        )
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка при остановке сессии {session_name}: {e}", user_id=user_id)


async def start_forwarder_for_session(user_id: int, session_name: str, api_id: int, api_hash: str):
    """
    Запускает Pyrogram-клиент с защитой от параллельных запусков (Lock) 
    и проверкой уже работающего подключения.
    """
    task_key = (user_id, session_name)
    
    # 1. Проверяем, не запущен ли клиент прямо сейчас
    existing_client = client_instances.get(task_key)
    if existing_client is not None:
        if existing_client.is_connected:
            logger.warning("Сессия %s уже подключена и работает. Повторный старт отменен.", session_name)
            return
        client_instances.pop(task_key, None)

    # 2. Получаем асинхронный лок для предотвращения гонки процессов
    start_lock = get_client_start_lock(user_id, session_name)
    if start_lock.locked():
        logger.warning("Сессия %s уже запускается в параллельном потоке. Пропуск.", session_name)
        return

    async with start_lock:
        # Повторная проверка под мьютексом
        existing_client = client_instances.get(task_key)
        if existing_client is not None:
            if existing_client.is_connected:
                return
            client_instances.pop(task_key, None)

        session_string = await Database.get_session_string(user_id, session_name)
        if not session_string:
            return

        await update_live_config(user_id, session_name)
        session_config = loaded_configs.get(task_key, {})
        set_running_config(user_id, session_name, session_config)

        app = Client(
            name=f"session_{user_id}_{session_name}",
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            in_memory=True
        )
        client_instances[task_key] = app

        async def on_message_wrapper(cli, msg):
            await handle_incoming_message(cli, msg, user_id, session_name)

        app.add_handler(MessageHandler(on_message_wrapper))

        try:
            await app.start()
            
            start_msg = f"🟢 <b>Сессия запущена</b>\nПользователь: <code>{user_id}</code>\nИмя: <code>{session_name}</code>"
            await log_event("forwarding_enabled", start_msg, user_id=user_id)
            
            async for _ in app.get_dialogs():
                pass

            keep_alive_task = asyncio.create_task(keep_channels_alive(app, user_id, session_name))
            try:
                await asyncio.Event().wait()
            finally:
                keep_alive_task.cancel()

        except Exception as e:
            # Если произошел конфликт параллельных сессий AUTH_KEY_DUPLICATED
            if e.__class__.__name__ == "AuthKeyDuplicated" or "AUTH_KEY_DUPLICATED" in str(e):
                err_msg = (
                    f"❌ <b>Критическая ошибка сессии: {session_name}</b>\n\n"
                    f"⚠️ <code>[406 AUTH_KEY_DUPLICATED]</code>\n"
                    f"Эта сессия используется параллельно в другом процессе (например, старый процесс Python или меню каналов).\n"
                    f"Автоматический перезапуск отключен во избежание спама в Telegram."
                )
                await log_event("bot_error", err_msg, user_id=user_id)
                # Отключаем Enable_posting в базе, чтобы цикл supervisor не долбился бесконечно
                await Database.update_session_posting_status(user_id, session_name, False)
            else:
                await log_event("bot_error", f"❌ Ошибка работы сессии {session_name}: {e}", user_id=user_id)
            raise e
        finally:
            if client_instances.get(task_key) is app:
                await stop_forwarder(user_id, session_name)


async def run_forwarder_forever(user_id: int, session_name: str, api_id: int, api_hash: str):
    task_key = (user_id, session_name)
    while True:
        try:
            enabled = await Database.get_session_posting_status(user_id, session_name)
            if not enabled:
                return
            await start_forwarder_for_session(user_id, session_name, api_id, api_hash)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # При дублировании ключа выходим из вечного цикла
            if e.__class__.__name__ == "AuthKeyDuplicated" or "AUTH_KEY_DUPLICATED" in str(e):
                logger.critical("Остановка вечного цикла для %s из-за дубликата сессии.", task_key)
                client_instances.pop(task_key, None)
                return
            await asyncio.sleep(10)


async def restart_session_gracefully(user_id: int, session_name: str, api_id: int, api_hash: str) -> bool:
    task_key = (user_id, session_name)
    old_task = active_forwarder_tasks.pop(task_key, None)
    if old_task:
        old_task.cancel()
        try:
            await old_task
        except Exception:
            pass

    await stop_forwarder(user_id, session_name)
    await asyncio.sleep(2)

    enabled = await Database.get_session_posting_status(user_id, session_name)
    if not enabled:
        return False

    set_running_config(user_id, session_name, await Database.get_session_configs(user_id, session_name))
    task = asyncio.create_task(run_forwarder_forever(user_id, session_name, api_id, api_hash))
    active_forwarder_tasks[task_key] = task
    return True


async def safe_restart_forwarder(user_id: int, session_name: str, api_id: int, api_hash: str, delay: float = 3.0):
    await asyncio.sleep(delay)
    await restart_session_gracefully(user_id, session_name, api_id, api_hash)


async def restore_active_forwarders():
    """Восстанавливает работу активных юзерботов при запуске бота."""
    try:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id, session_name FROM user_sessions WHERE Enable_posting = 1") as cursor:
                sessions = await cursor.fetchall()
        for user_id, session_name in sessions:
            task_key = (user_id, session_name)
            active_forwarder_tasks[task_key] = asyncio.create_task(run_forwarder_forever(user_id, session_name, API_ID, API_HASH))
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка восстановления сессий: {e}")
