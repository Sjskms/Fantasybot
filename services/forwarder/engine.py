# services/forwarder/engine.py
import asyncio
import logging
from html import escape

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
    InputMediaDocument,
)

from services.logging_service import log_event, get_logging_bot_instance
from services.forwarder.state import (
    media_group_buffers,
    loaded_configs,
    last_known_msg_ids,
    bot_instance,
    claim_message,
    finish_message,
)
from services.forwarder.filters import is_message_allowed
from services.forwarder.transformer import (
    is_transform_needed,
    get_message_html,
    transform_text,
)

logger = logging.getLogger(__name__)


async def send_user_log(
    user_id: int,
    log_type: str,
    message_text: str,
    session_configs: dict,
):
    """Отправляет персональный лог пользователю в Telegram."""
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

    target_chat_id = (
        logging_config.get("log_chat_id")
        or logging_config.get("chat_id")
        or user_id
    )

    try:
        await bot.send_message(
            chat_id=target_chat_id,
            text=message_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("Не удалось отправить лог пользователю %s в чат %s: %s", user_id, target_chat_id, e)


def get_export_channels(channels_config: dict) -> dict[int, dict]:
    result = {}
    if not isinstance(channels_config, dict):
        return result
    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(str(raw_id).strip())
        except Exception:
            continue
        export_mode = channel_data.get("modes", {}).get("export", {})
        if isinstance(export_mode, dict) and export_mode.get("enabled", False):
            result[chat_id] = export_mode
    return result


def get_post_channels(channels_config: dict) -> list[int]:
    result = []
    if not isinstance(channels_config, dict):
        return result
    for raw_id, channel_data in channels_config.items():
        try:
            chat_id = int(str(raw_id).strip())
        except Exception:
            continue
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
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    str_id = str(chat_id)
    if str_id.startswith("-100"):
        return f"https://t.me/c/{str_id[4:]}/{message_id}"
    return f"https://t.me/c/{str_id.lstrip('-')}/{message_id}"


async def forward_album_delayed(buffer_key: str, client: Client, user_id: int, session_configs: dict):
    await asyncio.sleep(1.5)
    buffer = media_group_buffers.pop(buffer_key, None)
    if not buffer:
        return

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
                if copied:
                    sent_msg_id = copied[0].id
            else:
                original_caption = ""
                for msg in messages:
                    caption = getattr(msg.caption, "html", None) or msg.caption or ""
                    if caption:
                        original_caption = caption
                        break
                
                final_caption = transform_text(original_caption, transform_config, is_media=True)
                media = []
                for index, msg in enumerate(messages):
                    cap = final_caption if index == 0 else ""
                    pm = ParseMode.HTML if index == 0 and cap else None
                    if msg.photo:
                        media.append(InputMediaPhoto(msg.photo.file_id, caption=cap, parse_mode=pm))
                    elif msg.video:
                        media.append(InputMediaVideo(msg.video.file_id, caption=cap, parse_mode=pm))
                
                if media:
                    sent = await client.send_media_group(target_chat_id, media)
                    if sent:
                        sent_msg_id = sent[0].id

            dest_link = make_message_link(target_chat_id, sent_msg_id) if sent_msg_id else ""
            log_msg = (
                f"✅ <b>Альбом переслан</b> ({len(messages)} медиа)\n"
                f"📤 Из: <b>{source_title}</b> (<a href='{src_link}'>#{messages[0].id}</a>)\n"
                f"📥 В: <b>{target_title}</b> (<a href='{dest_link}'>#{sent_msg_id}</a>)"
            )
            await send_user_log(user_id, "success", log_msg, session_configs)
            await log_event("forward_success", log_msg, user_id=user_id)

        except Exception as error:
            err_msg = (
                f"❌ <b>Ошибка отправки альбома</b>\n"
                f"📤 Из: <b>{source_title}</b> [<code>{source_chat}</code>]\n"
                f"📥 В: <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
                f"⚠️ Ошибка: <code>{escape(str(error))}</code>"
            )
            await send_user_log(user_id, "error", err_msg, session_configs)
            await log_event("forward_error", err_msg, user_id=user_id)


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
        await send_user_log(user_id, "success", log_msg, full_config)
        await log_event("forward_success", log_msg, user_id=user_id)

    except Exception as error:
        err_msg = (
            f"❌ <b>Ошибка отправки</b>\n"
            f"📤 Из: <b>{source_title}</b> [<code>{source_chat_id}</code>] (<a href='{src_link}'>#{message.id}</a>)\n"
            f"📥 В: <b>{target_title}</b> [<code>{target_chat_id}</code>]\n"
            f"⚠️ Ошибка: <code>{escape(str(error))}</code>"
        )
        await send_user_log(user_id, "error", err_msg, full_config)
        await log_event("forward_error", err_msg, user_id=user_id)


async def handle_incoming_message(client: Client, message: Message, user_id: int, session_name: str):
    if not message.chat:
        return
    source_chat_id = int(message.chat.id)

    if not await claim_message(source_chat_id, message.id):
        return

    full_config = copy_dict = dict(loaded_configs.get((user_id, session_name), {}))
    try:
        channels_config = full_config.get("channels", {})
        export_channels = get_export_channels(channels_config)
        target_chats = get_post_channels(channels_config)
        export_mode = export_channels.get(source_chat_id)

        if export_mode is None or not target_chats:
            return

        source_title = get_channel_title(source_chat_id, channels_config, getattr(message.chat, "title", None))
        src_link = make_message_link(source_chat_id, message.id, getattr(message.chat, "username", None))

        if not is_message_allowed(message, export_mode.get("filters", {})):
            log_msg = f"ℹ️ <b>Отфильтровано</b>\nИсточник: <b>{source_title}</b>\nПост: <a href='{src_link}'>#{message.id}</a>"
            await send_user_log(user_id, "filtered", log_msg, full_config)
            await log_event("forward_filtered", log_msg, user_id=user_id)
            return

        transform_config = export_mode.get("text_transform", full_config.get("text_transform", {}))

        if message.media_group_id:
            buffer_key = f"{source_chat_id}:{message.media_group_id}"
            if buffer_key not in media_group_buffers:
                task = asyncio.create_task(forward_album_delayed(buffer_key, client, user_id, full_config))
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

        for target_chat_id in target_chats:
            await forward_single_message(
                client, message, target_chat_id, transform_config,
                user_id, source_chat_id, source_title, src_link, full_config
            )

    except Exception as error:
        err_msg = f"❌ Критическая ошибка в handle_incoming_message: {error}"
        await send_user_log(user_id, "error", err_msg, full_config)
        await log_event("bot_error", err_msg, user_id=user_id)
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
                        if msg.id <= last_id:
                            break
                        last_known_msg_ids[ch_id] = max(last_id, msg.id)
                        asyncio.create_task(handle_incoming_message(client, msg, user_id, session_name))
                except Exception:
                    pass
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(10)
