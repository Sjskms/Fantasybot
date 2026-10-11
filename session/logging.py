# handlers/session/logging.py
from html import escape
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import Database
from keyboards.session_kb import get_logging_settings_keyboard
from services.forwarder.supervisor import update_live_config
from .state import UserSessionLoggingStates

router = Router()

from html import escape
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from services.access_service import check_user_access
from database import Database
# Подставьте правильный путь к вашей клавиатуре настроек логирования:
from keyboards.logging_kb import get_logging_settings_keyboard


@router.callback_query(F.data.startswith("session_logging_"))
async def show_logging_settings(callback: CallbackQuery):
    """Отображает настройки логирования сессии с проверкой прав доступа."""
    user_id = callback.from_user.id
    session_name = callback.data.removeprefix("session_logging_")

    # 1. Проверяем права доступа по ключу "logs" из configuration/access.json
    has_access, reason = await check_user_access(user_id, "logs")

    if not has_access:
        if reason == "off":
            return await callback.answer("⚠️ Логирование временно отключено администратором.", show_alert=True)

        if reason == "admin":
            return await callback.answer("🔒 Настройки логирования доступны только администраторам.", show_alert=True)

        if reason == "premium":
            promo_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Оформить Premium", callback_data="payment")],
                [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"session_config_{session_name}")]
            ])
            return await callback.message.edit_text(
                "⭐ <b>Расширенное логирование доступно в тарифе Premium!</b>\n\n"
                "• Детальная история пересылок и публикаций в реальном времени\n"
                "• Отслеживание ошибок публикации и причин фильтрации постов\n"
                "• Уведомления о сбоях и статусах отправки сообщений\n\n"
                "Оформите подписку, чтобы получить полный контроль над логами сессий:",
                reply_markup=promo_kb,
                parse_mode="HTML"
            )

    # 2. Если доступ есть — открываем панель настроек логирования
    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    log_config = session_configs.get(
        "logging", 
        {"enabled": True, "log_success": True, "log_filtered": False, "log_errors": True}
    )

    text = f"📜 <b>Настройки логирования для сессии:</b> <code>{escape(session_name)}</code>"
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

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    log_config = session_configs.setdefault("logging", {"enabled": True, "log_success": True, "log_filtered": False, "log_errors": True})

    if log_type == "chatprompt":
        await state.update_data(target_session_name=session_name)
        await state.set_state(UserSessionLoggingStates.waiting_for_session_log_chat_id)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить", callback_data=f"toggle_log_{session_name}_reset_chat")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"session_logging_{session_name}")]
        ])
        await callback.message.edit_text("✍️ Отправьте ID чата/канала для логов:", reply_markup=kb, parse_mode="HTML")
        await callback.answer()
        return
    elif log_type == "reset_chat":
        log_config["log_chat_id"] = None
    elif log_type == "main":
        log_config["enabled"] = not log_config.get("enabled", True)
    elif log_type == "success":
        log_config["log_success"] = not log_config.get("log_success", True)
    elif log_type == "filtered":
        log_config["log_filtered"] = not log_config.get("log_filtered", False)
    elif log_type == "errors":
        log_config["log_errors"] = not log_config.get("log_errors", True)

    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)

    keyboard = get_logging_settings_keyboard(session_name, log_config)
    try:
        await callback.message.edit_text(f"📜 <b>Настройки логирования:</b> <code>{escape(session_name)}</code>", reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer("Обновлено")

@router.message(UserSessionLoggingStates.waiting_for_session_log_chat_id)
async def process_user_session_log_chat_id(message: Message, state: FSMContext):
    if not message.text.lstrip('-').isdigit():
        return await message.answer("⚠️ Некорректный ID. Отправьте числовой ID.")

    fsm_data = await state.get_data()
    session_name = fsm_data.get("target_session_name")
    user_id = message.from_user.id
    await state.clear()

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    session_configs.setdefault("logging", {})["log_chat_id"] = int(message.text)
    
    await Database.update_session_configs(user_id, session_name, session_configs)
    await update_live_config(user_id, session_name)
    await message.answer(f"✅ Получатель логов для <code>{escape(session_name)}</code> изменен!", parse_mode="HTML")