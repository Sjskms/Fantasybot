# handlers/session_handler.py
# Standard library imports
import asyncio
import json
import logging
from html import escape
import copy
from typing import Any, Dict, List, Set

# Third-party library imports
from aiogram import F, Router,types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType

from pyrogram.types import Chat # 
# Local application imports
from config import API_ID, API_HASH
from database import Database
from keyboards import admin_kb, user_kb
from keyboards.session_kb import (
    get_session_management_keyboard,
    _build_filter_limits_keyboard,
    _build_channel_settings_keyboard,
    _build_channels_keyboard,
    get_logging_settings_keyboard,
    get_session_settings_keyboard,
)
from services.session_manager import SessionManager
from states.session_states import SessionAdd, SessionStates
from services.Additional_Feature import (
    active_forwarder_tasks,
    start_forwarder_for_session,
    update_live_config,
    safe_restart_forwarder,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from aiogram.fsm.state import State, StatesGroup


                                                                                                
logger = logging.getLogger(__name__)
router = Router()


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


db = Database() 

CONTENT_TYPES = {
    "photos": "Фото",
    "videos": "Видео",
    "text": "Текст",
    "documents": "Файлы",
    "music": "Музыка",
    "voices": "Голосовые",
    "video_notes": "Кружки"
}


def get_default_filters():
    return {
        "photos": {"enabled": True, "min": 0, "max": 999999},
        "videos": {"enabled": True, "min": 0, "max": 999999},
        "text": {"enabled": True, "min": 0, "max": 999999},
        "documents": {"enabled": False, "min": 0, "max": 999999},
        "music": {"enabled": False, "min": 0, "max": 999999},
        "voices": {"enabled": False, "min": 0, "max": 999999},
        "video_notes": {"enabled": True, "min": 0, "max": 999999},
    }
    


# Класс состояний для ввода лимитов
class FilterLimitsStates(StatesGroup):
    waiting_for_limit_value = State()

# Единицы измерения для каждого типа контента
FILTER_UNITS = {
    "text": "символах (длина текста)",
    "videos": "секундах (длительность)",
    "video_notes": "секундах (длительность кружочка)",
    "voices": "секундах (длительность)",
    "music": "секундах (длительность)",
    "photos": "байтах (размер файла)",
    "documents": "байтах (размер файла)"
}










# Состояния FSM
class TextTransformStates(StatesGroup):
    waiting_for_custom_text = State()
    waiting_for_replace_word = State()
    waiting_for_replace_link = State()


def get_text_transform_keyboard(session_name: str, transform_config: dict) -> InlineKeyboardMarkup:
    mode = transform_config.get("mode", "keep")
    
    m_keep = "🔘" if mode == "keep" else "⚪️"
    m_append = "🔘" if mode == "append" else "⚪️"
    m_prepend = "🔘" if mode == "prepend" else "⚪️"
    m_replace = "🔘" if mode == "replace" else "⚪️"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m_append} Добавлять в конец", callback_data=f"tt_mode_{session_name}_append")],
        [InlineKeyboardButton(text=f"{m_prepend} Добавлять в начало", callback_data=f"tt_mode_{session_name}_prepend")],
        [InlineKeyboardButton(text=f"{m_replace} Заменять текст целиком", callback_data=f"tt_mode_{session_name}_replace")],
        [InlineKeyboardButton(text=f"{m_keep} Только замены (без добавления)", callback_data=f"tt_mode_{session_name}_keep")],
        
        [InlineKeyboardButton(text="📝 Задать кастомный текст (HTML)", callback_data=f"tt_settext_{session_name}")],
        [InlineKeyboardButton(text="🔄 Добавить замену слова", callback_data=f"tt_addword_{session_name}")],
        [InlineKeyboardButton(text="🔗 Добавить замену ссылки", callback_data=f"tt_addlink_{session_name}")],
        [InlineKeyboardButton(text="🗑 Очистить все замены", callback_data=f"tt_clear_{session_name}")],
        [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data=f"manage_session_{session_name}")]
    ])





from config import API_ID, API_HASH
from services.Additional_Feature import restart_session_gracefully

from config import API_ID, API_HASH
from services.Additional_Feature import restart_session_gracefully

@router.callback_query(F.data.startswith("apply_all_changes_"))
async def apply_all_changes_handler(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("apply_all_changes_")
    user_id = callback.from_user.id
    state_data = await state.get_data()
    mode = state_data.get("current_mode", "export")

    # 1. Показываем статус ожидания
    await callback.message.edit_text(
        f"⏳ <b>Применение изменений...</b>\n\n"
        f"Выполняется мягкий перезапуск сессии <code>{session_name}</code> со всеми новыми настройками каналов и фильтров.\n"
        f"Пожалуйста, подождите...",
        parse_mode="HTML"
    )

    # 2. Перезапускаем сессию
    await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    # 3. Возвращаем пользователя обратно в список каналов (кнопка сохранения исчезнет, так как всё применилось)
    session_configs = await Database.get_session_configs(user_id, session_name)
    cached_channels = state_data.get("cached_channels", [])
    selected_ids = state_data.get("selected_channels_ids", set())

    keyboard = await _build_channels_keyboard(
        cached_channels=cached_channels,
        selected_channels_ids=selected_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=session_configs
    )

    mode_title = "Экспорт" if mode == "export" else "Постинг"
    await callback.message.edit_text(
        f"✅ <b>Все изменения успешно применены!</b>\n\n"
        f"Сессия <code>{session_name}</code> перезапущена и работает по обновленным правилам.\n\n"
        f"📋 <b>Каналы для режима: {mode_title}</b>:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer("Сессия перезапущена!", show_alert=False)















@router.callback_query(F.data.startswith("text_transform_"))
async def show_text_transform_menu(callback: CallbackQuery, state: FSMContext = None, session_name: str = None):
    # 1. Если был в режиме ожидания ввода текста/слов — сбрасываем состояние
    if state:
        await state.set_state(None)

    user_id = callback.from_user.id

    # 2. Надежно извлекаем session_name
    if not session_name:
        session_name = callback.data.removeprefix("text_transform_")

    try:
        # 3. Защита от None из базы
        session_configs = await Database.get_session_configs(user_id, session_name)
        if not session_configs or not isinstance(session_configs, dict):
            session_configs = {}

        tt_cfg = session_configs.get("text_transform", {})
        if not isinstance(tt_cfg, dict):
            tt_cfg = {}

        mode_names = {
            "keep": "Только замена слов/ссылок",
            "append": "Добавление в конец",
            "prepend": "Добавление в начало",
            "replace": "Полная замена текста"
        }

        current_mode = tt_cfg.get("mode", "keep")
        words_count = len(tt_cfg.get("replace_words", []))
        links_count = len(tt_cfg.get("replace_links", []))
        
        # 4. БЕЗОПАСНЫЙ ПРЕДПРОСМОТР: экранируем HTML, чтобы Telegram не падал
        raw_custom = tt_cfg.get("custom_text", "").strip()
        if raw_custom:
            # Экранируем спецсимволы (<, >, &)
            safe_text = html.escape(raw_custom)
            if len(safe_text) > 120:
                safe_text = safe_text[:120] + "..."
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


# Переключение режима (append / prepend / replace / keep)
@router.callback_query(F.data.startswith("tt_mode_"))
async def change_transform_mode(callback: CallbackQuery):
    # raw = "{session_name}_{mode}"
    raw = callback.data.removeprefix("tt_mode_")
    # rsplit('_', 1) отделяет только режим с конца, сохраняя имя вроде "Сессия_996506121805"
    session_name, new_mode = raw.rsplit("_", 1)
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    configs["text_transform"]["mode"] = new_mode

    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)

    # Перерисовываем меню с ЧИСТЫМ session_name
    await show_text_transform_menu(callback, session_name=session_name)


# Очистка всех правил
@router.callback_query(F.data.startswith("tt_clear_"))
async def clear_transform_rules(callback: CallbackQuery):
    session_name = callback.data.removeprefix("tt_clear_")
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    configs["text_transform"] = {
        "mode": "keep",
        "custom_text": "",
        "replace_words": [],
        "replace_links": []
    }

    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)
    await callback.answer("Все правила текста и ссылок очищены!", show_alert=True)
    
    # Перерисовываем меню с ЧИСТЫМ session_name
    await show_text_transform_menu(callback, session_name=session_name)


# --- 1. Задать кастомный текст ---
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
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(TextTransformStates.waiting_for_custom_text)
async def process_custom_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    user_id = message.from_user.id

    configs = await Database.get_session_configs(user_id, session_name)
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    
    # Сохраняем введенный HTML-текст
    configs["text_transform"]["custom_text"] = message.text or message.caption or ""
    
    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)
    await state.set_state(None)

    # Загружаем обновленную конфигурацию
    tt_cfg = configs.get("text_transform", {})

    mode_names = {
        "keep": "Только замена слов/ссылок",
        "append": "Добавление в конец",
        "prepend": "Добавление в начало",
        "replace": "Полная замена текста"
    }

    words_count = len(tt_cfg.get("replace_words", []))
    links_count = len(tt_cfg.get("replace_links", []))
    custom_preview = tt_cfg.get("custom_text") or "<i>(не задан)</i>"
    if len(custom_preview) > 100:
        custom_preview = custom_preview[:100] + "..."

    text = (
        f"✅ <b>Кастомный текст успешно сохранен!</b>\n\n"
        f"✏️ <b>Настройки текста и ссылок:</b> <code>{session_name}</code>\n\n"
        f"🔹 <b>Режим вставки:</b> {mode_names.get(tt_cfg.get('mode', 'keep'))}\n"
        f"🔹 <b>Правил замены слов:</b> {words_count}\n"
        f"🔹 <b>Правил замены ссылок:</b> {links_count}\n\n"
        f"📝 <b>Текущий кастомный текст:</b>\n{custom_preview}\n\n"
        "Выберите действие ниже:"
    )

    # Получаем полные кнопки меню настроек
    keyboard = get_text_transform_keyboard(session_name, tt_cfg)

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# --- 2. Добавить замену слова ---
@router.callback_query(F.data.startswith("tt_addword_"))
async def prompt_replace_word(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_addword_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_replace_word)

    await callback.message.answer(
        "🔤 Введите старое и новое слово через знак <code>=</code>\n\n"
        "<i>Пример:</i>\n<code>скидка = распродажа</code>\n"
        "<i>Или чтобы удалить слово, оставьте правую часть пустой:</i>\n<code>реклама = </code>",
        parse_mode="HTML"
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
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    if "replace_words" not in configs["text_transform"]:
        configs["text_transform"]["replace_words"] = []

    configs["text_transform"]["replace_words"].append({"from": old_w, "to": new_w})

    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)
    await state.set_state(None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ К настройкам текста", callback_data=f"text_transform_{session_name}")]
    ])
    await message.answer(
        f"✅ Правило замены слова добавлено: <code>{old_w}</code> ➡️ <code>{new_w}</code>",
        parse_mode="HTML",
        reply_markup=kb
    )


# --- 3. Добавить замену ссылки ---
@router.callback_query(F.data.startswith("tt_addlink_"))
async def prompt_replace_link(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_addlink_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_replace_link)

    await callback.message.answer(
        "🔗 Введите старую ссылку и новую ссылку через знак <code>=</code>\n\n"
        "<i>Пример:</i>\n"
        "<code>https://t.me/old_channel = https://t.me/my_channel</code>",
        parse_mode="HTML"
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
    if "text_transform" not in configs:
        configs["text_transform"] = {}
    if "replace_links" not in configs["text_transform"]:
        configs["text_transform"]["replace_links"] = []

    configs["text_transform"]["replace_links"].append({"from": old_l, "to": new_l})

    await Database.update_session_configs(user_id, session_name, configs)
    await update_live_config(user_id, session_name)
    await state.set_state(None)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ К настройкам текста", callback_data=f"text_transform_{session_name}")]
    ])
    await message.answer(
        f"✅ Правило замены ссылки добавлено:\n<code>{old_l}</code> ➡️ <code>{new_l}</code>",
        parse_mode="HTML",
        reply_markup=kb
    )
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
@router.callback_query(F.data.startswith("session_logging_"))
async def show_logging_settings(callback: CallbackQuery):
    session_name = callback.data.split("_", 2)[2]
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    log_config = session_configs.get("logging", {
        "enabled": True,
        "log_success": True,
        "log_filtered": False,
        "log_errors": True
    })

    text = (
        f"📜 <b>Настройки логирования для сессии:</b> <code>{session_name}</code>\n\n"
        "Выберите, какие события бот будет отправлять вам в личные сообщения:"
    )

    keyboard = get_logging_settings_keyboard(session_name, log_config)
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()    
    
    
    


        
            
from aiogram.exceptions import TelegramBadRequest
from services.Additional_Feature import update_live_config

@router.callback_query(F.data.startswith("toggle_log_"))
async def toggle_log_option_handler(callback: CallbackQuery):
    # Извлекаем session_name и log_type (надежно даже если в имени сессии есть '_')
    raw_data = callback.data.removeprefix("toggle_log_")
    session_name, log_type = raw_data.rsplit("_", 1)
    
    user_id = callback.from_user.id

    # 1. Загружаем текущие конфиги сессии из БД
    session_configs = await Database.get_session_configs(user_id, session_name)
    
    if "logging" not in session_configs:
        session_configs["logging"] = {
            "enabled": True,
            "log_success": True,
            "log_filtered": False,
            "log_errors": True
        }

    log_config = session_configs["logging"]

    # 2. Переключаем нужный параметр
    if log_type == "main":
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

    # 3. Сохраняем в БД
    await Database.update_session_configs(user_id, session_name, session_configs)

    # 4. Обновляем конфиг в оперативной памяти юзербота
    await update_live_config(user_id, session_name)

    # 5. Обновляем клавиатуру
    keyboard = get_logging_settings_keyboard(session_name, log_config)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest:
        pass

    await callback.answer(status_msg, show_alert=False)
                            
                                
                                    
                                        
                                            
                                                
                                                        
    
        
@router.callback_query(F.data.startswith("toggle_posting_"))
async def toggle_posting_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    session_name = callback.data.split("_", 2)[2]
    task_key = (user_id, session_name)

    current_status = await Database.get_session_posting_status(user_id, session_name)
    new_status = not current_status

    if new_status:
        post_channels_count = await Database.get_session_channels_count(user_id, session_name, 'post')
        export_channels_count = await Database.get_session_channels_count(user_id, session_name, 'export')

        if post_channels_count == 0 or export_channels_count == 0:
            await callback.answer(
                "⚠️ Невозможно включить пересылку: убедитесь, что настроен как минимум один канал для постинга и один для экспорта.",
                show_alert=True
            )
            return

        await Database.update_session_posting_status(user_id, session_name, True)

        if task_key in active_forwarder_tasks:
            active_forwarder_tasks[task_key].cancel()

        task = asyncio.create_task(
            start_forwarder_for_session(
                user_id=user_id,
                session_name=session_name,
                api_id=API_ID,
                api_hash=API_HASH
            )
        )
        active_forwarder_tasks[task_key] = task

    else:
        await Database.update_session_posting_status(user_id, session_name, False)

        if task_key in active_forwarder_tasks:
            task = active_forwarder_tasks.pop(task_key)
            task.cancel()

    updated_keyboard = get_session_management_keyboard(session_name, new_status)
    await callback.message.edit_reply_markup(reply_markup=updated_keyboard)
    await callback.answer(f"Пересылка {'✅ включена' if new_status else '❌ выключена'}.")
    






    
# 1. Открытие подменю настройки конкретного медиа (Мин/Макс)
@router.callback_query(F.data.startswith("edit_limits_"))
async def edit_limits_menu(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    chat_id = int(parts[2])
    filter_key = parts[3] # 'video_notes'

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


# 2. Запрос на ввод нового числа (Мин или Макс)
@router.callback_query(F.data.startswith("set_limit_"))
async def prompt_limit_input(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    chat_id = int(parts[2])
    filter_key = parts[3]
    limit_type = parts[4] # "min" или "max"

    # Сохраняем во временный FSMContext, что именно редактируем
    await state.update_data(
        edit_chat_id=chat_id,
        edit_filter_key=filter_key,
        edit_limit_type=limit_type
    )
    await state.set_state(FilterLimitsStates.waiting_for_limit_value)

    media_title = CONTENT_TYPES.get(filter_key, filter_key)
    unit = FILTER_UNITS.get(filter_key, "единицах")
    limit_title = "МИНИМАЛЬНОЕ" if limit_type == "min" else "МАКСИМАЛЬНОЕ"

    await callback.message.answer(
        f"✍️ Введите новое <b>{limit_title}</b> значение для <b>{media_title}</b> в {unit}:\n\n"
        f"<i>Пример: 50 (отправьте просто число в чат)</i>",
        parse_mode="HTML"
    )
    await callback.answer()


# 3. Прием текста с числом и запись в БД
@router.message(FilterLimitsStates.waiting_for_limit_value)
async def process_limit_input(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("⚠️ Ошибка! Пожалуйста, введите целое положительное число (например: 0, 50, 100).")
        return

    new_value = int(message.text)
    state_data = await state.get_data()

    chat_id = state_data.get("edit_chat_id")
    filter_key = state_data.get("edit_filter_key")
    limit_type = state_data.get("edit_limit_type") # "min" или "max"
    session_name = state_data.get("current_session")
    mode = state_data.get("current_mode") or "export"
    user_id = message.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name)
    str_chat_id = str(chat_id)

    try:
        channel_mode_config = session_configs["channels"][str_chat_id]["modes"][mode]
        filters = channel_mode_config.get("filters", {})

        # Гарантируем структуру словаря
        if not isinstance(filters.get(filter_key), dict):
            filters[filter_key] = {"enabled": True, "min": 0, "max": 999999}

        # Сохраняем новое число
        filters[filter_key][limit_type] = new_value
        channel_mode_config["filters"] = filters

        # Записываем в базу данных
        await Database.update_session_configs(user_id, session_name, session_configs)

        limit_title = "минимальное" if limit_type == "min" else "максимальное"
        media_title = CONTENT_TYPES.get(filter_key, filter_key)
        
        keyboard = _build_channel_settings_keyboard(
        chat_id, session_name, mode, channel_mode_config
        )
        
       

        # Кнопки только для навигации (никаких предложений перезапуска здесь)
        #kb_builder = InlineKeyboardBuilder()
#        kb_builder.button(
#            text="⚙️ К настройкам канала",
#            callback_data=f"config_channel_{chat_id}"
#        )
#        kb_builder.button(
#            text="✏️ Изменить другой лимит",
#            callback_data=f"edit_limits_{chat_id}_{filter_key}"
#        )
#        kb_builder.adjust(1)

        await message.answer(
            f"✅ Для <b>{media_title}</b> установлено {limit_title} значение: <code>{new_value}</code>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения настройки: {e}")

    finally:
        await state.set_state(None)
        
        
        
        
        



    
    

@router.callback_query(F.data.startswith("session_config2_"))
async def config_menu(callback: CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    session_name = "_".join(data_parts[2:])
    await state.update_data(current_session=session_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Каналы для экспорта", callback_data=f"list_export_{session_name}")],
        [InlineKeyboardButton(text="📤 Каналы для постинга", callback_data=f"list_post_{session_name}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"select_session_{session_name}")]
    ])
    await callback.message.edit_text(f"Выберите категорию каналов: ({session_name})", reply_markup=kb)
    
    
    
    
@router.callback_query(F.data.startswith("session_config_"))
async def session_config_handler(callback: CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")          
    session_name = "_".join(data_parts[2:])
    user_id = callback.from_user.id
    await state.update_data(current_session_name=session_name)

    #Получаем статус пересылки
    enable_posting_status = await Database.get_session_posting_status(user_id, session_name)
    
    keyboard = get_session_management_keyboard(session_name, enable_posting_status) # Передаем статус

    await callback.message.edit_text(
        f"⚙️ Управление сессией {escape(session_name)}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()



@router.callback_query(F.data.startswith("toggle_select_"))
async def toggle_channel_selection(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[2])

    state_data = await state.get_data()
    selected_channels_ids: Set[int] = state_data.get("selected_channels_ids", set())
    cached_channels: List[Dict] = state_data.get("cached_channels", [])
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode")
    user_id = callback.from_user.id

    # 1. Загружаем текущие конфигурации сессии из БД
    session_configs: Dict[str, Any] = await Database.get_session_configs(user_id, session_name)

    # Инициализируем структуру, если она отсутствует
    if "channels" not in session_configs:
        session_configs["channels"] = {}

    str_chat_id = str(chat_id)
    if str_chat_id not in session_configs["channels"]:
        # Если канала нет в конфигах, добавляем его с дефолтными значениями
        channel_title = "Unknown Channel"
        for ch in cached_channels:
            if ch["id"] == chat_id:
                channel_title = ch["title"]
                break
        session_configs["channels"][str_chat_id] = {"title": channel_title, "modes": {}}

    channel_config = session_configs["channels"][str_chat_id]

    if "modes" not in channel_config:
        channel_config["modes"] = {}

    if mode not in channel_config["modes"]:
        # Инициализируем дефолтные фильтры, если режима еще нет
        #default_filters = {key: True for key in CONTENT_TYPES.keys()}
        channel_config["modes"][mode] = {
            "enabled": False, # По умолчанию выключен, пока не будет выбран
            "filters": get_default_filters()
        }

    # 2. Обновляем состояние выбора (включен/выключен)
    if chat_id in selected_channels_ids:
        selected_channels_ids.remove(chat_id)
        channel_config["modes"][mode]["enabled"] = False # Отмечаем канал как выключенный в этом режиме
        message_text = "Канал отменен."
    else:
        selected_channels_ids.add(chat_id)
        channel_config["modes"][mode]["enabled"] = True # Отмечаем канал как включенный в этом режиме
        message_text = "Канал выбран."

    # 3. Сохраняем обновленные конфигурации обратно в БД
    await Database.update_session_configs(user_id, session_name, session_configs)

    # 4. Обновляем FSMContext для selected_channels_ids (необходимо для перерисовки клавиатуры)
    await state.update_data(selected_channels_ids=selected_channels_ids)

    # 5. Пересоздаем и отправляем обновленную клавиатуру
    keyboard = await _build_channels_keyboard(
        cached_channels=cached_channels,
        selected_channels_ids=selected_channels_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=session_configs
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer(message_text, show_alert=False)
    
    
    
@router.callback_query(F.data.startswith("list_"))
async def list_channels(callback: CallbackQuery, state: FSMContext):
    data_parts = callback.data.split("_")
    mode = data_parts[1]              # export / post
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
                    else:
                        continue 
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
    if "channels" in session_configs:
        for str_chat_id, channel_data in session_configs["channels"].items():
            if "modes" in channel_data and mode in channel_data["modes"]:
                if channel_data["modes"][mode].get("enabled", False):
                    try:
                        current_selected_channels_ids.add(int(str_chat_id))
                    except ValueError:
                        pass

    await state.update_data(selected_channels_ids=current_selected_channels_ids)

    # 👈 Передаем user_id и session_configs для проверки изменений
    keyboard = await _build_channels_keyboard(
        cached_channels=fetched_channels,
        selected_channels_ids=current_selected_channels_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=session_configs
    )

    await callback.message.edit_text(
        f"Выберите канал{'ы' if len(current_selected_channels_ids) > 0 else ''} для {'постинга' if mode == 'post' else 'экспорта'}:",
        reply_markup=keyboard
    )
    await callback.answer()
    
   
    
    
    
# Обработчик для кнопок ✅ и ❌
@router.callback_query(F.data.startswith("toggle_select_"))
async def toggle_channel_selection(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[2]) # Получаем ID канала из callback_data

    state_data = await state.get_data()
    selected_channels_ids: Set[int] = state_data.get("selected_channels_ids", set())
    cached_channels: List[Dict] = state_data.get("cached_channels", [])
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode")

    if chat_id in selected_channels_ids:
        selected_channels_ids.remove(chat_id)
        message_text = "Канал отменен."
    else:
        selected_channels_ids.add(chat_id)
        message_text = "Канал выбран."

    await state.update_data(selected_channels_ids=selected_channels_ids)

    # Пересоздаем и отправляем обновленную клавиатуру
    keyboard = await _build_channels_keyboard(
        cached_channels=cached_channels,
        selected_channels_ids=selected_channels_ids,
        session_name=session_name,
        mode=mode,
        user_id=user_id,
        session_configs=session_configs
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer(message_text, show_alert=False) # Небольшое уведомление без блокировки




# Обработчик для кнопки ⚙️ (Настройки канала)
@router.callback_query(F.data.startswith("config_channel_"))
async def configure_channel(callback: CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split("_")[2])
    str_chat_id = str(chat_id)

    state_data = await state.get_data()
    session_name: str = state_data.get("current_session")
    mode: str = state_data.get("current_mode") or "export"
    user_id = callback.from_user.id

    # 1. Загружаем конфиг из БД
    session_configs = await Database.get_session_configs(user_id, session_name)

    # 2. Инициализируем структуру, если её еще нет
    if "channels" not in session_configs:
        session_configs["channels"] = {}

    if str_chat_id not in session_configs["channels"]:
        session_configs["channels"][str_chat_id] = {"title": f"Канал {chat_id}", "modes": {}}

    channel_info = session_configs["channels"][str_chat_id]
    
    # 👉 ВОТ ЗДЕСЬ ОПРЕДЕЛЯЕТСЯ channel_title:
    channel_title = channel_info.get("title", f"Канал {chat_id}")

    if "modes" not in channel_info:
        channel_info["modes"] = {}

    if mode not in channel_info["modes"]:
        channel_info["modes"][mode] = {
            "enabled": True,
            "filters": get_default_filters()
        }

    channel_mode_config = channel_info["modes"][mode]
    current_filters = channel_mode_config.get("filters", {})

    # 3. Сохраняем исходный снимок фильтров в FSM для умного сравнения изменений
    await state.update_data(
        initial_filters=copy.deepcopy(current_filters),
        current_session=session_name,
        current_mode=mode
    )

    # 4. При входе изменений нет -> has_changes=False
    keyboard = _build_channel_settings_keyboard(
    chat_id, session_name, mode, channel_mode_config
    )

    try:
        await callback.message.edit_text(
            f"Настройки канала: <b>{channel_title}</b> (ID: <code>{chat_id}</code>)\n"
            f"Режим: <b>{mode.capitalize()}</b>\n\n"
            "Выберите, какие типы контента разрешены:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
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

    if "channels" in session_configs and str_chat_id in session_configs["channels"] and \
       "modes" in session_configs["channels"][str_chat_id] and \
       mode in session_configs["channels"][str_chat_id]["modes"]:

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
                "max": 999999
            }

        await Database.update_session_configs(user_id, session_name, session_configs)

        keyboard = _build_channel_settings_keyboard(
        chat_id, session_name, mode, channel_mode_config
        )

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
        parse_mode="HTML"
    )

    was_restarted = await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    # Загружаем свежие примененные фильтры и обновляем исходный снимок
    fresh_configs = await Database.get_session_configs(user_id, session_name)
    fresh_filters = fresh_configs.get("channels", {}).get(str(chat_id), {}).get("modes", {}).get(mode, {}).get("filters", {})
    await state.update_data(initial_filters=copy.deepcopy(fresh_filters))

    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Вернуться к настройкам канала", callback_data=f"config_channel_{chat_id}")],
        [InlineKeyboardButton(text="◀️ Назад к списку каналов", callback_data=f"list_export_{session_name}")]
    ])

    if was_restarted:
        result_text = f"✅ <b>Настройки успешно применены!</b>\nСессия <code>{session_name}</code> перезапущена."
    else:
        result_text = f"✅ <b>Настройки сохранены!</b>\n(Пересылка выключена, применится при включении)."

    await callback.message.edit_text(result_text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

# Обработчик для кнопки, которая является просто меткой и не должна вызывать действие
@router.callback_query(F.data == "ignore_btn")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer(show_alert=False) # Просто закрываем всплывающее уведомление
    
    
    
    
    
    
    
    

        
                
    
    
    
@router.callback_query(F.data.startswith("config_"))
async def channel_settings(callback: CallbackQuery):
    mode, channel_id = callback.data.split("_")[1], callback.data.split("_")[2]

    if mode == "post":
        pass
    else:
        # Здесь логика перехода в FSM для ввода min/max параметров
        await callback.message.answer("Введите параметры фильтрации (min,max) для этого канала:")    
    
@staticmethod
async def update_session_config(user_id, session_name, channel_id, new_data):
    async with aiosqlite.connect(Database.DB_NAME) as db:
        # 1. Достаем текущий конфиг
        cursor = await db.execute(
            "SELECT session_configs_json FROM user_sessions WHERE user_id = ? AND session_name = ?",
            (user_id, session_name)
        )
        row = await cursor.fetchone()
        
        # Парсим JSON или создаем пустой словарь
        config = json.loads(row[0]) if row and row[0] else {}
        
        # 2. Подготавливаем структуру для конкретного канала
        if channel_id not in config:
            config[channel_id] = {
                "is_enabled": False,
                "Channel_type": "None",
                "filters": {"text": {"min": 0, "max": 9999}, "video": {"min": 0, "max": 9999}}
            }
        
        # 3. Применяем изменения через deep_update
        config[channel_id] = deep_update(config[channel_id], new_data)
        
        # 4. Сохраняем
        await db.execute(
            "UPDATE user_sessions SET session_configs_json = ? WHERE user_id = ? AND session_name = ?",
            (json.dumps(config), user_id, session_name)
        )
        await db.commit()    
    
    
    
import json

def deep_update(mapping, *updating_mappings):
    updated_mapping = mapping.copy()
    for updating_mapping in updating_mappings:
        for k, v in updating_mapping.items():
            if k in updated_mapping and isinstance(updated_mapping[k], dict) and isinstance(v, dict):
                updated_mapping[k] = deep_update(updated_mapping[k], v)
            else:
                updated_mapping[k] = v
    return updated_mapping    
    
    


async def get_client(session_string: str) -> Client:
    client = Client(
        name=session_string[:10], # Уникальное имя для клиента, можно использовать часть сессии
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        no_updates=True # Часто полезно отключать обновления, если клиент нужен только для разовой выборки данных
    )
    return client    
    
    











# Вместо commands=["start_session"] используем Command("start_session")
@router.message(Command("start_session"))
async def start_my_session(message: types.Message):
    user_id = message.from_user.id
    session_name = "default_session" 
    
    # Пытаемся получить или создать клиента
    client = await SessionManager.get_or_create_client(user_id, session_name, API_ID, API_HASH)
    
    if client:
        me = await client.get_me()
        await message.answer(f"Сессия запущена! Вы вошли как: {me.first_name}")
    else:
        await message.answer("Сессия не найдена в базе данных.")
        
        
        
 


    
    
# --- Callback хендлеры для меню сессий ---

@router.callback_query(F.data == "session_settings")
async def process_session_settings(callback: types.CallbackQuery, state: FSMContext):
    await state.clear() # Очищаем состояние на всякий случай
    user_sessions = await Database.get_user_sessions(callback.from_user.id)
    markup = get_session_settings_keyboard(user_sessions)
    await callback.message.edit_text("⚙️ Ваши Telegram сессии:", reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data.startswith("select_session_"))
async def process_select_session(callback: types.CallbackQuery):
    session_name = callback.data.replace("select_session_", "")
    user_id = callback.from_user.id
    enable_posting_status = await db.get_session_posting_status(user_id, session_name)
    markup = get_session_management_keyboard(session_name, enable_posting_status)
    
    #markup = get_session_management_keyboard(session_name)
  
    text = f"Управление сессией: {escape(session_name)}\n\n<b>Что вы хотите сделать?</b>"
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    
    await callback.answer()




#########_УДАЛЕНИЕ СЕССИЙ #########


# ШАГ 1: Показываем окно с подтверждением (Да / Отмена)
@router.callback_query(F.data.startswith("delete_session_"))
async def ask_delete_session_confirmation(callback: types.CallbackQuery):
    session_name = callback.data.replace("delete_session_", "")

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑 Да, удалить", 
                callback_data=f"confirm_delete_session_{session_name}"
            ),
            InlineKeyboardButton(
                text="❌ Отмена", 
                callback_data=f"session_config_{session_name}"  # Возврат обратно в управление этой сессией
            )
        ]
    ])

    text = (
        f"⚠️ <b>Подтверждение удаления</b>\n\n"
        f"Вы действительно хотите удалить сессию <code>{session_name}</code>?\n"
        f"Все сохраненные настройки фильтров и каналов будут удалены безвозвратно."
    )

    try:
        await callback.message.edit_text(text, reply_markup=confirm_keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass

    await callback.answer()


# ШАГ 2: Удаление сессии, остановка юзербота и возврат к обновленному списку
@router.callback_query(F.data.startswith("confirm_delete_session_"))
async def process_confirmed_delete_session(callback: types.CallbackQuery):
    session_name = callback.data.replace("confirm_delete_session_", "")
    user_id = callback.from_user.id
    task_key = (user_id, session_name)

    # 1. Если для этой сессии работал автопостинг — останавливаем его
    if task_key in active_forwarder_tasks:
        task = active_forwarder_tasks.pop(task_key)
        task.cancel()

    # 2. Удаляем из БД
    await Database.delete_session(user_id, session_name)
    await callback.answer(f"Сессия '{session_name}' успешно удалена.", show_alert=True)

    # 3. Получаем свежий список сессий и перерисовываем исходное меню
    user_sessions = await Database.get_user_sessions(user_id)
    markup = get_session_settings_keyboard(user_sessions)
    
    try:
        await callback.message.edit_text("⚙️ Ваши Telegram сессии:", reply_markup=markup)
    except TelegramBadRequest:
        pass

####################
    
