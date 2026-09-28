# handlers/session/lifecycle.py
import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import API_ID, API_HASH
from database import Database
from keyboards.session_kb import (
    get_forwarding_menu_keyboard,
    get_session_management_keyboard,
    get_session_settings_keyboard,
)
from .channels import (
    encode_value,
    decode_value,
)
from services.forwarder.state import active_forwarder_tasks
from services.forwarder.supervisor import (
    restart_session_gracefully,
    run_forwarder_forever,
    stop_forwarder,
)
from .common import build_forwarding_status_text
from .channels import encode_value

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("session_config2_"))
async def config_menu(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("session_config2_")
    await state.update_data(current_session=session_name)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Каналы для экспорта", callback_data=f"list_export_{session_name}")],
            [InlineKeyboardButton(text="📤 Каналы для постинга", callback_data=f"list_post_{session_name}")],
            [InlineKeyboardButton(text="⚙️ Общие фильтры для всех", callback_data=f"g_filt_{session_name}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"select_session_{session_name}")],
        ]
    )
    
    try:
        await callback.message.edit_text(
            f"⚙️ Выберите категорию каналов для сессии: <code>{escape(session_name)}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
    await callback.answer()
    
    
@router.callback_query(F.data == "session_settings")
async def process_session_settings(callback: CallbackQuery):
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

    text = build_forwarding_status_text(session_name, session_configs, enable_posting)
    keyboard = get_session_management_keyboard(session_name=session_name, enable_posting=enable_posting)

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
    await callback.answer()

@router.callback_query(F.data.startswith("session_config_"))
async def session_config_handler(callback: CallbackQuery):
    session_name = callback.data.removeprefix("session_config_")
    user_id = callback.from_user.id
    enable_posting_status = await Database.get_session_posting_status(user_id, session_name)
    keyboard = get_session_management_keyboard(session_name, enable_posting_status)

    try:
        await callback.message.edit_text(
            f"⚙️ Управление сессией: <code>{escape(session_name)}</code>",
            reply_markup=keyboard, parse_mode="HTML",
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
        post_count = await Database.get_session_channels_count(user_id, session_name, "post")
        export_count = await Database.get_session_channels_count(user_id, session_name, "export")
        if post_count == 0 or export_count == 0:
            return await callback.answer("⚠️ Настройте хотя бы один канал для постинга и экспорта.", show_alert=True)

        await Database.update_session_posting_status(user_id, session_name, True)
        old_task = active_forwarder_tasks.pop(task_key, None)
        if old_task:
            old_task.cancel()
        await stop_forwarder(user_id, session_name)

        active_forwarder_tasks[task_key] = asyncio.create_task(
            run_forwarder_forever(user_id, session_name, API_ID, API_HASH),
            name=f"forwarder:{user_id}:{session_name}"
        )
    else:
        await Database.update_session_posting_status(user_id, session_name, False)
        task = active_forwarder_tasks.pop(task_key, None)
        if task:
            task.cancel()
        await stop_forwarder(user_id, session_name)

    actual_status = await Database.get_session_posting_status(user_id, session_name)
    session_configs = await Database.get_session_configs(user_id, session_name)
    text = build_forwarding_status_text(session_name, session_configs, actual_status)
    keyboard = get_session_management_keyboard(session_name=session_name, enable_posting=actual_status)

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

    restarted = await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)
    session_configs = await Database.get_session_configs(user_id, session_name)
    status_text = build_forwarding_status_text(session_name, session_configs, restarted)
    keyboard = get_forwarding_menu_keyboard(session_name, restarted)

    await callback.message.edit_text(status_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("apply_all_changes_"))
async def apply_all_changes_handler(callback: CallbackQuery):
    session_name = callback.data.removeprefix("apply_all_changes_")
    user_id = callback.from_user.id
    restarted = await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

    text = f"✅ <b>Изменения применены</b>\n\nСессия <code>{session_name}</code> перезапущена." if restarted else f"✅ <b>Изменения сохранены</b>."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ К настройкам каналов", callback_data=f"session_config2_{session_name}")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")