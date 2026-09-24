# handlers/session/channels.py
import math
import copy
from html import escape
from urllib.parse import quote, unquote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import API_HASH, API_ID
from database import Database
from services.forwarder.supervisor import update_live_config
from services.session_manager import SessionManager
from pyrogram.enums import ChatMemberStatus, ChatType

from keyboards.session_kb import (
    _build_channel_settings_keyboard,
    _build_filter_limits_keyboard,
)
from .state import CONTENT_TYPES, FILTER_UNITS, FilterLimitsStates

router = Router()

CHANNELS_PER_PAGE = 8


def encode_value(value: str) -> str:
    return quote(str(value), safe="")


def decode_value(value: str) -> str:
    return unquote(value)


def make_page_callback(mode: str, page: int) -> str:
    return f"ch:page:{mode}:{page}"

def make_toggle_callback(mode: str, chat_id: int, page: int) -> str:
    return f"ch:tog:{mode}:{chat_id}:{page}"

def make_config_callback(mode: str, chat_id: int) -> str:
    return f"ch:cfg:{mode}:{chat_id}"


def build_channels_keyboard(
    channels: list[dict],
    selected_ids: set[int],
    mode: str,
    current_page: int,
) -> InlineKeyboardMarkup:
    total_channels = len(channels)
    total_pages = max(1, math.ceil(total_channels / CHANNELS_PER_PAGE))
    current_page = max(1, min(current_page, total_pages))

    start = (current_page - 1) * CHANNELS_PER_PAGE
    page_channels = channels[start:start + CHANNELS_PER_PAGE]

    rows = []

    for channel in page_channels:
        chat_id = int(channel["id"])
        title = channel.get("title", f"Канал {chat_id}")
        selected = chat_id in selected_ids
        icon = "✅" if selected else "▫️"

        # Создаем строку кнопок для канала
        channel_row = [
            InlineKeyboardButton(
                text=f"{icon} {title}",
                callback_data=make_toggle_callback(mode, chat_id, current_page),
            )
        ]

        # Кнопка настройки появляется на той же строке СТРОГО если канал выбран
        if selected:
            channel_row.append(
                InlineKeyboardButton(
                    text="⚙️ Настроить",
                    callback_data=make_config_callback(mode, chat_id),
                )
            )

        rows.append(channel_row)

    # Блок пагинации (если страниц больше одной)
    if total_pages > 1:
        navigation = []
        if current_page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=make_page_callback(mode, current_page - 1),
                )
            )
        navigation.append(
            InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore_btn")
        )
        if current_page < total_pages:
            navigation.append(
                InlineKeyboardButton(
                    text="Вперёд ➡️",
                    callback_data=make_page_callback(mode, current_page + 1),
                )
            )
        rows.append(navigation)

    # Служебные кнопки внизу
    rows.append([
        InlineKeyboardButton(
            text="💾 Сохранить изменения",
            callback_data=f"ch:save:{mode}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад к категориям",
            callback_data="ch:back",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


from services.forwarder.state import client_instances


async def fetch_available_channels(user_id: int, session_name: str, mode: str) -> list[dict]:
    task_key = (user_id, session_name)
    
    # 1. Проверяем, запущен ли клиент прямо сейчас в фоновом форвардере
    active_client = client_instances.get(task_key)
    
    client = None
    should_stop_after = False

    if active_client and active_client.is_connected:
        client = active_client
    else:
        # Если юзербот не запущен, создаем временный клиент для чтения диалогов
        session_string = await Database.get_session_string(user_id, session_name)
        if not session_string:
            return []
            
        client = Client(
            name=f"temp_fetch_{user_id}_{session_name}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True
        )
        try:
            await client.start()
            should_stop_after = True
        except Exception as e:
            logger.error(f"Не удалось запустить временный клиент для сессии {session_name}: {e}")
            return []

    result = []
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.type not in {ChatType.CHANNEL, ChatType.SUPERGROUP}:
                continue

            if mode == "post":
                try:
                    member = await client.get_chat_member(chat.id, "me")
                    if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                        continue
                except Exception:
                    continue

            result.append({
                "id": int(chat.id),
                "title": chat.title or str(chat.id),
                "username": chat.username,
                "type": chat.type,
            })
    except Exception as e:
        logger.error(f"Ошибка при получении диалогов сессии {session_name}: {e}")
    finally:
        # Если клиент был создан во временном порядке — обязательно останавливаем его
        if should_stop_after and client and client.is_connected:
            try:
                await client.stop()
            except Exception:
                pass

    return result


def get_selected_channel_ids(session_configs: dict, mode: str) -> set[int]:
    selected = set()
    channels = session_configs.get("channels", {})
    for raw_id, channel_data in channels.items():
        if not isinstance(channel_data, dict):
            continue
        mode_config = channel_data.get("modes", {}).get(mode, {})
        if isinstance(mode_config, dict) and mode_config.get("enabled", False):
            try:
                selected.add(int(raw_id))
            except (TypeError, ValueError):
                pass
    return selected


async def render_channel_page(callback: CallbackQuery, state: FSMContext, mode: str, session_name: str, page: int):
    user_id = callback.from_user.id
    state_data = await state.get_data()
    cached_channels = state_data.get("cached_channels")

    if cached_channels is None:
        cached_channels = await fetch_available_channels(user_id, session_name, mode)
        await state.update_data(cached_channels=cached_channels)

    configs = await Database.get_session_configs(user_id, session_name) or {}
    selected_ids = get_selected_channel_ids(configs, mode)

    keyboard = build_channels_keyboard(cached_channels, selected_ids, mode, page)
    mode_title = "постинга" if mode == "post" else "экспорта"

    text = (
        f"📋 <b>Каналы для {mode_title}</b>\n\n"
        f"Сессия: <code>{escape(session_name)}</code>\n"
        f"Всего каналов: <b>{len(cached_channels)}</b>\n\n"
        "Выберите каналы или нажмите <b>⚙️ Настроить фильтры</b> под нужным каналом:"
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


@router.callback_query(F.data.startswith("list_"))
async def open_channel_list(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 2)
    if len(parts) != 3:
        return await callback.answer("Некорректные данные", show_alert=True)

    _, mode, encoded_session = parts
    session_name = decode_value(encoded_session)

    await state.clear()
    await state.update_data(current_session=session_name, current_mode=mode)
    
    await callback.message.edit_text("⏳ Загрузка каналов из Telegram... Пожалуйста, подождите.")
    await render_channel_page(callback, state, mode, session_name, page=1)
    await callback.answer()





@router.callback_query(F.data.startswith("ch:page:"))
async def channel_page_handler(callback: CallbackQuery, state: FSMContext):
    _, _, mode, page = callback.data.split(":", 3)
    data = await state.get_data()
    session_name = data.get("current_session")
    await render_channel_page(callback, state, mode, session_name, page=int(page))
    await callback.answer()


@router.callback_query(F.data.startswith("ch:tog:"))
async def toggle_channel_handler(callback: CallbackQuery, state: FSMContext):
    _, _, mode, raw_chat_id, page = callback.data.split(":", 4)
    chat_id = int(raw_chat_id)
    page = int(page)
    user_id = callback.from_user.id

    data = await state.get_data()
    session_name = data.get("current_session")

    configs = await Database.get_session_configs(user_id, session_name) or {}
    channels = configs.setdefault("channels", {})
    
    channel_config = channels.setdefault(str(chat_id), {"title": f"Канал {chat_id}", "modes": {}})
    modes = channel_config.setdefault("modes", {})
    m_cfg = modes.setdefault(mode, {"enabled": False, "filters": {}})
    m_cfg["enabled"] = not m_cfg.get("enabled", False)

    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)

    await render_channel_page(callback, state, mode, session_name, page)
    await callback.answer("Канал выбран ✅" if m_cfg["enabled"] else "Канал отключен ❌")


@router.callback_query(F.data.startswith("ch:cfg:"))
async def configure_channel(callback: CallbackQuery, state: FSMContext):
    _, _, mode, raw_chat_id = callback.data.split(":", 3)
    chat_id = int(raw_chat_id)
    str_chat_id = str(chat_id)
    user_id = callback.from_user.id

    data = await state.get_data()
    session_name = data.get("current_session")

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    channels = session_configs.setdefault("channels", {})
    
    channel_info = channels.setdefault(str_chat_id, {"title": f"Канал {chat_id}", "modes": {}})
    channel_modes = channel_info.setdefault("modes", {})
    
    if mode not in channel_modes:
        channel_modes[mode] = {"enabled": True, "filters": {}}

    channel_mode_config = channel_modes[mode]
    channel_title = channel_info.get("title", f"Канал {chat_id}")

    await state.update_data(edit_chat_id=chat_id)
    keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)

    try:
        await callback.message.edit_text(
            f"⚙️ Настройки канала: <b>{escape(channel_title)}</b> (ID: <code>{chat_id}</code>)\n"
            f"Режим: <b>{mode.capitalize()}</b>\n\n"
            "Выберите фильтры и типы контента:",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await callback.answer()


@router.callback_query(F.data.startswith("ch:save:"))
async def save_channels_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = callback.from_user.id
    if session_name:
        await update_live_config(user_id, session_name)
    await callback.answer("✅ Изменения успешно сохранены!", show_alert=True)


@router.callback_query(F.data == "ch:back")
async def back_to_categories(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Каналы для экспорта", callback_data=f"list_export_{encode_value(session_name)}")],
        [InlineKeyboardButton(text="📤 Каналы для постинга", callback_data=f"list_post_{encode_value(session_name)}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"select_session_{session_name}")],
    ])
    await callback.message.edit_text(f"⚙️ Выберите категорию каналов:", reply_markup=kb, parse_mode="HTML")
    await callback.answer()






@router.callback_query(F.data.startswith("toggle_filter_"))
async def toggle_channel_filter(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    chat_id = int(parts[2])
    filter_key = parts[3]

    state_data = await state.get_data()
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode") or "export"
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    str_chat_id = str(chat_id)

    try:
        channel_mode_config = session_configs["channels"][str_chat_id]["modes"][mode]
        current_filters = channel_mode_config.setdefault("filters", {})

        filter_item = current_filters.get(filter_key)
        if isinstance(filter_item, dict):
            filter_item["enabled"] = not filter_item.get("enabled", True)
            is_enabled = filter_item["enabled"]
        else:
            is_enabled = True
            current_filters[filter_key] = {"enabled": is_enabled, "min": 0, "max": 999999}

        await Database.update_session_configs(user_id, session_name, session_configs)
        await update_live_config(user_id, session_name)

        keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer(f"Фильтр {filter_key} обновлен")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("edit_limits_"))
async def edit_limits_menu(callback: CallbackQuery):
    parts = callback.data.split("_", 3)
    chat_id = int(parts[2])
    filter_key = parts[3]

    keyboard = _build_filter_limits_keyboard(chat_id, filter_key)
    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    unit = FILTER_UNITS.get(filter_key, "единицах")

    text = (
        f"⚙️ Настройки лимитов: <b>{media_title}</b>\n"
        f"Измеряется в: <b>{unit}</b>\n\n"
        "Выберите, какое значение хотите изменить:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("set_limit_"))
async def prompt_limit_input(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    chat_id = int(parts[2])
    filter_key = parts[3]
    limit_type = parts[4]

    await state.update_data(
        edit_chat_id=chat_id,
        edit_filter_key=filter_key,
        edit_limit_type=limit_type,
    )
    await state.set_state(FilterLimitsStates.waiting_for_limit_value)

    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    limit_title = "минимальное" if limit_type == "min" else "максимальное"

    await callback.message.answer(
        f"✍️ Введите новое <b>{limit_title}</b> значение для <b>{media_title}</b> (отправьте число в чат):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(FilterLimitsStates.waiting_for_limit_value)
async def process_limit_input(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        return await message.answer("⚠️ Введите целое положительное число.")

    new_value = int(message.text)
    state_data = await state.get_data()

    chat_id = state_data.get("edit_chat_id")
    filter_key = state_data.get("edit_filter_key")
    limit_type = state_data.get("edit_limit_type")
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode") or "export"
    user_id = message.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    str_chat_id = str(chat_id)

    try:
        channel_mode_config = session_configs["channels"][str_chat_id]["modes"][mode]
        filters = channel_mode_config.setdefault("filters", {})
        
        if not isinstance(filters.get(filter_key), dict):
            filters[filter_key] = {"enabled": True, "min": 0, "max": 999999}

        filters[filter_key][limit_type] = new_value
        await Database.update_session_configs(user_id, session_name, session_configs)
        await update_live_config(user_id, session_name)

        keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)
        await message.answer(
            f"✅ Успешно обновлено! Новое значение: <code>{new_value}</code>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await state.set_state(None)


@router.callback_query(F.data == "ignore_btn")
async def ignore_button_handler(callback: CallbackQuery):
    await callback.answer()