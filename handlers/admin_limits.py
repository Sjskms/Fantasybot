# handlers/admin_limits.py
import logging
from html import escape
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.config_service import read_full_config, update_limit_in_config

router = Router()
logger = logging.getLogger(__name__)


class AdminLimitEditStates(StatesGroup):
    waiting_for_new_value = State()


LIMIT_LABELS = {
    "max_sessions_per_user": ("Обычный: макс. сессий", "шт."),
    "max_export_channels_per_session": ("Обычный: экспорт каналов", "шт."),
    "max_post_channels_per_session": ("Обычный: постинг каналов", "шт."),
    "premium_max_sessions_per_user": ("⭐ Premium: макс. сессий", "шт."),
    "premium_max_export_channels_per_session": ("⭐ Premium: экспорт каналов", "шт."),
    "premium_max_post_channels_per_session": ("⭐ Premium: постинг каналов", "шт."),
}


def build_admin_limits_keyboard(config: dict) -> InlineKeyboardMarkup:
    """Генерирует инлайн-клавиатуру для редактирования лимитов."""
    rows = []

    # 1. Блок обычных пользователей
    rows.append([
        InlineKeyboardButton(
            text=f"📱 Сессии (Free): {config.get('max_sessions_per_user', 3)}",
            callback_data="adm_lim:edit:max_sessions_per_user",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=f"📤 Экспорт (Free): {config.get('max_export_channels_per_session', 10)}",
            callback_data="adm_lim:edit:max_export_channels_per_session",
        ),
        InlineKeyboardButton(
            text=f"📥 Постинг (Free): {config.get('max_post_channels_per_session', 5)}",
            callback_data="adm_lim:edit:max_post_channels_per_session",
        ),
    ])

    # 2. Блок Premium-пользователей
    rows.append([
        InlineKeyboardButton(
            text=f"⭐ Сессии (Prem): {config.get('premium_max_sessions_per_user', 10)}",
            callback_data="adm_lim:edit:premium_max_sessions_per_user",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=f"⭐ 📤 Экспорт: {config.get('premium_max_export_channels_per_session', 50)}",
            callback_data="adm_lim:edit:premium_max_export_channels_per_session",
        ),
        InlineKeyboardButton(
            text=f"⭐ 📥 Постинг: {config.get('premium_max_post_channels_per_session', 25)}",
            callback_data="adm_lim:edit:premium_max_post_channels_per_session",
        ),
    ])

    # 3. Кнопка возврата в админ-панель
    rows.append([
        InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_panel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_limits_text(config: dict) -> str:
    """Формирует наглядный текст с текущими лимитами системы."""
    return (
        "⚙️ <b>Управление системными лимитами</b>\n\n"
        "👤 <b>Обычные пользователи (Free):</b>\n"
        f"  ├ 📱 Максимум сессий: <b>{config.get('max_sessions_per_user', 3)}</b>\n"
        f"  ├ 📤 Каналов экспорта на сессию: <b>{config.get('max_export_channels_per_session', 10)}</b>\n"
        f"  └ 📥 Каналов постинга на сессию: <b>{config.get('max_post_channels_per_session', 5)}</b>\n\n"
        "⭐ <b>Премиум пользователи (Premium):</b>\n"
        f"  ├ 📱 Максимум сессий: <b>{config.get('premium_max_sessions_per_user', 10)}</b>\n"
        f"  ├ 📤 Каналов экспорта на сессию: <b>{config.get('premium_max_export_channels_per_session', 50)}</b>\n"
        f"  └ 📥 Каналов постинга на сессию: <b>{config.get('premium_max_post_channels_per_session', 25)}</b>\n\n"
        "<i>Нажмите на любую кнопку ниже, чтобы изменить значение в файле bot_logging_config.json:</i>"
    )


# --- ОТКРЫТИЕ МЕНЮ НАСТРОЙКИ ЛИМИТОВ ---
@router.callback_query(F.data == "admin_limits_menu")
async def open_admin_limits_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    config = read_full_config()
    text = build_admin_limits_text(config)
    keyboard = build_admin_limits_keyboard(config)

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- ЗАПРОС НОВОГО ЗНАЧЕНИЯ ---
@router.callback_query(F.data.startswith("adm_lim:edit:"))
async def prompt_limit_edit(callback: CallbackQuery, state: FSMContext):
    key = callback.data.removeprefix("adm_lim:edit:")
    if key not in LIMIT_LABELS:
        return await callback.answer("Неизвестный параметр", show_alert=True)

    label, _ = LIMIT_LABELS[key]
    config = read_full_config()
    current_val = config.get(key, 0)

    await state.update_data(editing_limit_key=key)
    await state.set_state(AdminLimitEditStates.waiting_for_new_value)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_limits_menu")]
    ])

    await callback.message.edit_text(
        f"✍️ <b>Изменение лимита:</b> {escape(label)}\n\n"
        f"Текущее значение: <code>{current_val}</code>\n\n"
        "Отправьте новое целое число в чат (например: <code>15</code>):",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


# --- СОХРАНЕНИЕ НОВОГО ЗНАЧЕНИЯ В JSON ---
@router.message(AdminLimitEditStates.waiting_for_new_value)
async def process_new_limit_value(message: Message, state: FSMContext):
    raw_text = (message.text or "").strip()
    if not raw_text.isdigit() or int(raw_text) < 0:
        return await message.answer(
            "⚠️ Пожалуйста, введите корректное положительное целое число (например: <code>5</code>, <code>20</code>).",
            parse_mode="HTML"
        )

    new_value = int(raw_text)
    data = await state.get_data()
    key = data.get("editing_limit_key")
    await state.clear()

    if not key or key not in LIMIT_LABELS:
        return await message.answer("⚠️ Сессия настройки истекла. Откройте панель лимитов заново.")

    label, _ = LIMIT_LABELS[key]
    success = update_limit_in_config(key, new_value)

    if success:
        config = read_full_config()
        text = (
            f"✅ <b>Лимит успешно обновлен и сохранен в bot_logging_config.json!</b>\n\n"
            f"Параметр: <b>{escape(label)}</b>\n"
            f"Новое значение: <code>{new_value}</code>\n\n"
        ) + build_admin_limits_text(config)

        keyboard = build_admin_limits_keyboard(config)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message.answer("❌ Ошибка при записи в файл конфигурации. Проверьте права доступа.")
