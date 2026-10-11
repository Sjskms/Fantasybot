# handlers/admin_access.py
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from keyboards import admin_kb
from my_filters.admin_filter import IsAdmin
from services.access_service import (
    ACCESS_MODES,
    FEATURE_NAMES,
    load_access_config,
    set_feature_access_mode,
)

router = Router()


@router.callback_query(F.data == "settings_access", IsAdmin())
async def show_access_menu(callback: CallbackQuery):
    """Отображает меню настройки доступа к четырем функциям."""
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    text = (
        "🔐 <b>Настройка доступа</b>\n\n"
        "🟢 — функция доступна по выбранному правилу, 🔴 — отключена.\n"
        "Нажмите ⚙️, чтобы выбрать, кому доступна функция."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=admin_kb.get_access_main_kb(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("acc_toggle_"), IsAdmin())
async def toggle_feature(callback: CallbackQuery):
    """Переключает функцию между текущим режимом доступа и полным отключением."""
    feature_key = callback.data.removeprefix("acc_toggle_")
    if feature_key not in FEATURE_NAMES:
        try:
            return await callback.answer("Неизвестная функция", show_alert=True)
        except TelegramBadRequest:
            return

    current = load_access_config().get(feature_key, "all")
    new_mode = "off" if current != "off" else "all"
    set_feature_access_mode(feature_key, new_mode)

    try:
        await callback.message.edit_reply_markup(reply_markup=admin_kb.get_access_main_kb())
    except TelegramBadRequest:
        pass

    try:
        status_name = "отключена 🔴" if new_mode == "off" else "включена 🟢"
        await callback.answer(f"Функция {status_name}")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("acc_config_"), IsAdmin())
async def open_feature_access_settings(callback: CallbackQuery):
    """Открывает варианты доступа для выбранной функции."""
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    feature_key = callback.data.removeprefix("acc_config_")
    if feature_key not in FEATURE_NAMES:
        return

    config = load_access_config()
    current_mode = config.get(feature_key, "all")
    title = FEATURE_NAMES[feature_key]
    mode_title = ACCESS_MODES.get(current_mode, current_mode)

    try:
        await callback.message.edit_text(
            f"🔐 <b>{title}</b>\n\nТекущий доступ: <b>{mode_title}</b>\n\nВыберите режим:",
            reply_markup=admin_kb.get_feature_access_mode_kb(feature_key),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("acc_set_"), IsAdmin())
async def set_feature_access(callback: CallbackQuery):
    """Сохраняет выбранную аудиторию функции в configuration/access.json."""
    raw = callback.data.removeprefix("acc_set_")
    feature_key = next((key for key in FEATURE_NAMES if raw.startswith(f"{key}_")), None)
    if not feature_key:
        try:
            return await callback.answer("Неизвестная функция", show_alert=True)
        except TelegramBadRequest:
            return

    mode = raw.removeprefix(f"{feature_key}_")
    if mode not in ACCESS_MODES:
        try:
            return await callback.answer("Неизвестный режим доступа", show_alert=True)
        except TelegramBadRequest:
            return

    set_feature_access_mode(feature_key, mode)

    try:
        await callback.answer(f"Сохранено: {ACCESS_MODES[mode]}")
    except TelegramBadRequest:
        pass

    try:
        await callback.message.edit_text(
            "🔐 <b>Настройка доступа</b>\n\n"
            "🟢 — функция доступна по выбранному правилу, 🔴 — отключена.\n"
            "Нажмите ⚙️, чтобы изменить аудиторию.",
            reply_markup=admin_kb.get_access_main_kb(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass