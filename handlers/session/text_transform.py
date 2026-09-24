# handlers/session/text_transform.py
import html
import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import Database
from keyboards.session_kb import get_text_transform_keyboard
from .state import TextTransformStates, mode_names

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data.startswith("text_transform_"))
async def show_text_transform_menu(callback: CallbackQuery, state: FSMContext = None, session_name: str = None):
    if state:
        await state.set_state(None)
    user_id = callback.from_user.id
    if not session_name:
        session_name = callback.data.removeprefix("text_transform_")

    try:
        session_configs = await Database.get_session_configs(user_id, session_name) or {}
        tt_cfg = session_configs.get("text_transform", {})
        current_mode = tt_cfg.get("mode", "keep")
        words_count = len(tt_cfg.get("replace_words", []))
        links_count = len(tt_cfg.get("replace_links", []))

        raw_custom = tt_cfg.get("custom_text", "").strip()
        custom_preview = f"<code>{html.escape(raw_custom[:200])}</code>" if raw_custom else "<i>(не задан)</i>"

        text = (
            f"✏️ <b>Настройки текста и ссылок:</b> <code>{session_name}</code>\n\n"
            f"🔹 <b>Режим вставки:</b> {mode_names.get(current_mode, 'Не выбран')}\n"
            f"🔹 <b>Правил замены слов:</b> {words_count}\n"
            f"🔹 <b>Правил замены ссылок:</b> {links_count}\n\n"
            f"📝 <b>Текущий кастомный текст:</b>\n{custom_preview}"
        )
        keyboard = get_text_transform_keyboard(session_name, tt_cfg)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Ошибка в show_text_transform_menu: {e}")
    finally:
        await callback.answer()

@router.callback_query(F.data.startswith("tt_mode_"))
async def change_transform_mode(callback: CallbackQuery):
    raw = callback.data.removeprefix("tt_mode_")
    session_name, new_mode = raw.rsplit("_", 1)
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name) or {}
    configs.setdefault("text_transform", {})["mode"] = new_mode
    await Database.update_session_configs(user_id, session_name, configs)
    await show_text_transform_menu(callback, session_name=session_name)

@router.callback_query(F.data.startswith("tt_clear_"))
async def clear_transform_rules(callback: CallbackQuery):
    session_name = callback.data.removeprefix("tt_clear_")
    user_id = callback.from_user.id

    configs = await Database.get_session_configs(user_id, session_name) or {}
    configs["text_transform"] = {"mode": "keep", "custom_text": "", "replace_words": [], "replace_links": []}
    await Database.update_session_configs(user_id, session_name, configs)
    await callback.answer("Все правила очищены!", show_alert=True)
    await show_text_transform_menu(callback, session_name=session_name)

@router.callback_query(F.data.startswith("tt_settext_"))
async def prompt_custom_text(callback: CallbackQuery, state: FSMContext):
    session_name = callback.data.removeprefix("tt_settext_")
    await state.update_data(current_session=session_name)
    await state.set_state(TextTransformStates.waiting_for_custom_text)
    await callback.message.answer("✍️ Отправьте текст для описания:", parse_mode="HTML")
    await callback.answer()

@router.message(TextTransformStates.waiting_for_custom_text)
async def process_custom_text(message: Message, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("current_session")
    configs = await Database.get_session_configs(message.from_user.id, session_name) or {}
    configs.setdefault("text_transform", {})["custom_text"] = message.text or message.caption or ""
    await Database.update_session_configs(message.from_user.id, session_name, configs)
    await state.set_state(None)
    await message.answer("✅ Кастомный текст успешно сохранен!")

@router.callback_query(F.data.startswith("tt_addword_"))
async def prompt_replace_word(callback: CallbackQuery, state: FSMContext):
    await state.update_data(current_session=callback.data.removeprefix("tt_addword_"))
    await state.set_state(TextTransformStates.waiting_for_replace_word)
    await callback.message.answer("🔤 Введите замену слов через знак '=' (пример: <code>скидка = распродажа</code>)", parse_mode="HTML")
    await callback.answer()

@router.message(TextTransformStates.waiting_for_replace_word)
async def process_replace_word(message: Message, state: FSMContext):
    if "=" not in message.text:
        return await message.answer("⚠️ Формат неверный! Используйте знак '='.")
    old_w, new_w = map(str.strip, message.text.split("=", 1))
    data = await state.get_data()
    configs = await Database.get_session_configs(message.from_user.id, data.get("current_session")) or {}
    configs.setdefault("text_transform", {}).setdefault("replace_words", []).append({"from": old_w, "to": new_w})
    await Database.update_session_configs(message.from_user.id, data.get("current_session"), configs)
    await state.set_state(None)
    await message.answer(f"✅ Правило добавлено: <code>{html.escape(old_w)}</code> ➡️ <code>{html.escape(new_w)}</code>", parse_mode="HTML")

@router.callback_query(F.data.startswith("tt_addlink_"))
async def prompt_replace_link(callback: CallbackQuery, state: FSMContext):
    await state.update_data(current_session=callback.data.removeprefix("tt_addlink_"))
    await state.set_state(TextTransformStates.waiting_for_replace_link)
    await callback.message.answer("🔗 Введите замену ссылок через знак '='", parse_mode="HTML")
    await callback.answer()

@router.message(TextTransformStates.waiting_for_replace_link)
async def process_replace_link(message: Message, state: FSMContext):
    if "=" not in message.text:
        return await message.answer("⚠️ Формат неверный! Используйте знак '='.")
    old_l, new_l = map(str.strip, message.text.split("=", 1))
    data = await state.get_data()
    configs = await Database.get_session_configs(message.from_user.id, data.get("current_session")) or {}
    configs.setdefault("text_transform", {}).setdefault("replace_links", []).append({"from": old_l, "to": new_l})
    await Database.update_session_configs(message.from_user.id, data.get("current_session"), configs)
    await state.set_state(None)
    await message.answer("✅ Ссылка успешно заменена!")