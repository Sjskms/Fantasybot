# handlers/session/config_sync.py
import json
import logging
import os
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message, InlineKeyboardButton, InlineKeyboardMarkup

from database import Database
from services.forwarder.supervisor import update_live_config, restart_session_gracefully
from services.forwarder.state import loaded_configs
from config import API_ID, API_HASH
from .channels import decode_value, encode_value

router = Router()
logger = logging.getLogger(__name__)


class ConfigSyncStates(StatesGroup):
    waiting_for_config_file = State()


def get_config_sync_keyboard(session_name: str) -> InlineKeyboardMarkup:
    """Клавиатура для меню выгрузки/загрузки конфигурации сессии."""
    encoded_name = encode_value(session_name)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Выгрузить config.json", callback_data=f"export_cfg:{encoded_name}")],
            [InlineKeyboardButton(text="📥 Загрузить config.json", callback_data=f"import_cfg:{encoded_name}")],
            [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"select_session_{session_name}")],
        ]
    )


# --- 1. ОТКРЫТИЕ МЕНЮ СИНХРОНИЗАЦИИ ---
@router.callback_query(F.data.startswith("config_sync_"))
async def open_config_sync_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    session_name = callback.data.removeprefix("config_sync_")
    
    text = (
        f"⚙️ <b>Резервное копирование конфигурации</b>\n"
        f"Сессия: <code>{escape(session_name)}</code>\n\n"
        "Вы можете выгрузить текущие настройки каналов и фильтров в виде файла <code>config.json</code> "
        "или загрузить готовый файл конфигурации обратно в систему:"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=get_config_sync_keyboard(session_name), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# --- 2. ВЫГРУЗКА (ЭКСПОРТ) CONFIG.JSON ---
@router.callback_query(F.data.startswith("export_cfg:"))
async def export_session_config(callback: CallbackQuery):
    _, encoded_name = callback.data.split(":", 1)
    session_name = decode_value(encoded_name)
    user_id = callback.from_user.id

    try:
        # Принудительно подтягиваем актуальные данные в кэш памяти из базы данных
        await update_live_config(user_id, session_name)
        
        # Получаем конфиг напрямую из базы (или через свежий кэш)
        configs = await Database.get_session_configs(user_id, session_name)
        if not configs:
            configs = loaded_configs.get((user_id, session_name), {})

        file_name = f"config_{session_name}.json"
        file_content = json.dumps(configs, ensure_ascii=False, indent=2)
        
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(file_content)

        document = FSInputFile(file_name)
        file_size_kb = round(os.path.getsize(file_name) / 1024, 2)

        await callback.message.answer_document(
            document=document,
            caption=f"📄 <b>Конфигурация сессии:</b> <code>{escape(session_name)}</code>\n📊 Размер: <code>{file_size_kb} KB</code>",
            parse_mode="HTML"
        )
        await callback.answer("Файл конфигурации успешно выгружен!")
        
        if os.path.exists(file_name):
            os.remove(file_name)

    except Exception as e:
        logger.exception("Ошибка экспорта конфига сессии: %s", e)
        await callback.answer(f"❌ Ошибка экспорта: {e}", show_alert=True)


# --- 3. ЗАПРОС ФАЙЛА У ПОЛЬЗОВАТЕЛЯ (ИМПОРТ) ---
@router.callback_query(F.data.startswith("import_cfg:"))
async def prompt_import_session_config(callback: CallbackQuery, state: FSMContext):
    _, encoded_name = callback.data.split(":", 1)
    session_name = decode_value(encoded_name)

    await state.update_data(import_session_name=session_name)
    await state.set_state(ConfigSyncStates.waiting_for_config_file)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"session_config_sync_{session_name}")]
        ]
    )

    await callback.message.edit_text(
        f"📥 <b>Импорт конфигурации для сессии:</b> <code>{escape(session_name)}</code>\n\n"
        "Отправьте файл <b>config.json</b> в этот чат (или вставьте содержимое текстом):",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


# --- 4. ОБРАБОТКА ПОЛУЧЕННОГО ФАЙЛА ИЛИ ТЕКСТА ---
@router.message(ConfigSyncStates.waiting_for_config_file)
async def process_session_config_upload(message: Message, state: FSMContext):
    data = await state.get_data()
    session_name = data.get("import_session_name")
    user_id = message.from_user.id

    raw_text = None

    if message.document:
        try:
            file = await message.bot.get_file(message.document.file_id)
            file_io = await message.bot.download_file(file.file_path)
            raw_text = file_io.read().decode("utf-8")
        except Exception as e:
            return await message.answer(f"⚠️ Не удалось прочитать прикрепленный файл: {e}")
    elif message.text:
        raw_text = message.text

    if not raw_text:
        return await message.answer("⚠️ Пожалуйста, отправьте файл <code>config.json</code> или текст конфигурации.", parse_mode="HTML")

    try:
        parsed_config = json.loads(raw_text)
        if not isinstance(parsed_config, dict):
            raise ValueError("Содержимое JSON должно быть словарем (dict).")

        # 1. Сохраняем в базу данных
        await Database.update_session_configs(user_id, session_name, parsed_config)

        # 2. Обновляем память и перезапускаем воркхук сессии
        await update_live_config(user_id, session_name)
        await restart_session_gracefully(user_id, session_name, API_ID, API_HASH)

        await state.clear()

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ К настройкам сессии", callback_data=f"select_session_{session_name}")],
            ]
        )

        file_size_kb = round(len(raw_text.encode('utf-8')) / 1024, 2)

        await message.answer(
            f"✅ <b>Конфигурация успешно импортирована и применена!</b>\n"
            f"Сессия: <code>{escape(session_name)}</code>\n\n"
            f"📊 Размер файла: <b>{file_size_kb} KB</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )

    except json.JSONDecodeError as je:
        await message.answer(f"❌ <b>Ошибка формата JSON:</b>\n<code>{escape(str(je))}</code>\n\nПроверьте синтаксис файла и попробуйте снова.", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка импорта конфигурации: %s", e)
        await message.answer(f"❌ Ошибка сохранения в базу: {e}")