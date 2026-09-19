import asyncio
import copy
import logging
import re
from html import escape
import aiosqlite

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaDocument,
)

from config import API_ID, API_HASH
from database import Database
from services.logging_service import log_event, get_logging_bot_instance, set_logging_bot_instance

# --- ГЛОБАЛЬНЫЕ РЕЕСТРЫ СОСТОЯНИЯ ---
active_forwarder_tasks: dict[tuple[int, str], asyncio.Task] = {}
loaded_configs: dict[tuple[int, str], dict] = {}
running_configs: dict[tuple[int, str], dict] = {}
media_group_buffers: dict[str, dict] = {}
client_instances: dict[tuple[int, str], Client] = {}

# Защита от дублей при параллельной работе MessageHandler и поллера
processed_messages: set[tuple[int, int]] = set()
processing_messages: set[tuple[int, int]] = set()
message_claim_lock = asyncio.Lock()
last_known_msg_ids: dict[int, int] = {}

bot_instance = None


def set_bot_instance(bot):
    """Устанавливает экземпляр Aiogram-бота для отправки логов."""
    global bot_instance
    bot_instance = bot
    set_logging_bot_instance(bot)


def set_running_config(user_id: int, session_name: str, config: dict):
    """Фиксирует точный снимок конфигурации, на которой запущен юзербот."""
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


async def send_user_log(
    user_id: int,
    log_type: str,
    message_text: str,
    session_configs: dict,
):
    """
    Отправляет персональный лог пользователю в Telegram.
    НЕ ЗАВИСИТ от глобальных настроек логирования админа!
    
    Поддерживает:
    - Логирование в ЛС пользователя с ботом (по умолчанию)
    - Логирование в выбранный пользователем чат/канал (если указан log_chat_id / chat_id)
    """
    bot = bot_instance or get_logging_bot_instance()
    if not bot:
        return

    logging_config = session_configs.get("logging", {})
    if not logging_config.get("enabled", True):
        return

    if log_type == "success" and not logging_config.get("log_success", True):
        return
    if log_type == "filtered" and not logging_config.get("log_filtered", False):
        return
    if log_type == "error" and not logging_config.get("log_errors", True):
        return

    # Если пользователь выбрал отдельный чат/канал для логов — слать туда, иначе в ЛС пользователю
    target_chat_id = (
        logging_config.get("log_chat_id")
        or logging_config.get("chat_id")
        or user_id
    )

    try:
        await bot.send_message(
            chat_id=target_chat_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logging.warning("Не удалось отправить лог пользователю %s в чат %s: %s", user_id, target_chat_id, e)


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


async def stop_forwarder(user_id: int, session_name: str) -> None:
    """Безопасно останавливает Pyrogram-клиент."""
    task_key = (user_id, session_name)
    client = client_instances.pop(task_key, None)

    if client is None:
        return

    try:
        if client.is_connected:
            await client.stop()
        
        # 1. Глобальный лог для админов
        await log_event(
            "forwarding_disabled", 
            f"🔴 <b>Сессия остановлена</b>\nПользователь: <code>{user_id}</code>\nИмя: <code>{session_name}</code>"
        )
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка при остановке сессии {session_name}: {e}")


async def update_live_config(user_id: int, session_name: str):
    """Загружает свежую конфигурацию сессии из БД в память."""
    configs = await Database.get_session_configs(user_id, session_name)
    loaded_configs[(user_id, session_name)] = configs or {}


def is_transform_needed(transform_config: dict) -> bool:
    """Проверяет, требуются ли изменения в тексте сообщения."""
    if not isinstance(transform_config, dict):
        return False

    mode = transform_config.get("mode", "keep")
    custom_text = str(transform_config.get("custom_text", "")).strip()
    replace_words = transform_config.get("replace_words", [])
    replace_links = transform_config.get("replace_links", [])

    return bool((mode != "keep" and custom_text) or replace_words or replace_links)


def get_message_html(message: Message) -> tuple[str, bool]:
    """Извлекает исходный HTML-текст или подпись из сообщения."""
    is_media = bool(message.media)
    source = message.caption if is_media else message.text
    html_text = getattr(source, "html", None) or source or ""
    return html_text, is_media


def transform_text(original_html: str, transform_config: dict, is_media: bool = False) -> str:
    """Выполняет замену слов, ссылок и подстановку текста по настройкам."""
    if not isinstance(transform_config, dict):
        return original_html or ""

    text = original_html or ""

    for item in transform_config.get("replace_links", []):
        old_link = item.get("from")
        new_link = item.get("to")
        if old_link:
            text = text.replace(old_link, new_link or "")

    for item in transform_config.get("replace_words", []):
        old_word = item.get("from")
        new_word = item.get("to")
        if old_word:
            text = re.sub(re.escape(old_word), new_word or "", text, flags=re.IGNORECASE)

    mode = transform_config.get("mode", "keep")
    custom_text = transform_config.get("custom_text", "")

    if mode == "replace" and is_media:
        text = custom_text
    elif mode == "append" and custom_text:
        text = f"{text}\n\n{custom_text}" if text else custom_text
    elif mode == "prepend" and custom_text:
        text = f"{custom_text}\n\n{text}" if text else custom_text

    return text.strip()


def is_message_allowed(message: Message, export_filters: dict) -> bool:
    """Проверяет соответствие сообщения заданным фильтрам."""
    if not isinstance(export_filters, dict):
        return False

    def get_rules(key: str):
        value = export_filters.get(key, {})
        if isinstance(value, dict):
            return (
                bool(value.get("enabled", False)),
                int(value.get("min", 0)),
                int(value.get("max", 999999999)),
            )
        if isinstance(value, bool):
            return value, 0, 999999999
        return False, 0, 999999999

    if message.text and not message.media:
        enabled, min_val, max_val = get_rules("text")
        return enabled and min_val <= len(message.text) <= max_val

    if message.video or message.animation:
        enabled, min_val, max_val = get_rules("videos")
        dur = 0
        if message.video: dur = message.video.duration or 0
        elif message.animation: dur = message.animation.duration or 0
        return enabled and min_val <= dur <= max_val

    if message.photo:
        enabled, min_val, max_val = get_rules("photos")
        if max_val == 999999: max_val = 2000000000
        return enabled and min_val <= (message.photo.file_size or 0) <= max_val

    if message.document:
        enabled, min_val, max_val = get_rules("documents")
        if max_val == 999999: max_val = 2000000000
        return enabled and min_val <= (message.document.file_size or 0) <= max_val

    return False


def get_export_channels(channels_config: dict) -> dict[int, dict]:
    result = {}
    if not isinstance(channels_config, dict): return result
    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(str(raw_id).strip())
        except: continue
        export_mode = channel_data.get("modes", {}).get("export", {})
        if isinstance(export_mode, dict) and export_mode.get("enabled", False):
            result[chat_id] = export_mode
    return result


def get_post_channels(channels_config: dict) -> list[int]:
    result = []
    if not isinstance(channels_config, dict): return result
    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(str(raw_id).strip())
        except: continue
        post_mode = channel_data.get("modes", {}).get("post", {})
        if isinstance(post_mode, dict) and post_mode.get("enabled", False):
            result.append(chat_id)
    return result


def get_channel_title(chat_id: int, channels_config: dict, fallback_title: str = None) -> str:
    raw_id = str(chat_id)
    cdata = channels_config.get(raw_id, {})
    title = cdata.get("title") or fallback_title or f"Канал {chat_id}"
    return escape(str(title))


def make_message_link(chat_id: int, message_id: int, username: str = None) -> str:
    if username: return f"https://t.me/{username.lstrip('@')}/{message_id}"
    str_id = str(chat_id)
    if str_id.startswith("-100"): return f"https://t.me/c/{str_id[4:]}/{message_id}"
    return f"https://t.me/c/{str_id.lstrip('-')}/{message_id}"


async def forward_album_delayed(buffer_key: str, client: Client, user_id: int, session_configs: dict):
    await asyncio.sleep(1.5)
    buffer = media_group_buffers.pop(buffer_key, None)
    if not buffer: return

    messages: list[Message] = buffer["messages"]
    target_chats: list[int] = buffer.get("target_chats", [])
    transform_config = buffer.get("transform_config", {})
    source_chat = buffer.get("source_chat")
    messages.sort(key=lambda item: item.id)
    channels_cfg = session_configs.get("channels", {})

    source_title = get_channel_title(source_chat, channels_cfg, getattr(messages[0].chat, "title", None))
    src_link = make_message_link(source_chat, messages[0].id, getattr(messages[0].chat, "username", None))

    for target_chat_id in target_chats:
        target_title = get_channel_title(target_chat_id, channels_cfg)
        try:
            sent_msg_id = None
            if not is_transform_needed(transform_config):
                copied = await client.copy_media_group(target_chat_id, source_chat, messages[0].id)
                if copied: sent_msg_id = copied[0].id
            else:
                original_caption = ""
                for msg in messages:
                    caption = getattr(msg.caption, "html", None) or msg.caption or ""
                    if caption: original_caption = caption; break
                
                final_caption = transform_text(original_caption, transform_config, is_media=True)
                media = []
                for index, msg in enumerate(messages):
                    cap = final_caption if index == 0 else ""
                    pm = ParseMode.HTML if index == 0 and cap else None
                    if msg.photo: media.append(InputMediaPhoto(msg.photo.file_id, caption=cap, parse_mode=pm))
                    elif msg.video: media.append(InputMediaVideo(msg.video.file_id, caption=cap, parse_mode=pm))
                
                if media:
                    sent = await client.send_media_group(target_chat_id, media)
                    if sent: sent_msg_id = sent[0].id

            dest_link = make_message_link(target_chat_id, sent_msg_id) if sent_msg_id else ""
            log_msg = (
                f"✅ <b>Альбом переслан</b> ({len(messages)} медиа)\n"
                f"📤 Из: <b>{source_title}</b> (<a href='{src_link}'>#{messages[0].id}</a>)\n"
                f"📥 В: <b>{target_title}</b> (<a href='{dest_link}'>#{sent_msg_id}</a>)"
            )
            # 1. Персональный лог для пользователя (в Telegram)
            await send_user_log(user_id, "success", log_msg, session_configs)
            # 2. Глобальный лог для админов (консоль/файл/админский чат)
            await log_event("forward_success", log_msg)

        except Exception as error:
            err_msg = (
                f"❌ <b>Ошибка отправки альбома</b>\n"
                f"📤 Из: <b>{source_title}</b> [<code>{source_chat}</code>]\n"
                f"📥 В: <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
                f"⚠️ Ошибка: <code>{escape(str(error))}</code>"
            )
            await send_user_log(user_id, "error", err_msg, session_configs)
            await log_event("forward_error", err_msg)


async def forward_single_message(client: Client, message: Message, target_chat_id: int, transform_config: dict, user_id: int, source_chat_id: int, source_title: str, src_link: str, full_config: dict):
    channels_config = full_config.get("channels", {})
    target_title = get_channel_title(target_chat_id, channels_config)

    try:
        orig_html, is_media = get_message_html(message)
        new_text = transform_text(orig_html, transform_config, is_media=is_media)
        sent_msg = None

        if not is_transform_needed(transform_config):
            sent_msg = await message.copy(target_chat_id)
        elif not is_media:
            sent_msg = await client.send_message(target_chat_id, new_text, parse_mode=ParseMode.HTML)
        else:
            sent_msg = await message.copy(target_chat_id, caption=new_text, parse_mode=ParseMode.HTML)

        dest_link = make_message_link(target_chat_id, sent_msg.id) if sent_msg else ""
        log_msg = (
            f"✅ <b>Сообщение переслано</b>\n"
            f"📤 Из: <b>{source_title}</b> (<a href='{src_link}'>#{message.id}</a>)\n"
            f"📥 В: <b>{target_title}</b> (<a href='{dest_link}'>#{sent_msg.id}</a>)"
        )
        # 1. Персональный лог для пользователя (в Telegram)
        await send_user_log(user_id, "success", log_msg, full_config)
        # 2. Глобальный лог для админов (консоль/файл/админский чат)
        await log_event("forward_success", log_msg)

    except Exception as error:
        err_msg = (
            f"❌ <b>Ошибка отправки</b>\n"
            f"📤 Из: <b>{source_title}</b> [<code>{source_chat_id}</code>] (<a href='{src_link}'>#{message.id}</a>)\n"
            f"📥 В: <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
            f"⚠️ Ошибка: <code>{escape(str(error))}</code>"
        )
        await send_user_log(user_id, "error", err_msg, full_config)
        await log_event("forward_error", err_msg)


async def handle_incoming_message(client: Client, message: Message, user_id: int, session_name: str):
    if not message.chat: return
    source_chat_id = int(message.chat.id)

    if not await claim_message(source_chat_id, message.id): return

    full_config = copy.deepcopy(loaded_configs.get((user_id, session_name), {}))
    try:
        channels_config = full_config.get("channels", {})
        export_channels = get_export_channels(channels_config)
        target_chats = get_post_channels(channels_config)
        export_mode = export_channels.get(source_chat_id)

        if export_mode is None or not target_chats: return

        source_title = get_channel_title(source_chat_id, channels_config, getattr(message.chat, "title", None))
        src_link = make_message_link(source_chat_id, message.id, getattr(message.chat, "username", None))

        if not is_message_allowed(message, export_mode.get("filters", {})):
            log_msg = f"ℹ️ <b>Отфильтровано</b>\nИсточник: <b>{source_title}</b>\nПост: <a href='{src_link}'>#{message.id}</a>"
            await send_user_log(user_id, "filtered", log_msg, full_config)
            await log_event("forward_filtered", log_msg)
            return

        transform_config = export_mode.get("text_transform", full_config.get("text_transform", {}))

        if message.media_group_id:
            buffer_key = f"{source_chat_id}:{message.media_group_id}"
            if buffer_key not in media_group_buffers:
                task = asyncio.create_task(forward_album_delayed(buffer_key, client, user_id, full_config))
                media_group_buffers[buffer_key] = {"messages": [message], "task": task, "target_chats": target_chats.copy(), "transform_config": transform_config, "source_chat": source_chat_id}
            else:
                media_group_buffers[buffer_key]["messages"].append(message)
            return

        for target_chat_id in target_chats:
            await forward_single_message(client, message, target_chat_id, transform_config, user_id, source_chat_id, source_title, src_link, full_config)

    except Exception as error:
        err_msg = f"❌ Критическая ошибка в handle_incoming_message: {error}"
        await send_user_log(user_id, "error", err_msg, full_config)
        await log_event("bot_error", err_msg)
    finally:
        await finish_message(source_chat_id, message.id)


async def keep_channels_alive(client: Client, user_id: int, session_name: str):
    await asyncio.sleep(4)
    while client.is_connected:
        try:
            cfg = loaded_configs.get((user_id, session_name), {})
            exp_channels = get_export_channels(cfg.get("channels", {}))
            for ch_id in exp_channels.keys():
                try:
                    async for msg in client.get_chat_history(ch_id, limit=20):
                        last_id = last_known_msg_ids.get(ch_id, 0)
                        if msg.id <= last_id: break
                        last_known_msg_ids[ch_id] = max(last_id, msg.id)
                        asyncio.create_task(handle_incoming_message(client, msg, user_id, session_name))
                except: pass
            await asyncio.sleep(15)
        except asyncio.CancelledError: break
        except: await asyncio.sleep(10)


async def start_forwarder_for_session(user_id: int, session_name: str, api_id: int, api_hash: str):
    session_string = await Database.get_session_string(user_id, session_name)
    if not session_string: return

    await update_live_config(user_id, session_name)
    session_config = loaded_configs.get((user_id, session_name), {})
    set_running_config(user_id, session_name, session_config)

    app = Client(name=f"session_{user_id}_{session_name}", api_id=api_id, api_hash=api_hash, session_string=session_string, in_memory=True)
    task_key = (user_id, session_name)
    client_instances[task_key] = app

    async def on_message_wrapper(cli, msg): await handle_incoming_message(cli, msg, user_id, session_name)
    app.add_handler(MessageHandler(on_message_wrapper))

    try:
        await app.start()
        
        start_msg = f"🟢 <b>Сессия запущена</b>\nПользователь: <code>{user_id}</code>\nИмя: <code>{session_name}</code>"
        await send_user_log(user_id, "success", start_msg, session_config)
        await log_event("forwarding_enabled", start_msg)
        
        async for _ in app.get_dialogs(): pass

        keep_alive_task = asyncio.create_task(keep_channels_alive(app, user_id, session_name))
        try: await asyncio.Event().wait()
        finally: keep_alive_task.cancel()

    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка работы сессии {session_name}: {e}")
    finally:
        if client_instances.get(task_key) is app: await stop_forwarder(user_id, session_name)


async def run_forwarder_forever(user_id: int, session_name: str, api_id: int, api_hash: str):
    while True:
        try:
            enabled = await Database.get_session_posting_status(user_id, session_name)
            if not enabled: return
            await start_forwarder_for_session(user_id, session_name, api_id, api_hash)
            await asyncio.sleep(5)
        except asyncio.CancelledError: raise
        except: await asyncio.sleep(10)


async def restart_session_gracefully(user_id: int, session_name: str, api_id: int, api_hash: str) -> bool:
    task_key = (user_id, session_name)
    old_task = active_forwarder_tasks.pop(task_key, None)
    if old_task:
        old_task.cancel()
        try: await old_task
        except: pass

    await stop_forwarder(user_id, session_name)
    await asyncio.sleep(2)

    enabled = await Database.get_session_posting_status(user_id, session_name)
    if not enabled: return False

    set_running_config(user_id, session_name, await Database.get_session_configs(user_id, session_name))
    task = asyncio.create_task(run_forwarder_forever(user_id, session_name, api_id, api_hash))
    active_forwarder_tasks[task_key] = task
    return True


async def safe_restart_forwarder(user_id: int, session_name: str, api_id: int, api_hash: str, delay: float = 3.0):
    await asyncio.sleep(delay)
    await restart_session_gracefully(user_id, session_name, api_id, api_hash)


async def restore_active_forwarders():
    try:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id, session_name FROM user_sessions WHERE Enable_posting = 1") as cursor:
                sessions = await cursor.fetchall()
        for user_id, session_name in sessions:
            task_key = (user_id, session_name)
            active_forwarder_tasks[task_key] = asyncio.create_task(run_forwarder_forever(user_id, session_name, API_ID, API_HASH))
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка восстановления сессий: {e}")
