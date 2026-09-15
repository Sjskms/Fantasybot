import asyncio
import copy
import logging
import re
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


active_forwarder_tasks: dict[tuple[int, str], asyncio.Task] = {}
loaded_configs: dict[tuple[int, str], dict] = {}
running_configs: dict[tuple[int, str], dict] = {}
media_group_buffers: dict[str, dict] = {}

bot_instance = None
client_instances: dict[tuple[int, str], Client] = {}
client_locks: dict[tuple[int, str], asyncio.Lock] = {}

def set_bot_instance(bot):
    global bot_instance
    bot_instance = bot


def set_running_config(user_id: int, session_name: str, config: dict):
    running_configs[(user_id, session_name)] = copy.deepcopy(config or {})


def has_session_unapplied_changes(
    user_id: int,
    session_name: str,
    current_db_config: dict,
) -> bool:
    running = running_configs.get((user_id, session_name))

    if running is None:
        return False

    return running != (current_db_config or {})




async def stop_forwarder(
    user_id: int,
    session_name: str,
) -> None:
    task_key = (user_id, session_name)
    client = client_instances.pop(task_key, None)

    if client is None:
        return

    try:
        if client.is_connected:
            await client.stop()

        logging.info(
            "Pyrogram-клиент остановлен: %s",
            session_name,
        )

    except Exception:
        logging.exception(
            "Ошибка остановки клиента: %s",
            session_name,
        )
        
        

async def update_live_config(user_id: int, session_name: str):
    """
    Обновляет конфиг в памяти только для отображения/служебных задач.
    Рабочий клиент использует конфиг, загруженный при запуске.
    """
    configs = await Database.get_session_configs(user_id, session_name)
    loaded_configs[(user_id, session_name)] = configs or {}

    logging.info(
        "Конфиг для '%s' загружен из БД",
        session_name,
    )


async def send_user_log(
    user_id: int,
    log_type: str,
    message_text: str,
    session_configs: dict,
):
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
    is_media = bool(message.media)

    if is_media:
        source = message.caption
    else:
        source = message.text

    html_text = getattr(source, "html", None) or source or ""
    return html_text, is_media


def transform_text(
    original_html: str,
    transform_config: dict,
    is_media: bool = False,
) -> str:
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
            text = re.sub(
                re.escape(old_word),
                new_word or "",
                text,
                flags=re.IGNORECASE,
            )

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

    # Обычный текст
    if message.text and not message.media:
        enabled, min_value, max_value = get_rules("text")
        text_length = len(message.text)

        return (
            enabled
            and min_value <= text_length <= max_value
        )

    # Фото
    if message.photo:
        enabled, min_value, max_value = get_rules("photos")
        file_size = message.photo.file_size or 0

        return (
            enabled
            and min_value <= file_size <= max_value
        )

    # Видео
    if message.video:
        enabled, min_value, max_value = get_rules("videos")
        duration = message.video.duration or 0

        return (
            enabled
            and min_value <= duration <= max_value
        )

    # Кружок
    if message.video_note:
        enabled, min_value, max_value = get_rules("video_notes")
        duration = message.video_note.duration or 0

        return (
            enabled
            and min_value <= duration <= max_value
        )

    # Голосовое
    if message.voice:
        enabled, min_value, max_value = get_rules("voices")
        duration = message.voice.duration or 0

        return (
            enabled
            and min_value <= duration <= max_value
        )

    # Музыка
    if message.audio:
        enabled, min_value, max_value = get_rules("music")
        duration = message.audio.duration or 0

        return (
            enabled
            and min_value <= duration <= max_value
        )

    # Документ
    if message.document:
        enabled, min_value, max_value = get_rules("documents")
        file_size = message.document.file_size or 0

        return (
            enabled
            and min_value <= file_size <= max_value
        )

    return False


def get_export_channels(channels_config: dict) -> dict[int, dict]:
    result = {}

    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            logging.warning("Некорректный ID канала: %r", raw_id)
            continue

        export_mode = (
            channel_data
            .get("modes", {})
            .get("export", {})
        )

        if (
            isinstance(export_mode, dict)
            and export_mode.get("enabled", False)
        ):
            result[chat_id] = export_mode

    return result


def get_post_channels(channels_config: dict) -> list[int]:
    result = []

    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        post_mode = (
            channel_data
            .get("modes", {})
            .get("post", {})
        )

        if (
            isinstance(post_mode, dict)
            and post_mode.get("enabled", False)
        ):
            result.append(chat_id)

    return result


async def forward_album_delayed(
    buffer_key: str,
    client: Client,
    user_id: int,
    session_configs: dict,
):
    await asyncio.sleep(1.5)

    buffer = media_group_buffers.pop(buffer_key, None)

    if not buffer:
        return

    messages = buffer["messages"]
    target_chats = buffer.get("target_chats", [])
    transform_config = buffer.get("transform_config", {})
    source_chat = buffer.get("source_chat")

    messages.sort(key=lambda item: item.id)

    for target_chat_id in target_chats:
        try:
            if not is_transform_needed(transform_config):
                await client.copy_media_group(
                    chat_id=target_chat_id,
                    from_chat_id=source_chat,
                    message_id=messages[0].id,
                )
            else:
                original_caption = ""

                for message in messages:
                    caption = (
                        getattr(message.caption, "html", None)
                        or message.caption
                        or ""
                    )

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
                    parse_mode = (
                        ParseMode.HTML
                        if index == 0 and caption
                        else None
                    )

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

                    elif message.document:
                        media.append(
                            InputMediaDocument(
                                media=message.document.file_id,
                                caption=caption,
                                parse_mode=parse_mode,
                            )
                        )

                if media:
                    await client.send_media_group(
                        chat_id=target_chat_id,
                        media=media,
                    )

            await send_user_log(
                user_id,
                "success",
                (
                    "✅ <b>Альбом переслан</b>\n"
                    f"Источник: <code>{source_chat}</code>\n"
                    f"Цель: <code>{target_chat_id}</code>"
                ),
                session_configs,
            )

        except Exception as error:
            logging.exception("Ошибка отправки альбома")

            await send_user_log(
                user_id,
                "error",
                (
                    "❌ <b>Ошибка отправки альбома</b>\n"
                    f"Источник: <code>{source_chat}</code>\n"
                    f"Цель: <code>{target_chat_id}</code>\n"
                    f"Ошибка: <code>{error}</code>"
                ),
                session_configs,
            )


async def start_forwarder_for_session(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
):
    session_string = await Database.get_session_string(
        user_id,
        session_name,
    )

    if not session_string:
        logging.error(
            "Строка сессии не найдена: %s",
            session_name,
        )
        return

    await update_live_config(
        user_id,
        session_name,
    )

    session_config = loaded_configs.get(
        (user_id, session_name),
        {},
    )

    set_running_config(
        user_id,
        session_name,
        session_config,
    )

    app = Client(
        name=f"session_{user_id}_{session_name}",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True,
    )

    task_key = (user_id, session_name)

    # Не допускаем два клиента для одной сессии
    old_client = client_instances.get(task_key)

    if old_client is not None:
        try:
            if old_client.is_connected:
                await old_client.stop()
        except Exception:
            logging.exception(
                "Ошибка остановки старого клиента: %s",
                session_name,
            )

    client_instances[task_key] = app

    async def message_handler(
        client: Client,
        message: Message,
    ):
        full_config = copy.deepcopy(
            loaded_configs.get(
                (user_id, session_name),
                {},
            )
        )

        try:
            channels_config = full_config.get(
                "channels",
                {},
            )

            export_channels = get_export_channels(
                channels_config,
            )

            target_chats = get_post_channels(
                channels_config,
            )

            if not message.chat:
                logging.warning(
                    "Сообщение без чата: %s",
                    getattr(message, "id", None),
                )
                return

            source_chat_id = int(message.chat.id)

            export_mode = export_channels.get(
                source_chat_id,
            )

            if export_mode is None:
                logging.info(
                    "Сообщение из неактивного источника: %s",
                    source_chat_id,
                )
                return

            if not target_chats:
                logging.warning(
                    "Каналы назначения не выбраны: %s",
                    session_name,
                )
                return

            export_filters = export_mode.get(
                "filters",
                {},
            )

            if not is_message_allowed(
                message,
                export_filters,
            ):
                logging.info(
                    "Сообщение %s не прошло фильтр",
                    message.id,
                )

                await send_user_log(
                    user_id,
                    "filtered",
                    (
                        "ℹ️ <b>Сообщение отфильтровано</b>\n"
                        f"Источник: <code>{source_chat_id}</code>\n"
                        f"ID: <code>{message.id}</code>"
                    ),
                    full_config,
                )
                return

            transform_config = export_mode.get(
                "text_transform",
                full_config.get(
                    "text_transform",
                    {},
                ),
            )

            # Обработка альбома
            if message.media_group_id:
                buffer_key = (
                    f"{source_chat_id}:"
                    f"{message.media_group_id}"
                )

                if buffer_key not in media_group_buffers:
                    task = asyncio.create_task(
                        forward_album_delayed(
                            buffer_key,
                            client,
                            user_id,
                            full_config,
                        )
                    )

                    media_group_buffers[buffer_key] = {
                        "messages": [message],
                        "task": task,
                        "target_chats": target_chats.copy(),
                        "transform_config": transform_config,
                        "source_chat": source_chat_id,
                    }
                else:
                    media_group_buffers[buffer_key][
                        "messages"
                    ].append(message)

                return

            original_html, is_media = get_message_html(
                message,
            )

            new_text = transform_text(
                original_html,
                transform_config,
                is_media=is_media,
            )

            # ВАЖНО: этот цикл должен быть внутри message_handler
            for target_chat_id in target_chats:
                try:
                    if not is_transform_needed(
                        transform_config,
                    ):
                        await message.copy(
                            chat_id=target_chat_id,
                        )

                    elif not is_media:
                        await client.send_message(
                            chat_id=target_chat_id,
                            text=new_text,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False,
                        )

                    else:
                        await message.copy(
                            chat_id=target_chat_id,
                            caption=new_text,
                            parse_mode=ParseMode.HTML,
                        )

                    await send_user_log(
                        user_id,
                        "success",
                        (
                            "✅ <b>Сообщение переслано</b>\n"
                            f"Источник: <code>{source_chat_id}</code>\n"
                            f"Цель: <code>{target_chat_id}</code>\n"
                            f"ID: <code>{message.id}</code>"
                        ),
                        full_config,
                    )

                except Exception as error:
                    logging.exception(
                        "Ошибка отправки сообщения %s в %s",
                        message.id,
                        target_chat_id,
                    )

                    await send_user_log(
                        user_id,
                        "error",
                        (
                            "❌ <b>Ошибка отправки</b>\n"
                            f"Источник: <code>{source_chat_id}</code>\n"
                            f"Цель: <code>{target_chat_id}</code>\n"
                            f"Ошибка: <code>{error}</code>"
                        ),
                        full_config,
                    )

        except Exception as error:
            logging.exception(
                "Критическая ошибка обработки сообщения",
            )

            await send_user_log(
                user_id,
                "error",
                (
                    "❌ <b>Критическая ошибка обработки</b>\n"
                    f"Ошибка: <code>{error}</code>"
                ),
                full_config,
            )

    app.add_handler(
        MessageHandler(message_handler),
    )

    try:
        await app.start()

        logging.info(
            "Pyrogram-клиент запущен: %s",
            session_name,
        )

        async for _ in app.get_dialogs():
            pass

        logging.info(
            "Диалоги загружены: %s",
            session_name,
        )

        await asyncio.Event().wait()

    except asyncio.CancelledError:
        logging.info(
            "Pyrogram-клиент остановлен: %s",
            session_name,
        )
        raise

    except Exception:
        logging.exception(
            "Ошибка Pyrogram-клиента: %s",
            session_name,
        )
        raise

    finally:
        # Закрываем только текущий клиент
        current_client = client_instances.get(task_key)

        if current_client is app:
            await stop_forwarder(
                user_id,
                session_name,
            )


async def run_forwarder_forever(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
):
    """
    Автоматически перезапускает клиент после сетевых ошибок
    или неожиданного завершения.
    """
    retry_delay = 5

    while True:
        try:
            enabled = await Database.get_session_posting_status(
                user_id,
                session_name,
            )

            if not enabled:
                logging.info(
                    "Пересылка отключена: %s",
                    session_name,
                )
                return

            await start_forwarder_for_session(
                user_id,
                session_name,
                api_id,
                api_hash,
            )

            logging.warning(
                "Forwarder завершился. Повтор через %s секунд",
                retry_delay,
            )
            await asyncio.sleep(retry_delay)

        except asyncio.CancelledError:
            raise

        except Exception:
            logging.exception(
                "Ошибка forwarder. Повтор через %s секунд",
                retry_delay,
            )
            await asyncio.sleep(retry_delay)


async def restart_session_gracefully(
    user_id: int,
    session_name: str,
    api_id: int,
    api_hash: str,
) -> bool:
    task_key = (user_id, session_name)

    old_task = active_forwarder_tasks.pop(
        task_key,
        None,
    )

    if old_task:
        old_task.cancel()

        try:
            await old_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception(
                "Ошибка остановки старой задачи"
            )

    await asyncio.sleep(2)

    enabled = await Database.get_session_posting_status(
        user_id,
        session_name,
    )

    if not enabled:
        running_configs.pop(task_key, None)
        return False

    fresh_config = await Database.get_session_configs(
        user_id,
        session_name,
    )

    set_running_config(
        user_id,
        session_name,
        fresh_config,
    )

    task = asyncio.create_task(
        run_forwarder_forever(
            user_id,
            session_name,
            api_id,
            api_hash,
        ),
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
    delay: float = 3,
):
    """
    Отложенный перезапуск.
    Используйте только если действительно нужен
    автоматический restart.
    """
    await asyncio.sleep(delay)

    await restart_session_gracefully(
        user_id,
        session_name,
        api_id,
        api_hash,
    )


async def restore_active_forwarders():
    try:
        async with aiosqlite.connect(
            Database.DB_NAME
        ) as db:
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
                run_forwarder_forever(
                    user_id,
                    session_name,
                    API_ID,
                    API_HASH,
                ),
                name=f"forwarder:{user_id}:{session_name}",
            )

            active_forwarder_tasks[task_key] = task

            logging.info(
                "Автопостинг восстановлен: user=%s, session=%s",
                user_id,
                session_name,
            )

    except Exception:
        logging.exception(
            "Ошибка восстановления автопостинга"
        )