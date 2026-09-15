from aiogram import F, Router,types
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, CallbackQuery, Message
from pyrogram import Client
from config import API_ID, API_HASH
from database import Database
from aiogram.fsm.context import FSMContext
from states.session_states import SessionAdd, SessionStates
import logging
from html import escape
from pyrogram.errors import (
    AuthBytesInvalid,
    FloodWait,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    PasswordHashInvalid,
    SessionPasswordNeeded,
)
from keyboards.session_kb import (
    cancel_kb,
    skip_password_kb,
    get_session_settings_keyboard,
)

from services.Additional_Feature import (
    active_forwarder_tasks,
)


logger = logging.getLogger(__name__)
router = Router()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


@router.callback_query(F.data == "add_new_session")
async def add_new_session_start(callback: types.CallbackQuery, state: FSMContext):
    if not API_ID or not API_HASH:
        await callback.message.edit_text(
            "Извините, для добавления сессий боту необходимы настроенные API_ID и API_HASH.",
            reply_markup=profile_menu_kb 
        )
        return

    await state.set_state(SessionAdd.waiting_for_phone)
    await callback.message.edit_text(
        "📞 Отправьте мне номер телефона:\n"
        "Пример: +79123456789",
        reply_markup=cancel_kb
    )    
    

@router.callback_query(F.data == "cans") 
async def cancel_add_session_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_sessions = await Database.get_user_sessions(callback.from_user.id)
    markup = get_session_settings_keyboard(user_sessions)
    await callback.message.edit_text("🚫 Добавление сессии отменено.", reply_markup=markup)
    
   

@router.message(SessionAdd.waiting_for_phone)
async def process_phone_number(message: types.Message, state: FSMContext):
    phone_number = message.text.strip()
    # Простая валидация номера телефона
    if not phone_number.startswith('+') or not phone_number[1:].isdigit() or len(phone_number) < 10:
        await message.answer("❌ Пожалуйста, введите корректный номер телефона, начинающийся с `+`.", reply_markup=cancel_kb)
        return

    await message.answer("⏳ Отправляю запрос на код подтверждения...", reply_markup=types.ReplyKeyboardRemove())

    try:
        # Создаем временный Pyrogram Client для авторизации
        # Используем in_memory=True, чтобы не создавать .session файл
        temp_client = Client(
            name=str(message.from_user.id), # Имя сессии для pyrogram
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True # Не сохранять сессию на диск
        )

        await temp_client.connect()
        sent_code = await temp_client.send_code(phone_number)

        await state.update_data(temp_client=temp_client, phone_number=phone_number, phone_code_hash=sent_code.phone_code_hash)
        await state.set_state(SessionAdd.waiting_for_code)
        logger.info(f"Sent code to {phone_number} for user {message.from_user.id}")
       # await state.set_state(SessionAdd.waiting_for_code)
        
        await message.answer("🔢 Введите 5-значный код, который пришел в Telegram:\n\nКод: ` `", reply_markup=get_code_keyboard(),parse_mode="Markdown"  )
        await state.set_state(SessionAdd.waiting_for_code)

    except FloodWait as e:
        logger.warning(f"FloodWait for user {message.from_user.id}: {e}")
        await message.answer(
            f"🚫 Слишком много попыток. Пожалуйста, попробуйте снова через {e.value} секунд.",
            reply_markup=cancel_kb
        )
        await state.clear()
        # Возвращаем пользователя в меню настроек сессий
        user_sessions = await Database.get_user_sessions(message.from_user.id)
        markup = get_session_settings_keyboard(user_sessions)
        await message.answer("⚙️ Ваши Telegram сессии:", reply_markup=markup)
    except PhoneNumberInvalid:
        logger.warning(f"PhoneNumberInvalid for user {message.from_user.id}")
        await message.answer("❌ Неверный номер телефона. Пожалуйста, попробуйте еще раз.", reply_markup=cancel_kb)
    except Exception as e:
        logger.error(f"Error sending code for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("Произошла неизвестная ошибка при отправке кода. Пожалуйста, попробуйте еще раз.", reply_markup=cancel_kb)
        await temp_client.disconnect() # Отключаем клиент в случае ошибки
        await state.clear()
        user_sessions = await Database.get_user_sessions(message.from_user.id)
        markup = get_session_settings_keyboard(user_sessions)
        await message.answer("⚙️ Ваши Telegram сессии:", reply_markup=markup)



def get_code_keyboard(code_buffer: str = "") -> InlineKeyboardMarkup:
        # Генерируем кнопки 1-9
        buttons = []
        for i in range(1, 10, 3):
            buttons.append([
                InlineKeyboardButton(text=str(j), callback_data=f"code_{j}")
                for j in range(i, i + 3)
            ])
        # Добавляем нижний ряд
        buttons.append([
            InlineKeyboardButton(text="⬅️", callback_data="code_back"),
            InlineKeyboardButton(text="0", callback_data="code_0"),
            InlineKeyboardButton(text="✅", callback_data="code_submit")
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    

@router.callback_query(F.data.startswith("code_"))
async def process_code_buttons(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    temp_client = data.get("temp_client")
    
    if not temp_client:
        await callback.message.edit_text("Сессия истекла или произошла ошибка. Начните сначала.")
        await state.clear()
        return
    
    # Получаем текущий буфер кода и клиент
    code_buffer = data.get("code_buffer", "")
    temp_client = data.get("temp_client") 
    action = callback.data.split("_")[1]

    # Логика кнопок
    if action == "back":
        code_buffer = code_buffer[:-1]
    elif action == "submit":
        if not code_buffer:
            return await callback.answer("Введите код!")
        # Здесь логика sign_in
        await finalize_sign_in(callback, state, code_buffer)
        return
    else:
        # Добавляем цифру (например, ограничим длину до 5-6 знаков)
        if len(code_buffer) < 6:
            code_buffer += action
        else:
            return await callback.answer("Код слишком длинный!")

    # Обновляем состояние
    await state.update_data(code_buffer=code_buffer)
    
    # Обновляем сообщение с новым кодом
    display_text = f"🔢 Введите 5-значный код, который пришел в Telegram:\n\nВведите код: {code_buffer}"
    await callback.message.edit_text(display_text, reply_markup=get_code_keyboard(code_buffer))
    await callback.answer()
    
        
                

@router.callback_query(SessionAdd.waiting_for_code, F.data.startswith("code_"))
async def process_code_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    code_buffer = data.get("code_buffer", "")
    action = callback.data.split("_")[1]

    # 1. Логика кнопок
    if action == "back":
        code_buffer = code_buffer[:-1]
    elif action == "submit":
        if len(code_buffer) != 5:
            return await callback.answer("❌ Код должен быть 5-значным!", show_alert=True)
        # Если всё ок, переходим к логике sign_in (см. ниже)
        return await finalize_sign_in(callback, state, code_buffer)
    else:
        # Добавляем цифру
        if len(code_buffer) < 5:
            code_buffer += action
        else:
            return await callback.answer("Код уже введен!", show_alert=True)

    # 2. Обновляем состояние и сообщение
    await state.update_data(code_buffer=code_buffer)
    
    # Визуализация ввода (скрываем код или показываем)
    display_text = f"🔢 Введите 5-значный код, который пришел в Telegram:\n\nКод: `{code_buffer}`"
    
    try:
        await callback.message.edit_text(display_text, reply_markup=get_code_keyboard(code_buffer), parse_mode="Markdown")
    except Exception:
        pass # Игнорируем ошибку, если текст не изменился
    await callback.answer()
    
    
    
    
    
    
async def finalize_sign_in(callback: types.CallbackQuery, state: FSMContext, code: str):
    await callback.message.edit_text("⏳ Проверяю код...", reply_markup=None)
    
    data = await state.get_data()
    temp_client: Client = data.get("temp_client")
    phone_number = data.get("phone_number")
    phone_code_hash = data.get("phone_code_hash")
    clean_phone = phone_number.replace("+", "")
    session_name = f"Сессия_{clean_phone}"

    try:
        await temp_client.sign_in(phone_number, phone_code_hash, code)
        
        session_string = await temp_client.export_session_string()
        next_session_num = await Database.get_next_session_number(callback.from_user.id)
        await Database.add_session(callback.from_user.id,session_name, session_string)
        
        await temp_client.disconnect()
        await state.clear()
        
        await callback.message.answer(f"🎉 Сессия успешно добавлена!")
        # ... (код возврата в меню) ...

    except SessionPasswordNeeded:
        await state.set_state(SessionAdd.waiting_for_password)
        await callback.message.answer("🔒 Введите пароль 2FA:", reply_markup=skip_password_kb)
    
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await callback.message.answer("❌ Неверный код. Попробуйте еще раз.")
        await state.update_data(code_buffer="") # Сброс
        # Повторно вызываем меню ввода кода
        await callback.message.answer("🔢 Введите 5-значный код, который пришел в Telegram:\n\nКод: ", reply_markup=get_code_keyboard())
    
    except Exception as e:
        logger.error(f"Error: {e}")
        await callback.message.answer("Произошла ошибка.")
        await state.clear()
        await temp_client.disconnect()
        
            
                    
    
    


@router.message(SessionAdd.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    temp_client: Client = data.get("temp_client")
    phone_number = data.get("phone_number")

    if not temp_client:
        await message.answer("Произошла ошибка при получении данных. Пожалуйста, начните заново.", reply_markup=cancel_kb)
        await state.clear()
        return

    await message.answer("⏳ Проверяю пароль...", reply_markup=types.ReplyKeyboardRemove())

    try:
        await temp_client.check_password(password)

        session_string = await temp_client.export_session_string()

        clean_phone = phone_number.replace("+", "")
        session_name = f"Сессия_{clean_phone}"

        await Database.add_session(message.from_user.id, session_name, session_string)

        await temp_client.disconnect()
        await state.clear()
        
        await message.answer(f"🎉 Ваша сессия **'{escape(session_name)}'** успешно добавлена!", parse_mode="HTML")
    
    
        user_sessions = await Database.get_user_sessions(message.from_user.id)
        markup = get_session_settings_keyboard(user_sessions)
        await message.answer("⚙️ Ваши Telegram сессии:", reply_markup=markup)

    except (PasswordHashInvalid, AuthBytesInvalid):
        logger.warning(f"Invalid 2FA password for user {message.from_user.id}")
        await message.answer("❌ Неверный облачный пароль. Пожалуйста, попробуйте еще раз.", reply_markup=skip_password_kb)
    except Exception as e:
        logger.error(f"Error checking 2FA password for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer("Произошла неизвестная ошибка при проверке пароля. Пожалуйста, попробуйте еще раз.", reply_markup=skip_password_kb)
        await state.clear()
        
        if temp_client.is_connected:
        	await temp_client.disconnect()
        
        user_sessions = await Database.get_user_sessions(message.from_user.id)
        markup = get_session_settings_keyboard(user_sessions)
        await message.answer("⚙️ Ваши Telegram сессии:", reply_markup=markup)

@router.callback_query(F.data == "skip_2fa_password", SessionAdd.waiting_for_password)
async def skip_2fa_password(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Если у вас нет облачного пароля, значит, 2FA не включена. Отменяю процесс.", show_alert=True)
    await state.clear()

    user_sessions = await Database.get_user_sessions(callback.from_user.id)
    markup = get_session_settings_keyboard(user_sessions)
    await callback.message.answer("🚫 Добавление сессии отменено.", reply_markup=types.ReplyKeyboardRemove())
    await callback.message.edit_text("⚙️ Ваши Telegram сессии:", reply_markup=markup)





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
    

