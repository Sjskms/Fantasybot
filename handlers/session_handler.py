# handlers/session_handler.py
import asyncio
import copy
import html
import json
import logging
from html import escape
from typing import Any, Dict, List, Set

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType

from config import API_ID, API_HASH
from database import Database
from keyboards import admin_kb, user_kb
from keyboards.session_kb import (
    _build_channel_settings_keyboard,
    _build_channels_keyboard,
    _build_filter_limits_keyboard,
    get_forwarding_menu_keyboard,
    get_logging_settings_keyboard,
    get_session_management_keyboard,
    get_session_settings_keyboard,
    get_text_transform_keyboard,
)
from services.Additional_Feature import (
    active_forwarder_tasks,
    has_session_unapplied_changes,
    restart_session_gracefully,
    run_forwarder_forever,
    stop_forwarder,
    update_live_config,
)
from services.session_manager import SessionManager

logger = logging.getLogger(__name__)
router = Router()
db = Database()


# --- FSM СОСТОЯНИЯ ---
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

class UserSessionLoggingStates(StatesGroup):
    waiting_for_session_log_chat_id = State()
    
    
class TextTransformStates(StatesGroup):
    waiting_for_custom_text = State()
    waiting_for_replace_word = State()
    waiting_for_replace_link = State()


class FilterLimitsStates(StatesGroup):
    waiting_for_limit_value = State()


# --- КОНСТАНТЫ И СЛОВАРИ ---

CONTENT_TYPES = {
    "photos": "Фото",
    "videos": "Видео",
    "text": "Текст",
    "documents": "Файлы",
    "music": "Музыка",
    "voices": "Голосовые",
    "video_notes": "Кружки",
}

FILTER_UNITS = {
    "text": "символах (длина текста)",
    "videos": "секундах (длительность)",
    "video_notes": "секундах (длительность кружочка)",
    "voices": "секундах (длительность)",
    "music": "секундах (длительность)",
    "photos": "байтах (размер файла)",
    "documents": "байтах (размер файла)",
}

mode_names = {
    "keep": "Только замена слов/ссылок",
    "append": "Добавление в конец",
    "prepend": "Добавление в начало",
    "replace": "Полная замена текста",
}


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_enabled_filter_names(filters: dict) -> list[str]:
    """Возвращает список включенных названий фильтров."""
    content_names = {
        "photos": "фото",
        "videos": "видео",
        "text": "текст",
        "documents": "файлы",
        "music": "музыка",
        "voices": "голосовые",
        "video_notes": "кружки",
    }
    result = []
    if isinstance(filters, dict):
        for key, title in content_names.items():
            value = filters.get(key, {})
            enabled = value.get("enabled", False) if isinstance(value, dict) else bool(value)
            if enabled:
                result.append(title)
    return result


def get_default_filters():
    return {
        "photos": {"enabled": True, "min": 0, "max": 2000000000},       # До 2 ГБ
        "videos": {"enabled": True, "min": 0, "max": 999999},           # До 11 дней длительности
        "text": {"enabled": True, "min": 0, "max": 999999},             # До 999 тыс. символов
        "documents": {"enabled": True, "min": 0, "max": 2000000000},     # Разрешаем файлы и несжатые фото
        "music": {"enabled": True, "min": 0, "max": 999999},
        "voices": {"enabled": True, "min": 0, "max": 999999},
        "video_notes": {"enabled": True, "min": 0, "max": 999999},
    }


def build_forwarding_status_text(session_name: str, session_configs: dict, enabled: bool) -> str:
    """Формирует текстовое сообщение со статусом пересылки и списками каналов."""
    if not isinstance(session_configs, dict):
        session_configs = {}

    channels = session_configs.get("channels", {})

    if not enabled:
        return (
            "⏹ <b>Пересылка выключена</b>\n\n"
            f"Сессия: <code>{escape(session_name)}</code>\n\n"
            "Для запуска нажмите кнопку ниже."
        )

    export_lines = []
    post_lines = []

    for raw_chat_id, channel_data in channels.items():
        if not isinstance(channel_data, dict):
            continue

        title_raw = channel_data.get("title") or channel_data.get("name") or f"Канал {raw_chat_id}"
        title = escape(str(title_raw))

        modes = channel_data.get("modes", {})

        export_mode = modes.get("export", {})
        if isinstance(export_mode, dict) and export_mode.get("enabled", False):
            filters = export_mode.get("filters", {})
            enabled_types = get_enabled_filter_names(filters)

            if enabled_types:
                media_text = ", ".join(enabled_types)
                export_lines.append(f"• <b>{title}</b> (<i>{media_text}</i>)")
            else:
                export_lines.append(f"• <b>{title}</b> (<i>все типы</i>)")

        post_mode = modes.get("post", {})
        if isinstance(post_mode, dict) and post_mode.get("enabled", False):
            post_lines.append(f"• <b>{title}</b>")

    text = (
        "✅ <b>Пересылка включена!</b>\n\n"
        f"Сессия: <code>{escape(session_name)}</code>\n\n"
        "📤 <b>Каналы для экспорта:</b>\n"
    )

    text += "\n".join(export_lines) if export_lines else "• <i>Не выбраны</i>"
    text += "\n\n📥 <b>Каналы для постинга:</b>\n"
    text += "\n".join(post_lines) if post_lines else "• <i>Не выбраны</i>"

    return text


# --- ХЭНДЛЕРЫ МЕНЮ СЕССИЙ И УПРАВЛЕНИЯ ПЕРЕСЫЛКОЙ ---

@router.callback_query(F.data == "session_settings")
async def process_session_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_sessions = await Database.get_user_sessions(callback.from_user.id)
    markup = get_session_settings_keyboard(user_sessions)
    await callback.message.edit_text("⚙️ Ваши Telegram сессии:", reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("select_session_"))
async def select_session_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    session_name = callback.data.removeprefix("select_session_")

    enable_posting = await Database.get_session_posting_status(user_id, session_name)
    session_configs = await Database.get_session_configs(user_id, session_name)

    text = build_forwarding_status_text(
        session_name=session_name,
        session_configs=session_configs,
        enabled=enable_posting,
    )

    keyboard = get_session_management_keyboard(
        session_name=session_name,
        enable_posting=enable_posting,
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise

    await callback.answer()


@router.callback_query(F.data.startswith("session_config_"))
async def session_config_handler(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("session_config_")
    user_id = callback.from_user.id
    await state.update_data(current_session=session_name)

    enable_posting_status = await Database.get_session_posting_status(user_id, session_name)
    keyboard = get_session_management_keyboard(session_name, enable_posting_status)

    try:
        await callback.message.edit_text(
            f"⚙️ Управление сессией: <code>{escape(session_name)}</code>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_posting_"))
async def toggle_posting_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    session_name = callback.data.removeprefix("toggle_posting_")
    task_key = (user_id, session_name)

    current_status = await Database.get_session_posting_status(user_id, session_name)
    new_status = not current_status

    if new_status:
        post_channels_count = await Database.get_session_channels_count(user_id, session_name, "post")
        export_channels_count = await Database.get_session_channels_count(user_id, session_name, "export")

        if post_channels_count == 0 or export_channels_count == 0:
            await callback.answer(
                "⚠️ Невозможно включить пересылку.\nНастройте хотя бы один канал для постинга и один канал для экспорта.",
                show_alert=True,
            )
            return

        await Database.update_session_posting_status(user_id, session_name, True)

        saved_status = await Database.get_session_posting_status(user_id, session_name)
        if not saved_status:
            await callback.answer("❌ Не удалось сохранить статус пересылки в базе данных.", show_alert=True)
            return

        old_task = active_forwarder_tasks.pop(task_key, None)
        if old_task:
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass

        await stop_forwarder(user_id, session_name)

        task = asyncio.create_task(
            run_forwarder_forever(
                user_id=user_id,
                session_name=session_name,
                api_id=API_ID,
                api_hash=API_HASH,
            ),
            name=f"forwarder:{user_id}:{session_name}",
        )
        active_forwarder_tasks[task_key] = task
        logging.info(f"Пересылка включена: user_id={user_id}, session={session_name}")

    else:
        await Database.update_session_posting_status(user_id, session_name, False)

        saved_status = await Database.get_session_posting_status(user_id, session_name)
        if saved_status:
            await callback.answer("❌ Не удалось выключить пересылку в базе данных.", show_alert=True)
            return

        task = active_forwarder_tasks.pop(task_key, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        await stop_forwarder(user_id, session_name)
        logging.info(f"Пересылка выключена: user_id={user_id}, session={session_name}")

    actual_status = await Database.get_session_posting_status(user_id, session_name)
    session_configs = await Database.get_session_configs(user_id, session_name)

    text = build_forwarding_status_text(
        session_name=session_name,
        session_configs=session_configs,
        enabled=actual_status,
    )

    keyboard = get_session_management_keyboard(
        session_name=session_name,
        enable_posting=actual_status,
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise

    await callback.answer("Пересылка включена ✅" if actual_status else "Пересылка выключена ⏹")


@router.callback_query(F.data.startswith("restart_posting_"))
async def restart_posting_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    session_name = callback.data.removeprefix("restart_posting_")

    await callback.answer("⏳ Перезапускаю пересылку...")

    await callback.message.edit_text(
        f"⏳ <b>Перезапуск пересылки</b>\n\nСессия: <code>{escape(session_name)}</code>\nПожалуйста, подождите...",
        parse_mode="HTML",
    )

    restarted = await restart_session_gracefully(
        user_id=user_id,
        session_name=session_name,
        api_id=API_ID,
        api_hash=API_HASH,
    )

    session_configs = await Database.get_session_configs(user_id, session_name)

    status_text = build_forwarding_status_text(
        session_name=session_name,
        session_configs=session_configs,
        enabled=restarted,
    )

    keyboard = get_forwarding_menu_keyboard(session_name, restarted)

    if restarted:
        status_text = f"✅ <b>Пересылка перезапущена!</b>\n\n{status_text}"
    else:
        status_text = f"⏹ <b>Пересылка выключена.</b>\n\nНовые настройки будут применены при следующем запуске."

    try:
        await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


@router.callback_query(F.data.startswith("apply_all_changes_"))
async def apply_all_changes_handler(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("apply_all_changes_")
    user_id = callback.from_user.id

    await callback.answer("⏳ Перезапускаю сессию...")

    await callback.message.edit_text(
        f"⏳ <b>Применение изменений</b>\n\nСессия: <code>{session_name}</code>\nПожалуйста, подождите...",
        parse_mode="HTML",
    )

    restarted = await restart_session_gracefully(
        user_id=user_id,
        session_name=session_name,
        api_id=API_ID,
        api_hash=API_HASH,
    )

    if restarted:
        text = f"✅ <b>Изменения применены</b>\n\nСессия <code>{session_name}</code> перезапущена и работает с новым конфигом."
    else:
        text = f"✅ <b>Изменения сохранены</b>\n\nПересылка сейчас выключена. Новые настройки применятся при следующем запуске."

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ К настройкам каналов", callback_data=f"session_config2_{session_name}")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


# --- ХЭНДЛЕРЫ ТЕКСТА И HTML-ТРАНСФОРМАЦИИ ---

@router.callback_query(F.data.startswith("text_transform_"))
async def show_text_transform_menu(callback: CallbackQuery, state: FSMContext = None, session_name: str = None):
    if state:
        await state.set_state(None)

    user_id = callback.from_user.id
    if not session_name:
        session_name = callback.data.removeprefix("text_transform_")

    try:
        session_configs = await Database.get_session_configs(user_id, session_name)
        if not session_configs or not isinstance(session_configs, dict):
            session_configs = {}

        tt_cfg = session_configs.get("text_transform", {})
        if not isinstance(tt_cfg, dict):
            tt_cfg = {}

        current_mode = tt_cfg.get("mode", "keep")
        words_count = len(tt_cfg.get("replace_words", []))
        links_count = len(tt_cfg.get("replace_links", []))

        raw_custom = tt_cfg.get("custom_text", "").strip()
        if raw_custom:
            safe_text = html.escape(raw_custom)
            if len(safe_text) > 200:
                safe_text = safe_text[:200] + "..."
            custom_preview = f"<code>{safe_text}</code>"
        else:
            custom_preview = "<i>(не задан)</i>"

        text = (
            f"✏️ <b>Настройки текста и ссылок:</b> <code>{session_name}</code>\n\n"
            f"🔹 <b>Режим вставки:</b> {mode_names.get(current_mode, 'Не выбран')}\n"
            f"🔹 <b>Правил замены слов:</b> {words_count}\n"
            f"🔹 <b>Правил замены ссылок:</b> {links_count}\n\n"
            f"📝 <b>Текущий кастомный текст:</b>\n{custom_preview}\n\n"
            "Выберите действие ниже:"
        )

        keyboard = get_text_transform_keyboard(session_name, tt_cfg)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logging.error(f"Ошибка Telegram при открытии меню текста: {e}")
    except Exception as e:
        logging.exception(f"Критическая ошибка в show_text_transform_menu: {e}")
    finally:
        await callback.answer()


@router.callback_query(F.data.startswith("tt_mode_"))
async def change_transform_mode(callback: CallbackQuery):
    raw = callback.data.removeprefix("tt_mode_")
    session_name, new_mode = raw.rsplit("_", 1)
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(configs, dict):
        configs = {}
    if "text_transform" not in configs:
        configs["text_transform"] = {}

    configs["text_transform"]["mode"] = new_mode

    await Database.update_session_configs(user_id, session_name, configs)
    await show_text_transform_menu(callback, session_name=session_name)


@router.callback_query(F.data.startswith("tt_clear_"))
async def clear_transform_rules(callback: CallbackQuery):
    session_name = callback.data.removeprefix("tt_clear_")
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(configs, dict):
        configs = {}

    configs["text_transform"] = {
        "mode": "keep",
        "custom_text": "",
        "replace_words": [],
        "replace_links": [],
    }

    await Database.update_session_configs(user_id, session_name, configs)
    await callback.answer("Все правила текста и ссылок очищены!", show_alert=True)
    await show_text_transform_menu(callback, session_name=session_name)


@router.callback_query(F.data.startswith("tt_settext_"))
async def prompt_custom_text(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_settext_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_custom_text)

    await callback.message.answer(
        "✍️ Отправьте текст, который будет добавляться или заменять описание.\n\n"
        "Поддерживается HTML-разметка:\n"
        "<code>&lt;b&gt;жирный&lt;/b&gt;</code>\n"
        "<code>&lt;i&gt;курсив&lt;/i&gt;</code>\n"
        "<code>&lt;a href='https://t.me/your_channel'&gt;Ваша ссылка&lt;/a&gt;</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TextTransformStates.waiting_for_custom_text)
async def process_custom_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = message.from_user.id

    custom_text_input = message.text or message.caption or ""

    configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(configs, dict):
        configs = {}

    if "text_transform" not in configs:
        configs["text_transform"] = {}

    configs["text_transform"]["custom_text"] = custom_text_input

    await Database.update_session_configs(user_id, session_name, configs)
    await state.set_state(None)

    tt_cfg = configs.get("text_transform", {})
    current_mode = tt_cfg.get("mode", "keep")
    words_count = len(tt_cfg.get("replace_words", []))
    links_count = len(tt_cfg.get("replace_links", []))

    if custom_text_input.strip():
        safe_preview = html.escape(custom_text_input.strip())
        if len(safe_preview) > 120:
            safe_preview = safe_preview[:120] + "..."
        custom_preview = f"<code>{safe_preview}</code>"
    else:
        custom_preview = "<i>(не задан)</i>"

    text = (
        f"✅ <b>Кастомный текст успешно сохранен!</b>\n\n"
        f"✏️ <b>Настройки текста и ссылок:</b> <code>{session_name}</code>\n\n"
        f"🔹 <b>Режим вставки:</b> {mode_names.get(current_mode, 'Не выбран')}\n"
        f"🔹 <b>Правил замены слов:</b> {words_count}\n"
        f"🔹 <b>Правил замены ссылок:</b> {links_count}\n\n"
        f"📝 <b>Текущий кастомный текст:</b>\n{custom_preview}\n\n"
        "Выберите действие ниже:"
    )

    keyboard = get_text_transform_keyboard(session_name, tt_cfg)

    try:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as e:
        logging.error(f"Ошибка парсинга HTML при подтверждении текста: {e}")
        fallback_text = f"✅ <b>Кастомный текст успешно сохранен!</b>\n\n✏️ Настройки текста и ссылок для сессии: <code>{session_name}</code>"
        await message.answer(fallback_text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("tt_addword_"))
async def prompt_replace_word(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_addword_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_replace_word)

    await callback.message.answer(
        "🔤 Введите старое и новое слово через знак <code>=</code>\n\n"
        "<i>Пример:</i>\n<code>скидка = распродажа</code>\n"
        "<i>Или чтобы удалить слово, оставьте правую часть пустой:</i>\n<code>реклама = </code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TextTransformStates.waiting_for_replace_word)
async def process_replace_word(message: Message, state: FSMContext):
    if "=" not in message.text:
        return await message.answer("⚠️ Формат неверный! Используйте знак '=' между словами (например: старое = новое).")

    old_w, new_w = message.text.split("=", 1)
    old_w, new_w = old_w.strip(), new_w.strip()

    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = message.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(configs, dict):
        configs = {}
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    if "replace_words" not in configs["text_transform"]:
        configs["text_transform"]["replace_words"] = []

    configs["text_transform"]["replace_words"].append({"from": old_w, "to": new_w})

    await Database.update_session_configs(user_id, session_name, configs)
    await state.set_state(None)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚙️ К настройкам текста", callback_data=f"text_transform_{session_name}")]]
    )
    await message.answer(
        f"✅ Правило замены слова добавлено: <code>{html.escape(old_w)}</code> ➡️ <code>{html.escape(new_w)}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("tt_addlink_"))
async def prompt_replace_link(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_addlink_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_replace_link)

    await callback.message.answer(
        "🔗 Введите старую ссылку и новую ссылку через знак <code>=</code>\n\n"
        "<i>Пример:</i>\n<code>https://t.me/old_channel = https://t.me/my_channel</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TextTransformStates.waiting_for_replace_link)
async def process_replace_link(message: Message, state: FSMContext):
    if "=" not in message.text:
        return await message.answer("⚠️ Формат неверный! Используйте знак '=' между ссылками.")

    old_l, new_l = message.text.split("=", 1)
    old_l, new_l = old_l.strip(), new_l.strip()

    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = message.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(configs, dict):
        configs = {}
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    if "replace_links" not in configs["text_transform"]:
        configs["text_transform"]["replace_links"] = []

    configs["text_transform"]["replace_links"].append({"from": old_l, "to": new_l})

    await Database.update_session_configs(user_id, session_name, configs)
    await state.set_state(None)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚙️ К настройкам текста", callback_data=f"text_transform_{session_name}")]]
    )
    await message.answer(
        f"✅ Правило замены ссылки добавлено:\n<code>{html.escape(old_l)}</code> ➡️ <code>{html.escape(new_l)}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


# --- ХЭНДЛЕРЫ ЛОГИРОВАНИЯ ---

@router.callback_query(F.data.startswith("session_logging_"))
async def show_logging_settings(callback: CallbackQuery):
    session_name = callback.data.removeprefix("session_logging_")
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(session_configs, dict):
        session_configs = {}

    log_config = session_configs.get(
        "logging",
        {
            "enabled": True,
            "log_success": True,
            "log_filtered": False,
            "log_errors": True,
        },
    )

    text = (
        f"📜 <b>Настройки логирования для сессии:</b> <code>{escape(session_name)}</code>\n\n"
        "Выберите, какие события бот будет отправлять вам в личные сообщения:"
    )

    keyboard = get_logging_settings_keyboard(session_name, log_config)

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_log_"))
async def toggle_log_option_handler(callback: CallbackQuery, state: FSMContext):
    raw_data = callback.data.removeprefix("toggle_log_")
    session_name, log_type = raw_data.rsplit("_", 1)
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(session_configs, dict):
        session_configs = {}

    if "logging" not in session_configs or not isinstance(session_configs["logging"], dict):
        session_configs["logging"] = {
            "enabled": True,
            "log_success": True,
            "log_filtered": False,
            "log_errors": True,
            "log_chat_id": None,
        }

    log_config = session_configs["logging"]

    # --- НОВЫЙ ФУНКЦИОНАЛ: Настройка получателя логов ---
    if log_type == "chatprompt":
        await state.update_data(target_session_name=session_name)
        await state.set_state(UserSessionLoggingStates.waiting_for_session_log_chat_id)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить (в ЛС бота)", callback_data=f"toggle_log_{session_name}_reset_chat")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"session_logging_{session_name}")]
        ])
        
        await callback.message.edit_text(
            f"🎯 <b>Настройка получателя логов для сессии:</b> <code>{escape(session_name)}</code>\n\n"
            "Вы можете пересылать логи работы юзербота в ваш закрытый чат, группу или канал.\n\n"
            "✍️ Отправьте <b>ID чата/канала</b> (например, <code>-100123456789</code>).\n"
            "<i>Убедитесь, что ваш бот добавлен в этот чат/канал с правом отправки сообщений!</i>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return

    elif log_type == "reset_chat":
        log_config["log_chat_id"] = None
        status_msg = "Логи перенаправлены в ЛС бота"
        
    # --- СТАНДАРТНЫЕ ТУМБЛЕРЫ ---
    elif log_type == "main":
        log_config["enabled"] = not log_config.get("enabled", True)
        status_msg = "Главный тумблер логов " + ("включен ✅" if log_config["enabled"] else "выключен ❌")
    elif log_type == "success":
        log_config["log_success"] = not log_config.get("log_success", True)
        status_msg = "Логи успехов " + ("включены ✅" if log_config["log_success"] else "выключены ❌")
    elif log_type == "filtered":
        log_config["log_filtered"] = not log_config.get("log_filtered", False)
        status_msg = "Логи фильтрации " + ("включены ✅" if log_config["log_filtered"] else "выключены ❌")
    elif log_type == "errors":
        log_config["log_errors"] = not log_config.get("log_errors", True)
        status_msg = "Логи ошибок " + ("включены ✅" if log_config["log_errors"] else "выключены ❌")
    else:
        status_msg = "Обновлено"

    session_configs["logging"] = log_config

    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)

    # Возврат к стандартному меню настроек логирования
    text = (
        f"📜 <b>Настройки логирования для сессии:</b> <code>{escape(session_name)}</code>\n\n"
        "Выберите, какие события бот будет отправлять вам в личные сообщения:"
    )
    keyboard = get_logging_settings_keyboard(session_name, log_config)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    await callback.answer(status_msg, show_alert=False)


# --- ХЭНДЛЕР ПРИЕМА ID ОТ ПОЛЬЗОВАТЕЛЯ ---
@router.message(UserSessionLoggingStates.waiting_for_session_log_chat_id)
async def process_user_session_log_chat_id(message: Message, state: FSMContext):
    raw_val = (message.text or "").strip()
    
    # Проверка, что введен числовой ID (может начинаться с минуса)
    if not raw_val.lstrip('-').isdigit():
        return await message.answer(
            "⚠️ Некорректный ID. Пожалуйста, отправьте числовой ID чата/канала "
            "(например, <code>-100123456789</code> или <code>123456789</code>)."
        )

    target_chat_id = int(raw_val)
    fsm_data = await state.get_data()
    session_name = fsm_data.get("target_session_name")
    user_id = message.from_user.id

    await state.clear()

    session_configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(session_configs, dict):
        session_configs = {}

    log_config = session_configs.setdefault("logging", {
        "enabled": True,
        "log_success": True,
        "log_filtered": False,
        "log_errors": True,
    })
    
    log_config["log_chat_id"] = target_chat_id
    session_configs["logging"] = log_config

    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)

    keyboard = get_logging_settings_keyboard(session_name, log_config)
    await message.answer(
        f"✅ <b>Получатель логов успешно изменен!</b>\n"
        f"Логи сессии <code>{escape(session_name)}</code> теперь отправляются на ID: <code>{target_chat_id}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    

# --- ХЭНДЛЕРЫ КАНАЛОВ И ФИЛЬТРОВ ---

@router.callback_query(F.data.startswith("session_config2_"))
async def config_menu(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("session_config2_")
    await state.update_data(current_session=session_name)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Каналы для экспорта", callback_data=f"list_export_{session_name}")],
            [InlineKeyboardButton(text="📤 Каналы для постинга", callback_data=f"list_post_{session_name}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"select_session_{session_name}")],
        ]
    )
    await callback.message.edit_text(f"Выберите категорию каналов: ({escape(session_name)})", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("list_"))
async def list_channels(callback: CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    mode = data_parts[1]  # export / post
    session_name = "_".join(data_parts[2:])
    user_id = callback.from_user.id

    await state.update_data(
        current_session=session_name,
        current_mode=mode,
    )

    await callback.message.edit_text("Загрузка каналов... Пожалуйста, подождите.")

    client = await SessionManager.get_or_create_client(user_id, session_name, API_ID, API_HASH)

    if not client:
        await callback.message.edit_text("Ошибка: сессия не запущена или недоступна.")
        return

    fetched_channels = []
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if chat.type not in [ChatType.CHANNEL, ChatType.SUPERGROUP]:
                continue

            can_add_channel = False
            if mode == "post":
                try:
                    member = await client.get_chat_member(chat.id, "me")
                    if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                        can_add_channel = True
                except Exception:
                    continue
            else:
                can_add_channel = True

            if can_add_channel:
                fetched_channels.append({
                    "id": chat.id,
                    "title": chat.title,
                    "username": chat.username,
                    "type": chat.type,
                })

    except Exception as e:
        await callback.message.edit_text(f"Критическая ошибка при получении каналов: {e}")
        return

    if not fetched_channels:
        if mode == "post":
            await callback.message.edit_text("Каналов, где сессия является администратором или владельцем, не найдено.")
        else:
            await callback.message.edit_text("Каналы в этой сессии не найдены.")
        return

    await state.update_data(cached_channels=fetched_channels)

    session_configs: Dict[str, Any] = await Database.get_session_configs(user_id, session_name)
    current_selected_channels_ids = set()

    if isinstance(session_configs, dict) and "channels" in session_configs:
        for str_chat_id, channel_data in session_configs["channels"].items():
            if "modes" in channel_data and mode in channel_data["modes"]:
                if channel_data["modes"][mode].get("enabled", False):
                    try:
                        current_selected_channels_ids.add(int(str_chat_id))
                    except ValueError:
                        pass

    await state.update_data(selected_channels_ids=current_selected_channels_ids)

    keyboard = await _build_channels_keyboard(
        cached_channels=fetched_channels,
        selected_channels_ids=current_selected_channels_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=session_configs,
    )

    await callback.message.edit_text(
        f"Выберите канал{'ы' if len(current_selected_channels_ids) > 0 else ''} для {'постинга' if mode == 'post' else 'экспорта'}:",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_select_"))
async def toggle_channel_selection(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[2])

    state_data = await state.get_data()
    selected_channels_ids: Set[int] = set(state_data.get("selected_channels_ids", set()))
    cached_channels: List[Dict] = state_data.get("cached_channels", [])
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode")
    user_id = callback.from_user.id

    session_configs: Dict[str, Any] = await Database.get_session_configs(user_id, session_name)
    if not isinstance(session_configs, dict):
        session_configs = {}

    if "channels" not in session_configs:
        session_configs["channels"] = {}

    str_chat_id = str(chat_id)
    if str_chat_id not in session_configs["channels"]:
        channel_title = "Неизвестный канал"
        for ch in cached_channels:
            if ch["id"] == chat_id:
                channel_title = ch["title"]
                break
        session_configs["channels"][str_chat_id] = {"title": channel_title, "modes": {}}

    channel_config = session_configs["channels"][str_chat_id]
    if "modes" not in channel_config:
        channel_config["modes"] = {}

    if mode not in channel_config["modes"]:
        channel_config["modes"][mode] = {
            "enabled": False,
            "filters": get_default_filters(),
        }

    if chat_id in selected_channels_ids:
        selected_channels_ids.remove(chat_id)
        channel_config["modes"][mode]["enabled"] = False
        message_text = "Канал отменен."
    else:
        selected_channels_ids.add(chat_id)
        channel_config["modes"][mode]["enabled"] = True
        message_text = "Канал выбран."

    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)
    await state.update_data(selected_channels_ids=selected_channels_ids)

    updated_configs = await Database.get_session_configs(user_id, session_name)

    keyboard = await _build_channels_keyboard(
        cached_channels=cached_channels,
        selected_channels_ids=selected_channels_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=updated_configs,
    )

    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise

    await callback.answer(message_text, show_alert=False)


@router.callback_query(F.data.startswith("config_channel_"))
async def configure_channel(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[2])
    str_chat_id = str(chat_id)

    state_data = await state.get_data()
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode") or "export"
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    if not isinstance(session_configs, dict):
        session_configs = {}

    if "channels" not in session_configs:
        session_configs["channels"] = {}

    if str_chat_id not in session_configs["channels"]:
        session_configs["channels"][str_chat_id] = {"title": f"Канал {chat_id}", "modes": {}}

    channel_info = session_configs["channels"][str_chat_id]
    channel_title = channel_info.get("title", f"Канал {chat_id}")

    if "modes" not in channel_info:
        channel_info["modes"] = {}

    if mode not in channel_info["modes"]:
        channel_info["modes"][mode] = {
            "enabled": True,
            "filters": get_default_filters(),
        }

    channel_mode_config = channel_info["modes"][mode]
    current_filters = channel_mode_config.get("filters", {})

    await state.update_data(
        initial_filters=copy.deepcopy(current_filters),
        current_session=session_name,
        current_mode=mode,
    )

    keyboard = _build_channel_settings_keyboard(
        chat_id, session_name, mode, channel_mode_config
    )

    try:
        await callback.message.edit_text(
            f"Настройки канала: <b>{escape(channel_title)}</b> (ID: <code>{chat_id}</code>)\n"
            f"Режим: <b>{mode.capitalize()}</b>\n\n"
            "Выберите, какие типы контента разрешены:",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

    await callback.answer()


@router.callback_query(F.data.startswith("toggle_filter_"))
async def toggle_channel_filter(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    chat_id = int(parts[2])
    filter_key = parts[3]

    state_data = await state.get_data()
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode") or "export"
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    str_chat_id = str(chat_id)

    if (
        isinstance(session_configs, dict)
        and "channels" in session_configs
        and str_chat_id in session_configs["channels"]
        and "modes" in session_configs["channels"][str_chat_id]
        and mode in session_configs["channels"][str_chat_id]["modes"]
    ):
        channel_mode_config = session_configs["channels"][str_chat_id]["modes"][mode]
        current_filters = channel_mode_config.get("filters", {})

        filter_item = current_filters.get(filter_key)
        if isinstance(filter_item, dict):
            filter_item["enabled"] = not filter_item.get("enabled", True)
            is_enabled = filter_item["enabled"]
        else:
            is_enabled = not bool(filter_item)
            current_filters[filter_key] = {
                "enabled": is_enabled,
                "min": 0,
                "max": 999999,
            }

        await Database.update_session_configs(user_id, session_name, session_configs)

        keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)

        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest:
            pass

        status_text = "✅ включены" if is_enabled else "❌ отключены"
        media_name = CONTENT_TYPES.get(filter_key, filter_key)
        await callback.answer(f"{media_name.capitalize()} {status_text}", show_alert=False)
    else:
        await callback.answer("Ошибка: конфигурация канала не найдена.", show_alert=True)


@router.callback_query(F.data.startswith("apply_channel_settings_"))
async def apply_channel_settings_handler(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.removeprefix("apply_channel_settings_"))
    state_data = await state.get_data()
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode")
    user_id = callback.from_user.id

    await callback.message.edit_text(
        f"⏳ <b>Применение настроек...</b>\n\n"
        f"Выполняется перезапуск сессии <code>{session_name}</code>.\nПожалуйста, подождите...",
        parse_mode="HTML",
    )

    was_restarted = await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    fresh_configs = await Database.get_session_configs(user_id, session_name)
    fresh_filters = (
        fresh_configs.get("channels", {})
        .get(str(chat_id), {})
        .get("modes", {})
        .get(mode, {})
        .get("filters", {})
    )
    await state.update_data(initial_filters=copy.deepcopy(fresh_filters))

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Вернуться к настройкам канала", callback_data=f"config_channel_{chat_id}")],
            [InlineKeyboardButton(text="◀️ Назад к списку каналов", callback_data=f"list_export_{session_name}")],
        ]
    )

    if was_restarted:
        result_text = f"✅ <b>Настройки успешно применены!</b>\nСессия <code>{session_name}</code> перезапущена."
    else:
        result_text = f"✅ <b>Настройки сохранены!</b>\n(Пересылка выключена, применится при включении)."

    await callback.message.edit_text(result_text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()


# --- ХЭНДЛЕРЫ РЕДАКТИРОВАНИЯ ЛИМИТОВ MIN / MAX ---

@router.callback_query(F.data.startswith("edit_limits_"))
async def edit_limits_menu(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    chat_id = int(parts[2])
    filter_key = parts[3]

    state_data = await state.get_data()
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode")
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    str_chat_id = str(chat_id)

    channel_mode_config = session_configs.get("channels", {}).get(str_chat_id, {}).get("modes", {}).get(mode, {})
    filter_item = channel_mode_config.get("filters", {}).get(filter_key, {})

    if isinstance(filter_item, dict):
        min_val = filter_item.get("min", 0)
        max_val = filter_item.get("max", 999999)
        is_enabled = filter_item.get("enabled", True)
    else:
        min_val, max_val, is_enabled = 0, 999999, bool(filter_item)

    unit = FILTER_UNITS.get(filter_key, "единицах")
    media_title = CONTENT_TYPES.get(filter_key, filter_key)

    text = (
        f"⚙️ Настройки фильтра: <b>{media_title}</b>\n\n"
        f"Статус: <b>{'✅ Включен' if is_enabled else '❌ Выключен'}</b>\n"
        f"Измеряется в: <b>{unit}</b>\n\n"
        f"🔹 Минимальное значение: <code>{min_val}</code>\n"
        f"🔸 Максимальное значение: <code>{max_val}</code>\n\n"
        "Выберите, что хотите изменить:"
    )

    keyboard = _build_filter_limits_keyboard(chat_id, filter_key)
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
    unit = FILTER_UNITS.get(filter_key, "единицах")
    limit_title = "МИНИМАЛЬНОЕ" if limit_type == "min" else "МАКСИМАЛЬНОЕ"

    await callback.message.answer(
        f"✍️ Введите новое <b>{limit_title}</b> значение для <b>{media_title}</b> в {unit}:\n\n"
        f"<i>Пример: 50 (отправьте просто число в чат)</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(FilterLimitsStates.waiting_for_limit_value)
async def process_limit_input(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("⚠️ Ошибка! Пожалуйста, введите целое положительное число (например: 0, 50, 100).")
        return

    new_value = int(message.text)
    state_data = await state.get_data()

    chat_id = state_data.get("edit_chat_id")
    filter_key = state_data.get("edit_filter_key")
    limit_type = state_data.get("edit_limit_type")
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode") or "export"
    user_id = message.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    str_chat_id = str(chat_id)

    try:
        channel_mode_config = session_configs["channels"][str_chat_id]["modes"][mode]
        filters = channel_mode_config.get("filters", {})

        if not isinstance(filters.get(filter_key), dict):
            filters[filter_key] = {"enabled": True, "min": 0, "max": 999999}

        filters[filter_key][limit_type] = new_value
        channel_mode_config["filters"] = filters

        await Database.update_session_configs(user_id, session_name, session_configs)

        limit_title = "минимальное" if limit_type == "min" else "максимальное"
        media_title = CONTENT_TYPES.get(filter_key, filter_key)

        keyboard = _build_channel_settings_keyboard(chat_id, session_name, mode, channel_mode_config)

        await message.answer(
            f"✅ Для <b>{media_title}</b> установлено {limit_title} значение: <code>{new_value}</code>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения настройки: {e}")

    finally:
        await state.set_state(None)


# --- ПРОЧИЕ СЛУЖЕБНЫЕ ХЭНДЛЕРЫ ---

@router.callback_query(F.data == "ignore_btn")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer(show_alert=False)


@router.message(Command("start_session"))
async def start_my_session(message: types.Message):
    user_id = message.from_user.id
    session_name = "default_session"

    client = await SessionManager.get_or_create_client(user_id, session_name, API_ID, API_HASH)

    if client:
        me = await client.get_me()
        await message.answer(f"Сессия запущена! Вы вошли как: {me.first_name}")
    else:
        await message.answer("Сессия не найдена в базе данных.")
