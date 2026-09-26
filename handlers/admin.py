# handlers/admin.py
import asyncio
import logging
import os
from html import escape
from typing import Optional

from aiogram import Bot, F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import Database
from keyboards import admin_kb
from my_filters.admin_filter import IsAdmin, IsSuperAdmin
from services.logging_service import (
    LOG_FILE_PATH,
    cleanup_log_file,
    get_cached_logging_config,
    load_global_logging_config,
    save_global_logging_config,
)

router = Router()
logger = logging.getLogger(__name__)


# --- СОСТОЯНИЯ FSM ДЛЯ РАССЫЛКИ, УПРАВЛЕНИЯ И ЛОГИРОВАНИЯ ---
class BroadcastStates(StatesGroup):
    waiting_for_initial_content = State()
    preview_menu = State()
    waiting_for_text = State()
    waiting_for_media = State()
    waiting_for_buttons = State()


class AdminManageStates(StatesGroup):
    waiting_for_add_admin_id = State()
    waiting_for_remove_admin_id = State()


class LoggingManageStates(StatesGroup):
    waiting_for_log_chat_id = State()


# --- КОМАНДЫ ДЛЯ АДМИНИСТРАТОРА ---

@router.message(Command("admin", "adm"), IsAdmin())
async def cmd_admin_menu(message: Message, state: FSMContext):
    """Открывает главное меню администратора по команде /admin или /adm."""
    await state.clear()
    await message.answer(
        "👑 <b>Панель администратора</b>",
        reply_markup=admin_kb.main_menu,
        parse_mode="HTML",
    )

# Пример: /grant_premium 7916504148 30 (выдать на 30 дней)
@router.message(Command("grant_premium"),IsAdmin())
async def admin_grant_premium(message: Message):
    
    parts = message.text.split()
    if len(parts) < 3:
        return await message.answer("Использование: `/grant_premium <user_id> <дни>`", parse_mode="Markdown")

    target_user_id = int(parts[1])
    days = int(parts[2])

    end_date_str = await Database.add_premium(target_user_id, days)
    await message.answer(f"✅ Премиум для <code>{target_user_id}</code> успешно выдан до <b>{end_date_str}</b>!", parse_mode="HTML")
    
    
    
# --- КЛАВИАТУРЫ ДЛЯ МЕНЮ ЛОГИРОВАНИЯ ---

EVENT_TITLES = {
    "new_user": "👤 Новый пользователь",
    "session_added": "📥 Добавлена сессия",
    "session_removed": "🗑 Удалена сессия",
    "forward_success": "✅ Успешная пересылка",
    "forward_filtered": "ℹ️ Отфильтрованное сообщение",
    "forward_error": "⚠️ Ошибка отправки",
    "forwarding_enabled": "🟢 Пересылка включена",
    "forwarding_disabled": "🔴 Пересылка выключена",
    "bot_error": "❌ Ошибка в боте",
    "other": "📝 Другое логирование",
}


def get_logging_main_kb(cfg: dict) -> InlineKeyboardMarkup:
    """Главное меню настроек логирования."""
    bot_log_icon = "✅" if cfg.get("bot_logging", True) else "❌"
    tg_log_icon = "✅" if cfg.get("telegram_logging", True) else "❌"
    con_log_icon = "✅" if cfg.get("console_logging", True) else "❌"
    file_log_icon = "✅" if cfg.get("file_logging", True) else "❌"

    chat_id_val = cfg.get("telegram_log_chat_id")
    # Изменено: вместо 'Все админы' пишем 'Админу'
    chat_display = str(chat_id_val) if chat_id_val else "Админу"

    period_map = {
        "1_day": "1 день",
        "3_days": "3 дня",
        "1_week": "1 неделя",
        "2_weeks": "2 недели",
        "1_month": "1 месяц",
        "never": "Не удалять",
    }
    period_str = period_map.get(cfg.get("file_cleanup_period", "1_week"), "1 неделя")

    keyboard = [
        [InlineKeyboardButton(text=f"Логирование бота: {bot_log_icon}", callback_data="toggle_glog_bot_logging")],
        [InlineKeyboardButton(text=f"Логирование в телеграм: {tg_log_icon}", callback_data="toggle_glog_telegram_logging")],
        [InlineKeyboardButton(text=f"Логирование в консоль: {con_log_icon}", callback_data="toggle_glog_console_logging")],
        [InlineKeyboardButton(text=f"Логирование в файл: {file_log_icon}", callback_data="toggle_glog_file_logging")],
        [InlineKeyboardButton(text=f"🎯 Куда слать в TG: {chat_display}", callback_data="settings_log_chat_prompt")],
        [InlineKeyboardButton(text=f"📁 Очистка файла: {period_str}", callback_data="settings_log_cleanup_menu")],
        [InlineKeyboardButton(text="📥 Выгрузить файл логов", callback_data="download_log_file")],
        [InlineKeyboardButton(text="⚙️ Настроить события логирования", callback_data="settings_logging_events")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_cleanup_period_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора срока хранения логов в файле."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 день", callback_data="set_clean_1_day"),
                InlineKeyboardButton(text="3 дня", callback_data="set_clean_3_days"),
            ],
            [
                InlineKeyboardButton(text="1 неделя", callback_data="set_clean_1_week"),
                InlineKeyboardButton(text="2 недели", callback_data="set_clean_2_weeks"),
            ],
            [
                InlineKeyboardButton(text="1 месяц", callback_data="set_clean_1_month"),
                InlineKeyboardButton(text="Не удалять", callback_data="set_clean_never"),
            ],
            [
                InlineKeyboardButton(text="🔥 Удалить прямо сейчас", callback_data="set_clean_delete_now"),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад к логированию", callback_data="settings_logging"),
            ],
        ]
    )


def get_logging_events_kb(cfg: dict) -> InlineKeyboardMarkup:
    """Подменю детальной настройки событий логирования."""
    events_cfg = cfg.get("events", {})
    keyboard = []

    for event_key, title in EVENT_TITLES.items():
        is_on = events_cfg.get(event_key, True)
        icon = "✅" if is_on else "❌"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{title}: {icon}",
                callback_data=f"toggle_gevt_{event_key}"
            )
        ])

    keyboard.append([InlineKeyboardButton(text="◀️ Назад к общим настройкам", callback_data="settings_logging")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# --- ВЫГРУЗКА ФАЙЛА ЛОГОВ ---

@router.callback_query(F.data == "download_log_file", IsAdmin())
async def download_log_file_handler(callback_query: CallbackQuery):
    """Выгружает и отправляет файл bot_events.log администратору."""
    if not os.path.exists(LOG_FILE_PATH) or os.path.getsize(LOG_FILE_PATH) == 0:
        await callback_query.answer("⚠️ Файл логов пока пуст или еще не создан!", show_alert=True)
        return

    try:
        file_to_send = FSInputFile(LOG_FILE_PATH, filename="bot_events.log")
        file_size_kb = round(os.path.getsize(LOG_FILE_PATH) / 1024, 2)
        
        await callback_query.message.answer_document(
            document=file_to_send,
            caption=f"📄 <b>Файл системных логов бота</b>\n📊 Размер: <code>{file_size_kb} KB</code>",
            parse_mode="HTML"
        )
        await callback_query.answer("Логи успешно отправлены!")
    except Exception as e:
        logger.exception("Ошибка отправки файла логов: %s", e)
        await callback_query.answer(f"❌ Ошибка выгрузки: {e}", show_alert=True)


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ПАРСИНГ РАССЫЛКИ ---

def parse_buttons_from_text(raw_text: str) -> list[dict]:
    """Парсит строки формата: Текст - Ссылка."""
    buttons = []
    if not raw_text:
        return buttons

    lines = raw_text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = None
        for sep in [" - ", " – ", " — ", "-", "="]:
            if sep in line:
                parts = line.split(sep, 1)
                break

        if not parts or len(parts) != 2:
            continue

        btn_text = parts[0].strip()
        btn_url = parts[1].strip()

        if not btn_text or not btn_url:
            continue

        if not (btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("tg://")):
            continue

        buttons.append({"text": btn_text, "url": btn_url})

    return buttons


async def render_broadcast_preview(chat_id: int, state: FSMContext, bot: Bot):
    """Отрисовывает сообщение предпросмотра рассылки."""
    data = await state.get_data()
    text = data.get("text", "")
    media_type = data.get("media_type")
    media_id = data.get("media_id")
    buttons = data.get("buttons") or []

    user_buttons = []
    for b in buttons:
        user_buttons.append([InlineKeyboardButton(text=b["text"], url=b["url"])])

    admin_action_kb = [
        [
            InlineKeyboardButton(text="✏️ Изменить текст", callback_data="bc_edit_text"),
            InlineKeyboardButton(text="🖼 Изменить/добавить медиа", callback_data="bc_edit_media"),
        ],
        [
            InlineKeyboardButton(text="🔘 Настроить кнопки", callback_data="bc_edit_buttons"),
        ],
    ]

    if media_id:
        admin_action_kb.append([InlineKeyboardButton(text="🗑 Удалить медиа", callback_data="bc_remove_media")])

    if buttons:
        admin_action_kb.append([InlineKeyboardButton(text="🗑 Удалить все кнопки", callback_data="bc_remove_buttons")])

    admin_action_kb.append([
        InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data="bc_send_now"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel"),
    ])

    full_markup = InlineKeyboardMarkup(inline_keyboard=user_buttons + admin_action_kb)

    try:
        if media_type == "photo":
            await bot.send_photo(chat_id, photo=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        elif media_type == "video":
            await bot.send_video(chat_id, video=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        elif media_type == "animation":
            await bot.send_animation(chat_id, animation=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        elif media_type == "voice":
            await bot.send_voice(chat_id, voice=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        elif media_type == "audio":
            await bot.send_audio(chat_id, audio=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        elif media_type == "document":
            await bot.send_document(chat_id, document=media_id, caption=text, parse_mode="HTML", reply_markup=full_markup)
        else:
            display_text = text if text else "<i>(Текст рассылки пока не задан)</i>"
            await bot.send_message(chat_id, display_text, parse_mode="HTML", reply_markup=full_markup)
    except Exception as e:
        logger.exception("Ошибка отрисовки предпросмотра: %s", e)
        await bot.send_message(
            chat_id,
            f"⚠️ <b>Ошибка при формировании предпросмотра:</b> <code>{escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_action_kb),
        )


# --- РАЗДЕЛ НАСТРОЕК АДМИНИСТРАТОРА ---

@router.callback_query(F.data == "settings", IsAdmin())
async def admin_settings_handler(callback_query: CallbackQuery, state: FSMContext):
    """Главное меню настроек администратора."""
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Настроить логирование", callback_data="settings_logging")],
            [InlineKeyboardButton(text="🔐 Настроить доступ", callback_data="settings_access")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="admin_panel")],
        ]
    )
    await callback_query.message.edit_text(
        "⚙️ <b>Настройки администратора</b>\n\nВыберите нужный раздел:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data == "settings_access", IsAdmin())
async def settings_access_handler(callback_query: CallbackQuery):
    """Заглушка для раздела доступа."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад в настройки", callback_data="settings")],
        ]
    )
    await callback_query.message.edit_text(
        "⚠️ <b>Данное меню временно недоступно.</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback_query.answer()


# --- ХЭНДЛЕРЫ ЛОГИРОВАНИЯ ---

@router.callback_query(F.data == "settings_logging", IsAdmin())
async def show_logging_main_menu(callback_query: CallbackQuery, state: FSMContext):
    """Отображение главного меню настроек логирования."""
    await state.clear()
    cfg = await load_global_logging_config()
    text = (
        "📜 <b>Управление логированием системы</b>\n\n"
        "Здесь вы можете настроить каналы отправки (Telegram, консоль, файл), "
        "указать целевой чат, выгрузить файл или настроить периодичность автоочистки:"
    )
    await callback_query.message.edit_text(
        text,
        reply_markup=get_logging_main_kb(cfg),
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data.startswith("toggle_glog_"), IsAdmin())
async def toggle_global_logging_option(callback_query: CallbackQuery):
    """Переключение главных тумблеров логирования."""
    key = callback_query.data.removeprefix("toggle_glog_")
    cfg = await load_global_logging_config()

    if key in cfg:
        cfg[key] = not cfg[key]
        await save_global_logging_config(cfg)

    try:
        await callback_query.message.edit_reply_markup(reply_markup=get_logging_main_kb(cfg))
    except TelegramBadRequest:
        pass
    await callback_query.answer("Настройка обновлена")


# --- НАСТРОЙКА ID КАНАЛА/ЧАТА ДЛЯ ЛОГОВ TELEGRAM ---

@router.callback_query(F.data == "settings_log_chat_prompt", IsAdmin())
async def prompt_log_chat_id(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(LoggingManageStates.waiting_for_log_chat_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить (отправлять Админу)", callback_data="reset_log_chat_id")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="settings_logging")],
        ]
    )
    await callback_query.message.edit_text(
        "🎯 <b>Настройка получателя логов в Telegram</b>\n\n"
        "Отправьте <b>ID чата, группы или канала</b> (например: <code>-100123456789</code> или <code>123456789</code>).\n\n"
        "<i>Если указан канал/группа, убедитесь, что бот добавлен туда администратором!</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.message(LoggingManageStates.waiting_for_log_chat_id, IsAdmin())
async def process_log_chat_id(message: Message, state: FSMContext):
    raw_val = (message.text or "").strip()
    try:
        chat_id = int(raw_val)
    except ValueError:
        return await message.answer("⚠️ Некорректный ID. Отправьте числовой ID (например: -100123456789).")

    cfg = await load_global_logging_config()
    cfg["telegram_log_chat_id"] = chat_id
    await save_global_logging_config(cfg)
    await state.clear()

    await message.answer(
        f"✅ ID для отправки логов установлен: <code>{chat_id}</code>",
        reply_markup=get_logging_main_kb(cfg),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "reset_log_chat_id", IsAdmin())
async def reset_log_chat_id_handler(callback_query: CallbackQuery, state: FSMContext):
    cfg = await load_global_logging_config()
    cfg["telegram_log_chat_id"] = None
    await save_global_logging_config(cfg)
    await state.clear()

    await callback_query.message.edit_text(
        "✅ Получатель сброшен. Логи будут отправляться <b>Админу</b>.",
        reply_markup=get_logging_main_kb(cfg),
        parse_mode="HTML",
    )
    await callback_query.answer()


# --- НАСТРОЙКА ПЕРИОДИЧНОСТИ ОЧИСТКИ ФАЙЛА ЛОГОВ ---

@router.callback_query(F.data == "settings_log_cleanup_menu", IsAdmin())
async def show_cleanup_period_menu(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "📁 <b>Настройка автоочистки текстового файла логов</b> (<code>bot_events.log</code>):\n\n"
        "Выберите, через сколько дней удалять старые записи:",
        reply_markup=get_cleanup_period_kb(),
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data.startswith("set_clean_"), IsAdmin())
async def set_cleanup_period_option(callback_query: CallbackQuery):
    option = callback_query.data.removeprefix("set_clean_")
    cfg = await load_global_logging_config()

    if option == "delete_now":
        await cleanup_log_file("delete_now", force=True)
        await callback_query.answer("🔥 Файл логов полностью очищен и удален!", show_alert=True)
    else:
        cfg["file_cleanup_period"] = option
        await save_global_logging_config(cfg)
        await cleanup_log_file(option, force=True)
        await callback_query.answer("Период очистки сохранен!")

    await callback_query.message.edit_text(
        "📜 <b>Управление логированием системы</b>",
        reply_markup=get_logging_main_kb(cfg),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "settings_logging_events", IsAdmin())
async def show_logging_events_menu(callback_query: CallbackQuery):
    """Отображение меню детальных событий логирования."""
    cfg = await load_global_logging_config()
    text = "⚙️ <b>Детальная настройка событий логирования:</b>\n\nВыберите, какие события фиксировать:"
    await callback_query.message.edit_text(
        text,
        reply_markup=get_logging_events_kb(cfg),
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data.startswith("toggle_gevt_"), IsAdmin())
async def toggle_event_logging_option(callback_query: CallbackQuery):
    """Переключение конкретного типа события."""
    event_key = callback_query.data.removeprefix("toggle_gevt_")
    cfg = await load_global_logging_config()
    events = cfg.setdefault("events", {})

    if event_key in events:
        events[event_key] = not events[event_key]
    else:
        events[event_key] = False

    await save_global_logging_config(cfg)

    try:
        await callback_query.message.edit_reply_markup(reply_markup=get_logging_events_kb(cfg))
    except TelegramBadRequest:
        pass
    await callback_query.answer("Событие переключено")


# --- СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ ---

@router.callback_query(F.data == "stats", IsAdmin())
async def admin_stats_handler(callback_query: CallbackQuery):
    """Отображает статистику пользователей."""
    total_users = await Database.get_total_users()
    users_today = await Database.get_users_registered_today()
    users_week = await Database.get_users_registered_this_week()
    users_month = await Database.get_users_registered_this_month()

    stats_message = (
        "📊 <b>Статистика пользователей:</b>\n\n"
        f"👥 Всего в базе: <b>{total_users}</b>\n"
        f"📅 За сегодня: <b>+{users_today}</b>\n"
        f"🗓️ За 7 дней (неделя): <b>+{users_week}</b>\n"
        f"📈 За 30 дней (месяц): <b>+{users_month}</b>"
    )

    try:
        await callback_query.message.edit_text(
            stats_message,
            reply_markup=admin_kb.main_menu,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback_query.answer()


# --- КОНФИГУРАТОР И ЗАПУСК РАССЫЛКИ ---

@router.callback_query(F.data == "broadcast", IsAdmin())
async def admin_broadcast_menu_handler(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.edit_text(
        "📢 <b>Выберите целевую аудиторию для рассылки:</b>",
        reply_markup=admin_kb.broadcast_options_menu,
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data.in_({"broadcast_to_users", "broadcast_to_admins"}), IsAdmin())
async def start_broadcast_flow(callback_query: CallbackQuery, state: FSMContext):
    target = "users" if callback_query.data == "broadcast_to_users" else "admins"
    await state.update_data(
        target=target,
        text="",
        media_type=None,
        media_id=None,
        buttons=[],
    )
    await state.set_state(BroadcastStates.waiting_for_initial_content)

    audience_name = "ВСЕМ пользователям" if target == "users" else "ВСЕМ администраторам"
    await callback_query.message.edit_text(
        f"✍️ Отправьте сообщение для рассылки (<b>{audience_name}</b>).\n\n"
        "Вы можете отправить <b>текст, фото, видео, гифку, голосовое сообщение, аудио или документ</b>.",
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.message(BroadcastStates.waiting_for_initial_content, IsAdmin())
async def process_initial_broadcast_content(message: Message, state: FSMContext, bot: Bot):
    text = message.html_text or ""
    media_type = None
    media_id = None

    if message.photo:
        media_type = "photo"
        media_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        media_id = message.animation.file_id
    elif message.voice:
        media_type = "voice"
        media_id = message.voice.file_id
    elif message.audio:
        media_type = "audio"
        media_id = message.audio.file_id
    elif message.document:
        media_type = "document"
        media_id = message.document.file_id

    await state.update_data(
        text=text,
        media_type=media_type,
        media_id=media_id,
    )
    await state.set_state(BroadcastStates.preview_menu)

    await message.answer("🔎 <b>Предпросмотр сообщения для рассылки:</b>", parse_mode="HTML")
    await render_broadcast_preview(message.chat.id, state, bot)


# --- РЕДАКТИРОВАНИЕ КОНТЕНТА В ПРЕДПРОСМОТРЕ ---

@router.callback_query(F.data == "bc_edit_text", IsAdmin())
async def prompt_edit_text(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_for_text)
    await callback_query.message.answer(
        "✍️ Введите <b>новый текст</b> для рассылки (поддерживается HTML-разметка):\n\n"
        "<i>Для удаления текста отправьте точку (.)</i>",
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.message(BroadcastStates.waiting_for_text, IsAdmin())
async def process_edit_text(message: Message, state: FSMContext, bot: Bot):
    new_text = message.html_text or ""
    if new_text.strip() == ".":
        new_text = ""

    await state.update_data(text=new_text)
    await state.set_state(BroadcastStates.preview_menu)

    await message.answer("✅ Текст обновлен! <b>Обновленный предпросмотр:</b>", parse_mode="HTML")
    await render_broadcast_preview(message.chat.id, state, bot)


@router.callback_query(F.data == "bc_edit_media", IsAdmin())
async def prompt_edit_media(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_for_media)
    await callback_query.message.answer(
        "🖼 Отправьте <b>новое медиа</b> (фото, видео, GIF/анимацию, голосовое, аудио или документ):",
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.message(BroadcastStates.waiting_for_media, IsAdmin())
async def process_edit_media(message: Message, state: FSMContext, bot: Bot):
    media_type = None
    media_id = None

    if message.photo:
        media_type = "photo"
        media_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        media_id = message.animation.file_id
    elif message.voice:
        media_type = "voice"
        media_id = message.voice.file_id
    elif message.audio:
        media_type = "audio"
        media_id = message.audio.file_id
    elif message.document:
        media_type = "document"
        media_id = message.document.file_id
    else:
        return await message.answer("⚠️ Пожалуйста, пришлите медиафайл (фото, видео, гифку, голосовое или документ).")

    await state.update_data(media_type=media_type, media_id=media_id)
    await state.set_state(BroadcastStates.preview_menu)

    await message.answer("✅ Медиафайл прикреплен! <b>Обновленный предпросмотр:</b>", parse_mode="HTML")
    await render_broadcast_preview(message.chat.id, state, bot)


@router.callback_query(F.data == "bc_remove_media", IsAdmin())
async def remove_media_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(media_type=None, media_id=None)
    await callback_query.message.answer("🗑 Медиафайл удален! <b>Предпросмотр:</b>", parse_mode="HTML")
    await render_broadcast_preview(callback_query.message.chat.id, state, bot)
    await callback_query.answer()


@router.callback_query(F.data == "bc_edit_buttons", IsAdmin())
async def prompt_edit_buttons(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_for_buttons)
    await callback_query.message.answer(
        "🔘 Отправьте кнопки в формате:\n\n"
        "<code>Текст1 - ссылка1</code>\n"
        "<code>Текст2 - ссылка2</code>\n\n"
        "<i>Каждая кнопка с новой строки. Невалидные строки будут автоматически пропущены.</i>",
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.message(BroadcastStates.waiting_for_buttons, IsAdmin())
async def process_edit_buttons(message: Message, state: FSMContext, bot: Bot):
    parsed = parse_buttons_from_text(message.text or "")

    if not parsed:
        await message.answer(
            "⚠️ Не удалось распознать ни одной правильной кнопки.\n"
            "Убедитесь, что формат строки: <code>Текст - https://ссылка</code>"
        )
        return

    await state.update_data(buttons=parsed)
    await state.set_state(BroadcastStates.preview_menu)

    await message.answer(
        f"✅ Добавлено кнопок: <b>{len(parsed)}</b>. <b>Обновленный предпросмотр:</b>",
        parse_mode="HTML",
    )
    await render_broadcast_preview(message.chat.id, state, bot)


@router.callback_query(F.data == "bc_remove_buttons", IsAdmin())
async def remove_buttons_handler(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(buttons=[])
    await callback_query.message.answer("🗑 Все кнопки удалены! <b>Предпросмотр:</b>", parse_mode="HTML")
    await render_broadcast_preview(callback_query.message.chat.id, state, bot)
    await callback_query.answer()


# --- ЗАПУСК РАССЫЛКИ С ЗАЩИТОЙ ОТ FLOODWAIT ---

@router.callback_query(F.data == "bc_send_now", IsAdmin())
async def execute_broadcast(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    target = data.get("target", "users")
    text = data.get("text", "")
    media_type = data.get("media_type")
    media_id = data.get("media_id")
    buttons = data.get("buttons") or []

    if not text and not media_id:
        await callback_query.message.answer(
            "⚠️ Нельзя запустить пустую рассылку без текста и медиа!",
            reply_markup=admin_kb.main_menu,
        )
        return

    user_kb_markup = None
    if buttons:
        user_kb_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=b["text"], url=b["url"])] for b in buttons]
        )

    if target == "users":
        recipients = await Database.get_all_user_ids()
        target_name = "пользователям"
    else:
        recipients = await Database.get_all_admin_ids()
        target_name = "администраторам"

    await callback_query.message.answer(
        f"🚀 <b>Рассылка {target_name} запущена...</b>\nВсего получателей: <code>{len(recipients)}</code>",
        parse_mode="HTML",
    )

    sent_count = 0
    blocked_count = 0
    failed_count = 0

    caller_id = callback_query.from_user.id

    for uid in recipients:
        if uid == caller_id:
            continue

        while True:
            try:
                if media_type == "photo":
                    await bot.send_photo(uid, photo=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                elif media_type == "video":
                    await bot.send_video(uid, video=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                elif media_type == "animation":
                    await bot.send_animation(uid, animation=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                elif media_type == "voice":
                    await bot.send_voice(uid, voice=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                elif media_type == "audio":
                    await bot.send_audio(uid, audio=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                elif media_type == "document":
                    await bot.send_document(uid, document=media_id, caption=text, parse_mode="HTML", reply_markup=user_kb_markup)
                else:
                    await bot.send_message(uid, text, parse_mode="HTML", reply_markup=user_kb_markup)

                sent_count += 1
                break

            except TelegramRetryAfter as e:
                wait_time = e.retry_after + 1
                logger.warning(f"TelegramFloodWait: пауза {wait_time} секунд при рассылке.")
                await asyncio.sleep(wait_time)

            except TelegramAPIError as e:
                err_str = str(e).lower()
                if "bot was blocked by the user" in err_str or "user is deactivated" in err_str or "chat not found" in err_str:
                    blocked_count += 1
                else:
                    failed_count += 1
                    logger.warning("Ошибка рассылки пользователю %s: %s", uid, e)
                break

            except Exception as e:
                failed_count += 1
                logger.warning("Неизвестная ошибка рассылки пользователю %s: %s", uid, e)
                break

        await asyncio.sleep(0.03)

    result_text = (
        f"✅ <b>Рассылка {target_name} завершена!</b>\n\n"
        f"📤 Успешно доставлено: <b>{sent_count}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked_count}</b>\n"
        f"❌ Ошибок отправки: <b>{failed_count}</b>"
    )

    await callback_query.message.answer(result_text, parse_mode="HTML", reply_markup=admin_kb.main_menu)
    await callback_query.answer()


# --- НАВИГАЦИЯ ---

@router.callback_query(F.data == "admin_panel", IsAdmin())
async def back_to_admin_main_menu(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback_query.message.edit_text(
            "👑 <b>Панель администратора</b>",
            reply_markup=admin_kb.main_menu,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback_query.answer()
