import asyncio
import copy
import logging
import re
from html import escape
import aiosqlite
from html import escape

import requests

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

from database import Database

import config

API_ID = config.API_ID
API_HASH = config.API_HASH
ADMIN_ID = getattr(config, "ADMIN_ID", [])

# --- ГЛОБАЛЬНЫЕ РЕЕСТРЫ СОСТОЯНИЯ ---
active_forwarder_tasks: dict[tuple[int, str], asyncio.Task] = {}
loaded_configs: dict[tuple[int, str], dict] = {}
running_configs: dict[tuple[int, str], dict] = {}
media_group_buffers: dict[str, dict] = {}
client_instances: dict[tuple[int, str], Client] = {}

bot_instance = None


def set_bot_instance(bot):
    """Устанавливает экземпляр Aiogram-бота для отправки логов."""
    global bot_instance
    bot_instance = bot
    
    
async def keep_channels_alive(client: Client, user_id: int, session_name: str):
    """
    Фоновая задача: каждые 45 секунд опрашивает каналы экспорта.
    Это заставляет Telegram передавать события из каналов, где аккаунт не админ.
    """
    await asyncio.sleep(5) # Ждем полного старта клиента
    while client.is_connected:
        try:
            full_config = loaded_configs.get((user_id, session_name), {})
            channels_config = full_config.get("channels", {})
            export_channels = get_export_channels(channels_config)

            for chat_id in export_channels.keys():
                try:
                    # Запрашиваем последнее сообщение чата. 
                    # Это заставляет MTProto обновить PTS канала и включить подписку на апдейты!
                    async for _ in client.get_chat_history(chat_id, limit=1):
                        break
                    await asyncio.sleep(1) # Небольшая пауза между каналами от флуда
                except Exception as e:
                    logging.debug(f"Ошибка пинга канала {chat_id}: {e}")

            # Опрашиваем раз в 45 секунд
            await asyncio.sleep(45)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Ошибка в цикле keep_channels_alive: {e}")
            await asyncio.sleep(30)
            
                
                        

async def send_debug_log(text: str) -> None:
    """
    Временный диагностический лог.
    Отправляет сообщения администраторам независимо от настроек logging.
    """
    if not bot_instance:
        logging.warning("bot_instance еще не установлен")
        return

    if not ADMIN_ID:
        logging.warning("ADMIN_ID не настроен")
        return

    await bot_instance.send_message(
                chat_id=ADMIN_ID,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    
            
            
            
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


async def stop_forwarder(user_id: int, session_name: str) -> None:
    """Безопасно останавливает Pyrogram-клиент и удаляет его из памяти."""
    task_key = (user_id, session_name)
    client = client_instances.pop(task_key, None)

    if client is None:
        return

    try:
        if client.is_connected:
            await client.stop()
        logging.info("Pyrogram-клиент остановлен: %s", session_name)
    except Exception:
        logging.exception("Ошибка при остановке Pyrogram-клиента: %s", session_name)


async def update_live_config(user_id: int, session_name: str):
    """Загружает свежую конфигурацию сессии из БД в память."""
    configs = await Database.get_session_configs(user_id, session_name)
    loaded_configs[(user_id, session_name)] = configs or {}
    logging.info("Конфиг для '%s' загружен из БД", session_name)


async def send_user_log(
    user_id: int,
    log_type: str,
    message_text: str,
    session_configs: dict,
):
    """Отправляет сообщение-лог пользователю в личный чат с ботом."""
    if not bot_instance:
        return

    logging_config = session_configs.get("logging", {})
    if not logging_config.get("enabled", False):
        return

    if log_type == "success" and not logging_config.get("log_success", True):
        return
    if log_type == "filtered" and not logging_config.get("log_filtered", False):
        return
    if log_type == "error" and not logging_config.get("log_errors", True):
        return

    try:
        await bot_instance.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Не удалось отправить лог пользователю %s", user_id)


def is_transform_needed(transform_config: dict) -> bool:
    """Проверяет, требуются ли изменения в тексте сообщения."""
    if not isinstance(transform_config, dict):
        return False

    mode = transform_config.get("mode", "keep")
    custom_text = str(transform_config.get("custom_text", "")).strip()
    replace_words = transform_config.get("replace_words", [])
    replace_links = transform_config.get("replace_links", [])

    return bool(
        (mode != "keep" and custom_text)
        or replace_words
        or replace_links
    )


def get_message_html(message: Message) -> tuple[str, bool]:
    """Извлекает исходный HTML-текст или подпись из сообщения."""
    is_media = bool(message.media)
    source = message.caption if is_media else message.text
    html_text = getattr(source, "html", None) or source or ""
    return html_text, is_media


def transform_text(
    original_html: str,
    transform_config: dict,
    is_media: bool = False,
) -> str:
    """Выполняет замену слов, ссылок и подстановку текста по настройкам."""
    if not isinstance(transform_config, dict):
        return original_html or ""

    text = original_html or ""

    # 1. Замена ссылок
    for item in transform_config.get("replace_links", []):
        old_link = item.get("from")
        new_link = item.get("to")
        if old_link:
            text = text.replace(old_link, new_link or "")

    # 2. Замена слов (без учета регистра)
    for item in transform_config.get("replace_words", []):
        old_word = item.get("from")
        new_word = item.get("to")
        if old_word:
            text = re.sub(
                re.escape(old_word),
                new_word or "",
                text,
                flags=re.IGNORECASE,
            )

    # 3. Кастомный текст (добавление / замена)
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
    if not isinstance(export_filters, dict):
        logging.warning(
            "Фильтры имеют неправильный тип: %s",
            type(export_filters).__name__,
        )
        return False

    def get_rules(key: str):
        value = export_filters.get(key, {})

        if isinstance(value, dict):
            try:
                min_value = int(value.get("min", 0))
                max_value = int(value.get("max", 999999999))
            except (TypeError, ValueError):
                min_value = 0
                max_value = 999999999

            return (
                bool(value.get("enabled", False)),
                min_value,
                max_value,
            )

        if isinstance(value, bool):
            return value, 0, 999999999

        return False, 0, 999999999

    if message.text and not message.media:
        enabled, min_value, max_value = get_rules("text")
        length = len(message.text)

        logging.info(
            "Проверка text: enabled=%s, length=%s, range=%s-%s",
            enabled,
            length,
            min_value,
            max_value,
        )

        return enabled and min_value <= length <= max_value

    if message.video:
        enabled, min_value, max_value = get_rules("videos")
        duration = message.video.duration or 0

        logging.info(
            "Проверка video: enabled=%s, duration=%s, range=%s-%s",
            enabled,
            duration,
            min_value,
            max_value,
        )

        return enabled and min_value <= duration <= max_value

    if message.photo:
        enabled, min_value, max_value = get_rules("photos")
        file_size = message.photo.file_size or 0

        logging.info(
            "Проверка photo: enabled=%s, size=%s, range=%s-%s",
            enabled,
            file_size,
            min_value,
            max_value,
        )

        return enabled and min_value <= file_size <= max_value

    if message.video_note:
        enabled, min_value, max_value = get_rules("video_notes")
        duration = message.video_note.duration or 0

        return enabled and min_value <= duration <= max_value

    if message.voice:
        enabled, min_value, max_value = get_rules("voices")
        duration = message.voice.duration or 0

        return enabled and min_value <= duration <= max_value

    if message.audio:
        enabled, min_value, max_value = get_rules("music")
        duration = message.audio.duration or 0

        return enabled and min_value <= duration <= max_value

    if message.document:
        enabled, min_value, max_value = get_rules("documents")
        file_size = message.document.file_size or 0

        return enabled and min_value <= file_size <= max_value

    logging.info(
        "Сообщение %s имеет неподдерживаемый тип медиа",
        getattr(message, "id", None),
    )
    return False


def get_export_channels(channels_config: dict) -> dict[int, dict]:
    """
    Возвращает все каналы, у которых включен режим export.

    Результат:
    {
        -1001234567890: {
            "enabled": True,
            "filters": {...}
        }
    }
    """
    result = {}

    if not isinstance(channels_config, dict):
        logging.error(
            "Раздел channels имеет неправильный тип: %s",
            type(channels_config).__name__,
        )
        return result

    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            logging.warning(
                "Некорректный ID канала в session_configs_json: %r",
                raw_id,
            )
            continue

        if not isinstance(channel_data, dict):
            logging.warning(
                "Конфигурация канала %s имеет неправильный тип",
                chat_id,
            )
            continue

        modes = channel_data.get("modes", {})
        if not isinstance(modes, dict):
            continue

        export_mode = modes.get("export", {})
        if not isinstance(export_mode, dict):
            continue

        if export_mode.get("enabled", False):
            result[chat_id] = export_mode

            logging.info(
                "Активный источник экспорта: id=%s, title=%s",
                chat_id,
                channel_data.get("title", "Без названия"),
            )

    return result


def get_post_channels(channels_config: dict) -> list[int]:
    """Извлекает каналы, в которые включен постинг (цели)."""
    result = []
    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        post_mode = channel_data.get("modes", {}).get("post", {})
        if isinstance(post_mode, dict) and post_mode.get("enabled", False):
            result.append(chat_id)

    return result


def get_channel_title(chat_id: int, channels_config: dict, fallback_title: str = None) -> str:
    """Возвращает название канала из конфигурации или запасное имя."""
    raw_id = str(chat_id)
    cdata = channels_config.get(raw_id, {})
    title = cdata.get("title") or fallback_title or f"Канал {chat_id}"
    return escape(str(title))


def make_message_link(chat_id: int, message_id: int, username: str = None) -> str:
    """Генерирует ссылку на сообщение в Telegram (публичную или приватную)."""
    if username:
        clean_user = username.lstrip("@")
        return f"https://t.me/{clean_user}/{message_id}"
    
    # Для приватных каналов/супергрупп (ID начинается с -100)
    str_id = str(chat_id)
    if str_id.startswith("-100"):
        internal_id = str_id[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    
    # Для обычных групп/чатов
    clean_id = str_id.lstrip("-")
    return f"https://t.me/c/{clean_id}/{message_id}"


async def forward_album_delayed(
    buffer_key: str,
    client: Client,
    user_id: int,
    session_configs: dict,
):
    """Сборка и пересылка альбомов (медиагрупп) во все целевые каналы постинга."""
    await asyncio.sleep(1.5)

    buffer = media_group_buffers.pop(buffer_key, None)
    if not buffer:
        return

    messages: list[Message] = buffer["messages"]
    target_chats: list[int] = buffer.get("target_chats", [])
    transform_config = buffer.get("transform_config", {})
    source_chat = buffer.get("source_chat")

    if not target_chats:
        return

    messages.sort(key=lambda item: item.id)
    channels_cfg = session_configs.get("channels", {})
    
    source_title = get_channel_title(
        source_chat,
        channels_cfg,
        fallback_title=getattr(messages[0].chat, "title", None)
    )
    src_link = make_message_link(
        source_chat,
        messages[0].id,
        getattr(messages[0].chat, "username", None)
    )

    for target_chat_id in target_chats:
        target_title = get_channel_title(target_chat_id, channels_cfg)
        try:
            sent_msg_id = None
            if not is_transform_needed(transform_config):
                copied = await client.copy_media_group(
                    chat_id=target_chat_id,
                    from_chat_id=source_chat,
                    message_id=messages[0].id,
                )
                if copied and isinstance(copied, list) and len(copied) > 0:
                    sent_msg_id = copied[0].id
            else:
                original_caption = ""
                for message in messages:
                    caption = getattr(message.caption, "html", None) or message.caption or ""
                    if caption:
                        original_caption = caption
                        break

                final_caption = transform_text(
                    original_caption,
                    transform_config,
                    is_media=True,
                )

                media = []
                for index, message in enumerate(messages):
                    caption = final_caption if index == 0 else ""
                    parse_mode = ParseMode.HTML if index == 0 and caption else None

                    if message.photo:
                        media.append(
                            InputMediaPhoto(
                                media=message.photo.file_id,
                                caption=caption,
                                parse_mode=parse_mode,
                            )
                        )
                    elif message.video:
                        media.append(
                            InputMediaVideo(
                                media=message.video.file_id,
                                caption=caption,
                                parse_mode=parse_mode,
                            )
                        )
                    elif message.audio:
                        media.append(
                            InputMediaAudio(
                                media=message.audio.file_id,
                                caption=caption,
                                parse_mode=parse_mode,
                            )
                        )
                    elif message.document:
                        media.append(
                            InputMediaDocument(
                                media=message.document.file_id,
                                caption=caption,
                                parse_mode=parse_mode,
                            )
                        )

                if media:
                    sent = await client.send_media_group(
                        chat_id=target_chat_id,
                        media=media,
                    )
                    if sent and isinstance(sent, list) and len(sent) > 0:
                        sent_msg_id = sent[0].id

            # Ссылка на отправленное сообщение в канале постинга (если доступен ID)
            if sent_msg_id:
                dest_link = make_message_link(target_chat_id, sent_msg_id)
                dest_str = f"<b>{target_title}</b> [<code>{target_chat_id}</code>] (<a href='{dest_link}'>#{sent_msg_id}</a>)"
            else:
                dest_str = f"<b>{target_title}</b> [<code>{target_chat_id}</code>]"

            log_msg = (
                f"✅ <b>Альбом переслан</b> ({len(messages)} медиа)\n"
                f"📤 <b>Из:</b> <b>{source_title}</b> [<code>{source_chat}</code>] (<a href='{src_link}'>#{messages[0].id}</a>)\n"
                f"📥 <b>В:</b> {dest_str}"
            )
            await send_user_log(user_id, "success", log_msg, session_configs)

        except Exception as error:
            logging.exception("Ошибка отправки альбома в канал %s", target_chat_id)
            await send_user_log(
                user_id,
                "error",
                (
                    f"❌ <b>Ошибка отправки альбома</b>\n"
                    f"📤 <b>Из:</b> <b>{source_title}</b> [<code>{source_chat}</code>]\n"
                    f"📥 <b>В:</b> <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
                    f"⚠️ <b>Ошибка:</b> <code>{escape(str(error))}</code>"
                ),
                session_configs,
            )

async def keep_channels_alive(client: Client, user_id: int, session_name: str):
    """Фоновый пинг каналов-источников, чтобы Telegram не отправлял их в спящий режим."""
    await asyncio.sleep(5)
    while client.is_connected:
        try:
            full_config = loaded_configs.get((user_id, session_name), {})
            export_channels = get_export_channels(full_config.get("channels", {}))

            for chat_id in export_channels.keys():
                try:
                    async for _ in client.get_chat_history(chat_id, limit=1):
                        break
                    await asyncio.sleep(1)
                except Exception:
                    pass

            await asyncio.sleep(45)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Ошибка в цикле keep_channels_alive: {e}")
            await asyncio.sleep(30)


async def warmup_and_activate_channels(client: Client, channels_config: dict, session_name: str):
    """Инициализирует кэш пиров и подтягивает PTS каждого канала экспорта."""
    logging.info("Pyrogram-клиент запущен: %s. Подгружаем диалоги...", session_name)
    async for _ in client.get_dialogs():
        pass

    export_channels = get_export_channels(channels_config)
    for ch_id in export_channels.keys():
        try:
            await client.get_chat(ch_id)
            async for _ in client.get_chat_history(ch_id, limit=1):
                break
            logging.info("Канал-экспорта %s успешно активирован в MTProto!", ch_id)
        except Exception as ex:
            logging.warning("Не удалось активировать канал %s: %s", ch_id, ex)

    logging.info("Кэш пиров каналов для сессии '%s' полностью сформирован!", session_name)


async def forward_single_message(
    client: Client,
    message: Message,
    target_chat_id: int,
    transform_config: dict,
    user_id: int,
    source_chat_id: int,
    source_title: str,
    src_link: str,
    full_config: dict,
):
    """Отправляет одиночное сообщение (текст или медиа) в один целевой канал постинга."""
    channels_config = full_config.get("channels", {})
    target_title = get_channel_title(target_chat_id, channels_config)

    try:
        orig_html, is_media = get_message_html(message)
        new_text = transform_text(orig_html, transform_config, is_media=is_media)
        sent_msg = None

        if not is_transform_needed(transform_config):
            sent_msg = await message.copy(chat_id=target_chat_id)
        elif not is_media:
            sent_msg = await client.send_message(
                chat_id=target_chat_id,
                text=new_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        else:
            sent_msg = await message.copy(
                chat_id=target_chat_id,
                caption=new_text,
                parse_mode=ParseMode.HTML,
            )

        if sent_msg and hasattr(sent_msg, "id"):
            dest_link = make_message_link(target_chat_id, sent_msg.id)
            dest_str = f"<b>{target_title}</b> [<code>{target_chat_id}</code>] (<a href='{dest_link}'>#{sent_msg.id}</a>)"
        else:
            dest_str = f"<b>{target_title}</b> [<code>{target_chat_id}</code>]"

        log_msg = (
            f"✅ <b>Сообщение переслано</b>\n"
            f"📤 <b>Из:</b> <b>{source_title}</b> [<code>{source_chat_id}</code>] (<a href='{src_link}'>#{message.id}</a>)\n"
            f"📥 <b>В:</b> {dest_str}"
        )
        await send_user_log(user_id, "success", log_msg, full_config)

    except Exception as error:
        logging.exception("Ошибка отправки сообщения %s в %s", message.id, target_chat_id)
        await send_user_log(
            user_id,
            "error",
            (
                f"❌ <b>Ошибка отправки</b>\n"
                f"📤 <b>Из:</b> <b>{source_title}</b> [<code>{source_chat_id}</code>] (<a href='{src_link}'>#{message.id}</a>)\n"
                f"📥 <b>В:</b> <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
                f"⚠️ <b>Ошибка:</b> <code>{escape(str(error))}</code>"
            ),
            full_config,
        )


async def handle_incoming_message(client: Client, message: Message, user_id: int, session_name: str):
    """Основной обработчик события входящего сообщения: фильтрация и маршрутизация."""
    full_config = copy.deepcopy(loaded_configs.get((user_id, session_name), {}))

    try:
        channels_config = full_config.get("channels", {})
        export_channels = get_export_channels(channels_config)
        target_chats = get_post_channels(channels_config)

        if not message.chat:
            return

        source_chat_id = int(message.chat.id)
        export_mode = export_channels.get(source_chat_id)

        # Сообщение пришло из канала, которого нет в экспорте
        if export_mode is None or not target_chats:
            return

        source_title = get_channel_title(
            source_chat_id,
            channels_config,
            fallback_title=getattr(message.chat, "title", None),
        )
        src_link = make_message_link(
            source_chat_id,
            message.id,
            getattr(message.chat, "username", None),
        )

        # Проверка фильтров контента
        export_filters = export_mode.get("filters", {})
        if not is_message_allowed(message, export_filters):
            logging.info("Сообщение %s из чата %s отфильтровано", message.id, source_chat_id)
            log_msg = (
                f"ℹ️ <b>Сообщение отфильтровано</b>\n"
                f"📤 <b>Канал:</b> <b>{source_title}</b> [<code>{source_chat_id}</code>]\n"
                f"🔗 <b>Пост:</b> <a href='{src_link}'>#{message.id}</a>"
            )
            await send_user_log(user_id, "filtered", log_msg, full_config)
            return

        transform_config = export_mode.get("text_transform", full_config.get("text_transform", {}))

        # Обработка альбомов
        if message.media_group_id:
            buffer_key = f"{source_chat_id}:{message.media_group_id}"
            if buffer_key not in media_group_buffers:
                task = asyncio.create_task(
                    forward_album_delayed(buffer_key, client, user_id, full_config)
                )
                media_group_buffers[buffer_key] = {
                    "messages": [message],
                    "task": task,
                    "target_chats": target_chats.copy(),
                    "transform_config": transform_config,
                    "source_chat": source_chat_id,
                }
            else:
                media_group_buffers[buffer_key]["messages"].append(message)
            return

        # Пересылка одиночного сообщения по всем целевым каналам постинга
        for target_chat_id in target_chats:
            await forward_single_message(
                client=client,
                message=message,
                target_chat_id=target_chat_id,
                transform_config=transform_config,
                user_id=user_id,
                source_chat_id=source_chat_id,
                source_title=source_title,
                src_link=src_link,
                full_config=full_config,
            )

    except Exception as error:
        logging.exception("Критическая ошибка обработки сообщения")
        await send_user_log(
            user_id,
            "error",
            f"❌ <b>Критическая ошибка обработки</b>\nОшибка: <code>{escape(str(error))}</code>",
            full_config,
        )


async def start_forwarder_for_session(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
):
    """Инициализация, запуск и поддержание жизненного цикла клиента Pyrogram."""
    session_string = await Database.get_session_string(user_id, session_name)
    if not session_string:
        logging.error("Строка сессии не найдена: %s", session_name)
        return

    await update_live_config(user_id, session_name)
    session_config = loaded_configs.get((user_id, session_name), {})
    set_running_config(user_id, session_name, session_config)

    app = Client(
        name=f"session_{user_id}_{session_name}",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True,
    )

    task_key = (user_id, session_name)

    # Безопасная остановка старого инстанса, если был
    old_client = client_instances.get(task_key)
    if old_client is not None:
        try:
            if old_client.is_connected:
                await old_client.stop()
        except Exception:
            logging.exception("Ошибка при остановке старого клиента: %s", session_name)

    client_instances[task_key] = app

    # ВАЖНО: регистрируем асинхронный хэндлер
    async def on_message_wrapper(cli: Client, msg: Message):
        await handle_incoming_message(cli, msg, user_id, session_name)

    app.add_handler(MessageHandler(on_message_wrapper))

    try:
        await app.start()

        # 1. Прогрев и открытие каналов
        await warmup_and_activate_channels(app, session_config.get("channels", {}), session_name)

        # 2. Запуск фоновой поддержки соединения с каналами (Heartbeat)
        keep_alive_task = asyncio.create_task(
            keep_channels_alive(app, user_id, session_name),
            name=f"keepalive:{user_id}:{session_name}",
        )

        try:
            # Ожидание событий (слушаем апдейты вечно)
            await asyncio.Event().wait()
        finally:
            keep_alive_task.cancel()

    except asyncio.CancelledError:
        logging.info("Pyrogram-клиент остановлен: %s", session_name)
        raise
    except Exception:
        logging.exception("Ошибка Pyrogram-клиента: %s", session_name)
        raise
    finally:
        current_client = client_instances.get(task_key)
        if current_client is app:
            await stop_forwarder(user_id, session_name)


async def run_forwarder_forever(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
):
    """Супервизор: перезапускает пересылку при обрыве соединения или ошибках."""
    retry_delay = 5

    while True:
        try:
            enabled = await Database.get_session_posting_status(user_id, session_name)
            if not enabled:
                logging.info("Пересылка отключена: %s", session_name)
                return

            await start_forwarder_for_session(user_id, session_name, api_id, api_hash)
            logging.warning("Forwarder завершился. Повтор через %s секунд", retry_delay)
            await asyncio.sleep(retry_delay)

        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Ошибка forwarder. Повтор через %s секунд", retry_delay)
            await asyncio.sleep(retry_delay)


async def restart_session_gracefully(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
) -> bool:
    """Безопасный и плавный перезапуск процесса юзербота."""
    task_key = (user_id, session_name)

    old_task = active_forwarder_tasks.pop(task_key, None)
    if old_task:
        old_task.cancel()
        try:
            await old_task
        except (asyncio.CancelledError, Exception):
            pass

    await stop_forwarder(user_id, session_name)
    await asyncio.sleep(2)

    enabled = await Database.get_session_posting_status(user_id, session_name)
    if not enabled:
        running_configs.pop(task_key, None)
        return False

    fresh_config = await Database.get_session_configs(user_id, session_name)
    set_running_config(user_id, session_name, fresh_config)

    task = asyncio.create_task(
        run_forwarder_forever(user_id, session_name, api_id, api_hash),
        name=f"forwarder:{user_id}:{session_name}",
    )
    active_forwarder_tasks[task_key] = task

    await asyncio.sleep(2)
    return True


async def safe_restart_forwarder(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
    delay: float = 3.0,
):
    """Отложенный перезапуск сессии."""
    await asyncio.sleep(delay)
    await restart_session_gracefully(user_id, session_name, api_id, api_hash)


async def restore_active_forwarders():
    """Восстанавливает активные юзерботы при старте приложения."""
    try:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                """
                SELECT user_id, session_name
                FROM user_sessions
                WHERE Enable_posting = 1
                """
            ) as cursor:
                sessions = await cursor.fetchall()

        for user_id, session_name in sessions:
            task_key = (user_id, session_name)
            task = asyncio.create_task(
                run_forwarder_forever(user_id, session_name, API_ID, API_HASH),
                name=f"forwarder:{user_id}:{session_name}",
            )
            active_forwarder_tasks[task_key] = task
            logging.info("Автопостинг восстановлен: user=%s, session=%s", user_id, session_name)

    except Exception:
        logging.exception("Ошибка восстановления автопостинга")
