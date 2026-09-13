import asyncio
import copy
import json
import logging
import re
import aiosqlite
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaDocument
)

from config import API_ID, API_HASH
from database import Database

active_forwarder_tasks: dict[tuple[int, str], asyncio.Task] = {}
loaded_configs: dict[tuple[int, str], dict] = {}
running_configs: dict[tuple[int, str], dict] = {}
media_group_buffers: dict[str, dict] = {}
restart_timers: dict[tuple[int, str], asyncio.TimerHandle] = {}

bot_instance = None


def set_bot_instance(bot):
    """Инициализация объекта бота для логов."""
    global bot_instance
    bot_instance = bot


def set_running_config(user_id: int, session_name: str, config: dict):
    """Фиксирует снимок конфига, на котором клиент РЕАЛЬНО работает прямо сейчас."""
    running_configs[(user_id, session_name)] = copy.deepcopy(config or {})


def has_session_unapplied_changes(user_id: int, session_name: str, current_db_config: dict) -> bool:
    """Проверяет, отличается ли текущий конфиг в БД от работающего в памяти."""
    running = running_configs.get((user_id, session_name))
    if running is None:
        return False
    return running != (current_db_config or {})


async def update_live_config(user_id: int, session_name: str):
    """Синхронизирует конфиг из БД в память."""
    configs = await Database.get_session_configs(user_id, session_name)
    loaded_configs[(user_id, session_name)] = configs or {}
    logging.info(f"Конфиг для '{session_name}' синхронизирован в памяти.")


async def send_user_log(user_id: int, log_type: str, message_text: str, session_configs: dict):
    """Отправляет лог пользователю в личные сообщения через Aiogram Бот."""
    if not bot_instance:
        return

    log_settings = session_configs.get("logging", {})
    if not log_settings.get("enabled", False):
        return

    if log_type == "success" and not log_settings.get("log_success", True):
        return
    if log_type == "filtered" and not log_settings.get("log_filtered", False):
        return
    if log_type == "error" and not log_settings.get("log_errors", True):
        return

    try:
        await bot_instance.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Не удалось отправить лог пользователю {user_id}: {e}")


def is_transform_needed(transform_config: dict) -> bool:
    """Проверяет, требуется ли трансформация текста."""
    if not transform_config:
        return False
    mode = transform_config.get("mode", "keep")
    custom_text = transform_config.get("custom_text", "").strip()
    replace_words = transform_config.get("replace_words", [])
    replace_links = transform_config.get("replace_links", [])

    if mode != "keep" and custom_text:
        return True
    if replace_words or replace_links:
        return True
    return False


def get_message_html(message: Message) -> tuple[str, bool]:
    """Извлекает текст или подпись сообщения с сохранением исходных HTML-ссылок."""
    is_media = bool(message.media)
    if is_media:
        html_text = getattr(message.caption, "html", None) or message.caption or ""
    else:
        html_text = getattr(message.text, "html", None) or message.text or ""
    return html_text, is_media


def transform_text(original_html: str, transform_config: dict, is_media: bool = False) -> str:
    """Обрабатывает HTML-текст с сохранением существующих встроенных ссылок."""
    if not transform_config:
        return original_html or ""

    text = original_html or ""

    replace_links = transform_config.get("replace_links", [])
    for item in replace_links:
        old_link = item.get("from")
        new_link = item.get("to")
        if old_link and new_link:
            text = text.replace(old_link, new_link)

    replace_words = transform_config.get("replace_words", [])
    for item in replace_words:
        old_word = item.get("from")
        new_word = item.get("to")
        if old_word:
            text = re.sub(re.escape(old_word), new_word or "", text, flags=re.IGNORECASE)

    mode = transform_config.get("mode", "keep")
    custom_text = transform_config.get("custom_text", "")

    if mode == "replace":
        if is_media:
            text = custom_text
    elif mode == "append" and custom_text:
        text = f"{text}\n\n{custom_text}" if text else custom_text
    elif mode == "prepend" and custom_text:
        text = f"{custom_text}\n\n{text}" if text else custom_text

    return text.strip()


def is_message_allowed(message: Message, export_filters: dict) -> bool:
    """Проверка лимитов (символы, секунды, байты)."""
    text_content = message.text or message.caption or ""

    def get_rules(key: str):
        f = export_filters.get(key, {})
        if isinstance(f, dict):
            return f.get("enabled", False), f.get("min", 0), f.get("max", 999999)
        return bool(f), 0, 999999

    if message.text and not message.media:
        enabled, min_v, max_v = get_rules("text")
        return enabled and (min_v <= len(text_content) <= max_v)

    if message.media:
        if message.caption:
            enabled, min_v, max_v = get_rules("text")
            if enabled and not (min_v <= len(message.caption) <= max_v):
                return False

        if message.photo:
            enabled, min_v, max_v = get_rules("photos")
            return enabled and (min_v <= (message.photo.file_size or 0) <= max_v)
        elif message.video:
            enabled, min_v, max_v = get_rules("videos")
            return enabled and (min_v <= (message.video.duration or 0) <= max_v)
        elif message.video_note:
            enabled, min_v, max_v = get_rules("video_notes")
            return enabled and (min_v <= (message.video_note.duration or 0) <= max_v)
        elif message.voice:
            enabled, min_v, max_v = get_rules("voices")
            return enabled and (min_v <= (message.voice.duration or 0) <= max_v)
        elif message.audio:
            enabled, min_v, max_v = get_rules("music")
            return enabled and (min_v <= (message.audio.duration or 0) <= max_v)
        elif message.document:
            enabled, min_v, max_v = get_rules("documents")
            return enabled and (min_v <= (message.document.file_size or 0) <= max_v)

    return True


async def forward_album_delayed(media_group_id: str, client: Client, user_id: int, session_configs: dict):
    """Сборка и отправка альбома со строгой привязкой одного описания к первому медиа."""
    await asyncio.sleep(1.5)
    buf = media_group_buffers.pop(media_group_id, None)
    if not buf:
        return

    messages: list[Message] = buf["messages"]
    target_chat_id = buf["target_chat"]
    transform_config = buf.get("transform_config", {})

    try:
        messages.sort(key=lambda m: m.id)
        source_chat = messages[0].chat.id

        if not is_transform_needed(transform_config):
            message_ids = [m.id for m in messages]
            await client.copy_media_group(
                chat_id=target_chat_id,
                from_chat_id=source_chat,
                message_id=message_ids[0]
            )
        else:
            orig_caption_html = ""
            for msg in messages:
                cap = getattr(msg.caption, "html", None) or msg.caption or ""
                if cap:
                    orig_caption_html = cap
                    break

            final_caption = transform_text(orig_caption_html, transform_config, is_media=True)
            input_media_list = []

            for idx, msg in enumerate(messages):
                item_caption = final_caption if idx == 0 else ""
                parse_mode = ParseMode.HTML if idx == 0 and item_caption else None

                if msg.photo:
                    input_media_list.append(
                        InputMediaPhoto(
                            media=msg.photo.file_id,
                            caption=item_caption,
                            parse_mode=parse_mode
                        )
                    )
                elif msg.video:
                    input_media_list.append(
                        InputMediaVideo(
                            media=msg.video.file_id,
                            caption=item_caption,
                            parse_mode=parse_mode
                        )
                    )
                elif msg.audio:
                    input_media_list.append(
                        InputMediaAudio(
                            media=msg.audio.file_id,
                            caption=item_caption,
                            parse_mode=parse_mode
                        )
                    )
                elif msg.document:
                    input_media_list.append(
                        InputMediaDocument(
                            media=msg.document.file_id,
                            caption=item_caption,
                            parse_mode=parse_mode
                        )
                    )

            if input_media_list:
                await client.send_media_group(
                    chat_id=target_chat_id,
                    media=input_media_list
                )

        log_msg = (
            f"✅ <b>Альбом переслан!</b>\n"
            f"Элементов: {len(messages)}\n"
            f"Источник: <code>{source_chat}</code>\n"
            f"Цель: <code>{target_chat_id}</code>"
        )
        await send_user_log(user_id, "success", log_msg, session_configs)

    except Exception as e:
        err_msg = f"❌ <b>Ошибка пересылки альбома:</b>\n<code>{e}</code>"
        await send_user_log(user_id, "error", err_msg, session_configs)


async def start_forwarder_for_session(user_id: int, session_name: str, api_id: int, api_hash: str):
    """Главный процесс слушателя Pyrogram."""
    session_string = await Database.get_session_string(user_id, session_name)
    if not session_string:
        return

    await update_live_config(user_id, session_name)
    current_cfg = loaded_configs.get((user_id, session_name), {})
    set_running_config(user_id, session_name, current_cfg)

    app = Client(
        name=f"session_{user_id}_{session_name}",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True
    )

    async def message_handler(client: Client, message: Message):
        try:
            full_config = loaded_configs.get((user_id, session_name), {})
            channels_config = full_config.get("channels", {})

            chat_id = message.chat.id
            str_chat_id = str(chat_id)
            alt_chat_id = f"-100{abs(chat_id)}" if chat_id < 0 and not str_chat_id.startswith("-100") else str_chat_id

            channel_data = channels_config.get(str_chat_id) or channels_config.get(alt_chat_id)
            if not channel_data:
                return

            export_mode = channel_data.get("modes", {}).get("export", {})
            if not export_mode.get("enabled", False):
                return

            target_post_id = None
            for cid, cdata in channels_config.items():
                if cdata.get("modes", {}).get("post", {}).get("enabled", False):
                    target_post_id = int(cid)
                    break

            if not target_post_id:
                return

            export_filters = export_mode.get("filters", {})

            if not is_message_allowed(message, export_filters):
                log_msg = f"ℹ️ <b>Сообщение {message.id} отфильтровано:</b> не подходят параметры min/max или отключен тип."
                await send_user_log(user_id, "filtered", log_msg, full_config)
                return

            transform_config = export_mode.get("text_transform", full_config.get("text_transform", {}))

            if message.media_group_id:
                mg_id = message.media_group_id
                if mg_id not in media_group_buffers:
                    task = asyncio.create_task(forward_album_delayed(mg_id, client, user_id, full_config))
                    media_group_buffers[mg_id] = {
                        "messages": [message],
                        "task": task,
                        "target_chat": target_post_id,
                        "transform_config": transform_config
                    }
                else:
                    media_group_buffers[mg_id]["messages"].append(message)
                return

            if not is_transform_needed(transform_config):
                await message.copy(chat_id=target_post_id)
            else:
                orig_html, is_media_msg = get_message_html(message)
                new_text = transform_text(orig_html, transform_config, is_media=is_media_msg)

                if not is_media_msg:
                    await client.send_message(
                        chat_id=target_post_id,
                        text=new_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False
                    )
                else:
                    await message.copy(
                        chat_id=target_post_id,
                        caption=new_text,
                        parse_mode=ParseMode.HTML
                    )

            log_msg = f"✅ <b>Переслано сообщение #{message.id}</b>\nИз канала: <code>{chat_id}</code>\nВ канал: <code>{target_post_id}</code>"
            await send_user_log(user_id, "success", log_msg, full_config)

        except Exception as e:
            err_msg = f"❌ <b>Ошибка обработки сообщения #{getattr(message, 'id', '?')}:</b>\n<code>{e}</code>"
            await send_user_log(user_id, "error", err_msg, full_config)

    app.add_handler(MessageHandler(message_handler))

    try:
        await app.start()
        async for _ in app.get_dialogs():
            pass
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        if app.is_connected:
            await app.stop()


async def restart_session_gracefully(user_id: int, session_name: str, api_id: int, api_hash: str) -> bool:
    """Плавно перезапускает сессию и фиксирует новый рабочий конфиг."""
    task_key = (user_id, session_name)

    if task_key in active_forwarder_tasks:
        old_task = active_forwarder_tasks.pop(task_key)
        old_task.cancel()
        try:
            await old_task
        except (asyncio.CancelledError, Exception):
            pass

    await asyncio.sleep(2.0)

    is_active = await Database.get_session_posting_status(user_id, session_name)
    if not is_active:
        running_configs.pop(task_key, None)
        return False

    fresh_configs = await Database.get_session_configs(user_id, session_name)
    set_running_config(user_id, session_name, fresh_configs)

    new_task = asyncio.create_task(
        start_forwarder_for_session(user_id, session_name, api_id, api_hash)
    )
    active_forwarder_tasks[task_key] = new_task
    await asyncio.sleep(1.5)
    return True


async def _do_restart(user_id: int, session_name: str, api_id: int, api_hash: str):
    """Внутренняя функция для отложенного перезапуска."""
    await restart_session_gracefully(user_id, session_name, api_id, api_hash)


async def safe_restart_forwarder(user_id: int, session_name: str, api_id: int, api_hash: str, delay: float = 3.0):
    """
    Отложенный безопасный перезапуск (debounce).
    Предотвращает множественные перезапуски при серии быстрых кликов.
    """
    task_key = (user_id, session_name)

    if task_key in restart_timers:
        restart_timers[task_key].cancel()

    loop = asyncio.get_running_loop()
    timer = loop.call_later(
        delay,
        lambda: asyncio.create_task(_do_restart(user_id, session_name, api_id, api_hash))
    )
    restart_timers[task_key] = timer
    logging.info(f"Перезапуск сессии '{session_name}' запланирован через {delay} сек.")


async def restore_active_forwarders():
    """Восстановление активных пересылок при старте."""
    try:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                'SELECT user_id, session_name FROM user_sessions WHERE Enable_posting = TRUE'
            ) as cursor:
                active_sessions = await cursor.fetchall()

        for user_id, session_name in active_sessions:
            task_key = (user_id, session_name)
            task = asyncio.create_task(
                start_forwarder_for_session(
                    user_id=user_id,
                    session_name=session_name,
                    api_id=API_ID,
                    api_hash=API_HASH
                )
            )
            active_forwarder_tasks[task_key] = task
            logging.info(f"Восстановлена задача автопостинга для user={user_id}, session={session_name}")
    except Exception as e:
        logging.error(f"Ошибка при восстановлении автопостинга: {e}")
