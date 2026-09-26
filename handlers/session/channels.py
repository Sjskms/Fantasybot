# handlers/session/channels.py
import math
import copy
import logging
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
from services.forwarder.supervisor import update_live_config, restart_session_gracefully
from services.forwarder.state import client_instances
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType

from keyboards.session_kb import (
    _build_channel_settings_keyboard,
    _build_filter_limits_keyboard,
)
from .state import CONTENT_TYPES, FILTER_UNITS, FilterLimitsStates
from .common import get_default_filters

router = Router()
logger = logging.getLogger(__name__)

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


def format_limit_str(min_val, max_val):
    min_str = "0" if min_val is None else str(min_val)
    max_str = "∞" if max_val in (None, 999999, 2000000000) else str(max_val)
    return f"{min_str}-{max_str}"


def build_channels_keyboard(
    channels: list[dict],
    selected_ids: set[int],
    initial_selected_ids: set[int],
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

        channel_row = [
            InlineKeyboardButton(
                text=f"{icon} {title}",
                callback_data=make_toggle_callback(mode, chat_id, current_page),
            )
        ]

        if selected and mode == "export":
            channel_row.append(
                InlineKeyboardButton(
                    text="⚙️ Настроить",
                    callback_data=make_config_callback(mode, chat_id),
                )
            )

        rows.append(channel_row)

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

    # Кнопка появляется только если есть несохраненные изменения
    if set(selected_ids) != set(initial_selected_ids):
        rows.append([
            InlineKeyboardButton(
                text="💾 Сохранить изменения",
                callback_data=f"ch:save:{mode}:{current_page}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад к категориям",
            callback_data="ch:back",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def fetch_available_channels(user_id: int, session_name: str, mode: str) -> list[dict]:
    task_key = (user_id, session_name)
    client = client_instances.get(task_key)
    temporary_client = False

    if client is None or not client.is_connected:
        is_posting_enabled = await Database.get_session_posting_status(user_id, session_name)
        if is_posting_enabled:
            for (u_id, s_name), active_app in client_instances.items():
                if u_id == user_id and s_name == session_name and active_app.is_connected:
                    client = active_app
                    break
            if client is None or not client.is_connected:
                logger.warning("Сессия %s активна, но клиент не найден в памяти. Пропуск.", session_name)
                return []
        else:
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
                temporary_client = True
            except Exception as e:
                logger.error("Не удалось запустить временный клиент для %s: %s", session_name, e)
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
        logger.error("Ошибка при получении диалогов сессии %s: %s", session_name, e)
    finally:
        if temporary_client and client and client.is_connected:
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
    
    selected_ids = state_data.get("selected_channels_ids")
    if selected_ids is None:
        selected_ids = get_selected_channel_ids(configs, mode)
        initial_selected_ids = set(selected_ids)
        await state.update_data(
            selected_channels_ids=set(selected_ids),
            initial_selected_ids=set(initial_selected_ids)
        )
    else:
        selected_ids = set(selected_ids)
        initial_selected_ids = set(state_data.get("initial_selected_ids", set()))

    keyboard = build_channels_keyboard(cached_channels, selected_ids, initial_selected_ids, mode, page)
    mode_title = "постинга" if mode == "post" else "экспорта"

    text = (
        f"📋 <b>Каналы для {mode_title}</b>\n\n"
        f"Сессия: <code>{escape(session_name)}</code>\n"
        f"Всего каналов: <b>{len(cached_channels)}</b>\n\n"
        "Отметьте каналы галочками. После изменений появится кнопка <b>«💾 Сохранить изменения»</b>:"
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

    _, mode, session_name = parts
    # Сбрасываем старый кэш выбора, но сохраняем имя сессии и режим
    await state.update_data(
        current_session=session_name,
        current_mode=mode,
        selected_channels_ids=None,
        initial_selected_ids=None,
        cached_channels=None,
    )

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

    data = await state.get_data()
    session_name = data.get("current_session")
    selected_ids = set(data.get("selected_channels_ids", set()))

    if chat_id in selected_ids:
        selected_ids.remove(chat_id)
        msg_text = "Канал снят ❌"
    else:
        selected_ids.add(chat_id)
        msg_text = "Канал выбран ✅"

    # Сохраняем состояние только в FSM! В базу запишем по кнопке «Сохранить изменения»
    await state.update_data(selected_channels_ids=selected_ids)

    await render_channel_page(callback, state, mode, session_name, page)
    await callback.answer(msg_text)


@router.callback_query(F.data.startswith("ch:save:"))
async def save_channels_handler(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    mode = parts[2]
    page = int(parts[3]) if len(parts) > 3 else 1

    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = callback.from_user.id
    selected_ids = set(data.get("selected_channels_ids", set()))
    cached_channels = data.get("cached_channels", [])

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    channels = session_configs.setdefault("channels", {})

    # Создаем быстрый поиск названия по cached_channels
    titles_map = {ch["id"]: ch.get("title", f"Канал {ch['id']}") for ch in cached_channels}

    # 1. Обновляем существующие в конфиге каналы
    for str_id, ch_data in list(channels.items()):
        try:
            cid = int(str_id)
        except ValueError:
            continue
        modes = ch_data.setdefault("modes", {})
        mode_cfg = modes.setdefault(mode, {"enabled": False, "filters": get_default_filters()})
        mode_cfg["enabled"] = (cid in selected_ids)

    # 2. Добавляем новые выбранные каналы, которых еще не было в config
    for cid in selected_ids:
        str_id = str(cid)
        if str_id not in channels:
            channels[str_id] = {
                "title": titles_map.get(cid, f"Канал {cid}"),
                "modes": {
                    mode: {
                        "enabled": True,
                        "filters": get_default_filters(),
                    }
                }
            }

    # 3. Сохраняем в базу данных и синхронизируем память
    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)
    await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    # 4. Фиксируем изменения: теперь исходное совпадает с текущим -> кнопка пропадет!
    await state.update_data(initial_selected_ids=set(selected_ids))

    await callback.answer("✅ Изменения успешно сохранены!", show_alert=True)
    await render_channel_page(callback, state, mode, session_name, page)


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
        channel_modes[mode] = {"enabled": True, "filters": get_default_filters()}

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


@router.callback_query(F.data == "ch:back")
async def back_to_categories(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Каналы для экспорта", callback_data=f"list_export_{session_name}")],
        [InlineKeyboardButton(text="📤 Каналы для постинга", callback_data=f"list_post_{session_name}")],
        [InlineKeyboardButton(text="⚙️ Общие фильтры для всех", callback_data=f"g_filt_{session_name}")],
        [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"select_session_{session_name}")],
    ])
    await callback.message.edit_text("⚙️ Выберите категорию каналов:", reply_markup=kb, parse_mode="HTML")
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
        is_global_limit=False,
    )
    await state.set_state(FilterLimitsStates.waiting_for_limit_value)

    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    limit_title = "минимальное" if limit_type == "min" else "максимальное"

    await callback.message.answer(
        f"✍️ Введите новое <b>{limit_title}</b> значение для <b>{media_title}</b> (отправьте число в чат):",
        parse_mode="HTML",
    )
    await callback.answer()


# --- ГЛОБАЛЬНАЯ НАСТРОЙКА ФИЛЬТРОВ И ЛИМИТОВ ДЛЯ ВСЕХ КАНАЛОВ ЭКСПОРТА ---

def _build_global_filters_keyboard(session_name: str, filters: dict) -> InlineKeyboardMarkup:
    rows = []
    for f_key, f_title in CONTENT_TYPES.items():
        f_item = filters.get(f_key, {"enabled": True, "min": 0, "max": 999999})
        if isinstance(f_item, dict):
            is_enabled = f_item.get("enabled", True)
            min_v = f_item.get("min", 0)
            max_v = f_item.get("max", 999999)
        else:
            is_enabled = bool(f_item)
            min_v = 0
            max_v = 999999

        icon = "✅" if is_enabled else "❌"
        lim_str = format_limit_str(min_v, max_v)

        rows.append([
            InlineKeyboardButton(
                text=f"{f_title}: {icon}",
                callback_data=f"g_toggle_f_{f_key}"
            ),
            InlineKeyboardButton(
                text=f"⚙️ Лимиты ({lim_str})",
                callback_data=f"g_limits_{f_key}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="💾 Применить ко всем каналам экспорта",
            callback_data=f"g_apply:{session_name}"
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад к категориям",
            callback_data="ch:back"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("g_filt_"))
async def open_global_filters_menu(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("g_filt_")
    user_id = callback.from_user.id

    await state.update_data(current_session=session_name, current_mode="export")

    state_data = await state.get_data()
    global_filters = state_data.get("temp_global_filters")

    if not global_filters:
        session_configs = await Database.get_session_configs(user_id, session_name) or {}
        channels = session_configs.get("channels", {})
        global_filters = None
        for ch_data in channels.values():
            exp_mode = ch_data.get("modes", {}).get("export", {})
            if exp_mode.get("filters"):
                global_filters = copy.deepcopy(exp_mode.get("filters"))
                break
        if not global_filters:
            global_filters = get_default_filters()
        await state.update_data(temp_global_filters=global_filters)

    keyboard = _build_global_filters_keyboard(session_name, global_filters)

    text = (
        f"🌐 <b>Глобальные фильтры и лимиты для всех каналов экспорта</b>\n"
        f"Сессия: <code>{escape(session_name)}</code>\n\n"
        "1. Включите или отключите нужные типы контента (✅ / ❌).\n"
        "2. Нажмите <b>«⚙️ Лимиты»</b>, чтобы задать мин/макс секунды, символы или байты.\n"
        "3. Нажмите <b>«💾 Применить ко всем каналам»</b> для сохранения:"
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        logger.error("Ошибка отрисовки меню глобальных фильтров: %s", e)
        await callback.answer(f"⚠️ Ошибка меню: {e}", show_alert=True)
    else:
        await callback.answer()


@router.callback_query(F.data.startswith("g_toggle_f_"))
async def toggle_global_filter(callback: CallbackQuery, state: FSMContext):
    filter_key = callback.data.removeprefix("g_toggle_f_")
    state_data = await state.get_data()
    session_name = state_data.get("current_session")
    filters = state_data.get("temp_global_filters", {})

    f_item = filters.get(filter_key, {"enabled": True, "min": 0, "max": 999999})
    if isinstance(f_item, dict):
        f_item["enabled"] = not f_item.get("enabled", True)
    else:
        filters[filter_key] = {"enabled": not bool(f_item), "min": 0, "max": 999999}

    await state.update_data(temp_global_filters=filters)

    keyboard = _build_global_filters_keyboard(session_name, filters)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest:
        pass
    await callback.answer("Статус фильтра изменен")


@router.callback_query(F.data.startswith("g_limits_"))
async def open_global_limits_menu(callback: CallbackQuery, state: FSMContext):
    filter_key = callback.data.removeprefix("g_limits_")
    state_data = await state.get_data()
    filters = state_data.get("temp_global_filters", {})

    f_item = filters.get(filter_key, {"enabled": True, "min": 0, "max": 999999})
    if not isinstance(f_item, dict):
        f_item = {"enabled": bool(f_item), "min": 0, "max": 999999}
        filters[filter_key] = f_item

    min_val = f_item.get("min", 0)
    max_val = f_item.get("max", 999999)
    is_enabled = f_item.get("enabled", True)

    unit = FILTER_UNITS.get(filter_key, "единицах")
    media_title = CONTENT_TYPES.get(filter_key, filter_key)

    text = (
        f"⚙️ <b>Глобальные лимиты: {media_title}</b>\n\n"
        f"Статус: <b>{'✅ Включен' if is_enabled else '❌ Выключен'}</b>\n"
        f"Измеряется в: <b>{unit}</b>\n\n"
        f"🔹 Минимальное значение: <code>{min_val}</code>\n"
        f"🔸 Максимальное значение: <code>{max_val}</code>\n\n"
        "Выберите, что хотите изменить:"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔹 Мин. значение", callback_data=f"g_set_lim_{filter_key}_min"),
                InlineKeyboardButton(text="🔸 Макс. значение", callback_data=f"g_set_lim_{filter_key}_max"),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад к общим фильтрам", callback_data="g_filt_back")
            ]
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "g_filt_back")
async def back_to_global_filters(callback: CallbackQuery, state: FSMContext):
    state_data = await state.get_data()
    session_name = state_data.get("current_session")
    filters = state_data.get("temp_global_filters", {})

    keyboard = _build_global_filters_keyboard(session_name, filters)
    text = (
        f"🌐 <b>Глобальные фильтры и лимиты для всех каналов экспорта</b>\n"
        f"Сессия: <code>{escape(session_name)}</code>\n\n"
        "1. Включите или отключите нужные типы контента (✅ / ❌).\n"
        "2. Нажмите <b>«⚙️ Лимиты»</b>, чтобы задать мин/макс секунды, символы или байты.\n"
        "3. Нажмите <b>«💾 Применить ко всем каналам»</b> для сохранения:"
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("g_set_lim_"))
async def prompt_global_limit_input(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    filter_key = parts[3]
    limit_type = parts[4]

    await state.update_data(
        edit_filter_key=filter_key,
        edit_limit_type=limit_type,
        is_global_limit=True,
    )
    await state.set_state(FilterLimitsStates.waiting_for_limit_value)

    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    unit = FILTER_UNITS.get(filter_key, "единицах")
    limit_title = "МИНИМАЛЬНОЕ" if limit_type == "min" else "МАКСИМАЛЬНОЕ"

    await callback.message.answer(
        f"✍️ Введите новое <b>ГЛОБАЛЬНОЕ {limit_title}</b> значение для <b>{media_title}</b> в {unit}:\n\n"
        f"<i>Пример: 50 (отправьте просто число в чат)</i>",
        parse_mode="HTML",
    )
    await callback.answer()


# --- ЕДИНЫЙ ОБРАБОТЧИК ВВОДА ЧИСЕЛ (И ГЛОБАЛЬНЫХ, И ДЛЯ ОДНОГО КАНАЛА) ---

@router.message(FilterLimitsStates.waiting_for_limit_value)
async def process_limit_input(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        return await message.answer("⚠️ Ошибка! Пожалуйста, отправьте целое положительное число (например: 0, 30, 100).")

    new_value = int(message.text)
    state_data = await state.get_data()

    is_global = state_data.get("is_global_limit", False)
    filter_key = state_data.get("edit_filter_key")
    limit_type = state_data.get("edit_limit_type")
    session_name = state_data.get("current_session")
    user_id = message.from_user.id

    if not filter_key or not limit_type:
        await state.set_state(None)
        return await message.answer("⚠️ Сессия настройки устарела. Откройте меню фильтров заново.")

    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    limit_title = "минимальное" if limit_type == "min" else "максимальное"

    # 1. Если меняем глобальные фильтры
    if is_global:
        filters = state_data.get("temp_global_filters", {})
        f_item = filters.setdefault(filter_key, {"enabled": True, "min": 0, "max": 999999})
        if not isinstance(f_item, dict):
            f_item = {"enabled": bool(f_item), "min": 0, "max": 999999}
            filters[filter_key] = f_item

        f_item[limit_type] = new_value
        await state.update_data(temp_global_filters=filters, is_global_limit=False)
        await state.set_state(None)

        keyboard = _build_global_filters_keyboard(session_name, filters)
        return await message.answer(
            f"✅ Глобальное {limit_title} значение для <b>{media_title}</b> установлено: <code>{new_value}</code>\n\n"
            "Нажмите <b>«💾 Применить ко всем каналам экспорта»</b>, чтобы сохранить настройки в каналы.",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    # 2. Если меняем фильтр для конкретного одного канала
    chat_id = state_data.get("edit_chat_id")
    mode = state_data.get("current_mode") or "export"

    if not chat_id:
        await state.set_state(None)
        return await message.answer("⚠️ Не удалось определить канал. Попробуйте выбрать канал заново.")

    str_chat_id = str(chat_id)
    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    channels = session_configs.setdefault("channels", {})

    channel_info = channels.setdefault(str_chat_id, {"title": f"Канал {chat_id}", "modes": {}})
    modes = channel_info.setdefault("modes", {})
    channel_mode_config = modes.setdefault(mode, {"enabled": True, "filters": get_default_filters()})

    filters = channel_mode_config.setdefault("filters", {})
    if not isinstance(filters.get(filter_key), dict):
        filters[filter_key] = {"enabled": True, "min": 0, "max": 999999}

    filters[filter_key][limit_type] = new_value

    try:
        await Database.update_session_configs(user_id, session_name, session_configs)
        await update_live_config(user_id, session_name)

        keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)
        await message.answer(
            f"✅ Для <b>{media_title}</b> установлено {limit_title} значение: <code>{new_value}</code>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения в базу: {e}")
    finally:
        await state.set_state(None)


@router.callback_query(F.data.startswith("g_apply:"))
async def apply_global_filters_to_all(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("g_apply:")
    user_id = callback.from_user.id

    state_data = await state.get_data()
    filters_to_apply = state_data.get("temp_global_filters")

    if not filters_to_apply:
        return await callback.answer("⚠️ Ошибка: фильтры не найдены в памяти.", show_alert=True)

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    channels = session_configs.setdefault("channels", {})

    updated_count = 0
    for ch_id_str, ch_data in channels.items():
        if not isinstance(ch_data, dict):
            continue
        modes = ch_data.setdefault("modes", {})
        export_mode = modes.get("export")

        if isinstance(export_mode, dict) and export_mode.get("enabled", False):
            export_mode["filters"] = copy.deepcopy(filters_to_apply)
            updated_count += 1

    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)
    await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    await callback.answer(f"✅ Применено к {updated_count} каналам!", show_alert=True)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ К выбору каналов", callback_data=f"list_export_{session_name}")],
            [InlineKeyboardButton(text="◀️ В меню сессии", callback_data=f"select_session_{session_name}")]
        ]
    )
    await callback.message.edit_text(
        f"✅ <b>Глобальные фильтры успешно применены!</b>\n"
        f"Настройки записаны для <b>{updated_count}</b> каналов экспорта сессии <code>{escape(session_name)}</code>.",
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "ignore_btn")
async def ignore_button_handler(callback: CallbackQuery):
    await callback.answer()
