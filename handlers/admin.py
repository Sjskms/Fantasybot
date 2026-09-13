# handlers/admin.py
from aiogram import Router, types, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup # Для использования состояний (FSM)
from keyboards import admin_kb 
from my_filters.admin_filter import IsAdmin
from database import Database
from aiogram.types import CallbackQuery, Message # Убедимся, что Message импортирован
from aiogram.exceptions import TelegramAPIError # Для обработки ошибок при рассылке
import datetime # Для работы с датами
import asyncio # Для async.sleep (задержка при рассылке)

router = Router()

# Определяем состояния для административной рассылки
class AdminStates(StatesGroup):
    waiting_for_broadcast_message_users = State() # Ожидание сообщения для рассылки пользователям
    waiting_for_broadcast_message_admins = State() # Ожидание сообщения для рассылки админам

@router.callback_query(F.data == "settings", IsAdmin())
async def admin_settings_handler(callback_query: CallbackQuery):
    """
    Обработчик кнопки 'Настройки' для админа.
    Пока просто сообщает, что здесь ничего нет.
    """
    await callback_query.message.edit_text(
        "Вы в разделе настроек администратора. Здесь пока ничего нет.",
        reply_markup=admin_kb.main_menu # Возвращаем на основную админ-клавиатуру
    )
    await callback_query.answer() # Закрываем уведомление о нажатии на кнопку

@router.callback_query(F.data == "stats", IsAdmin())
async def admin_stats_handler(callback_query: CallbackQuery):
    """
    Обработчик кнопки 'Статистика' для админа.
    Отправляет статистику по количеству пользователей.
    """
    total_users = await Database.get_total_users()
    users_today = await Database.get_users_registered_today()
    users_week = await Database.get_users_registered_this_week()
    users_month = await Database.get_users_registered_this_month()

    stats_message = (
        f"📊 *Статистика пользователей:*\n\n"
        f"👥 Всего пользователей: *{total_users}*\n"
        f"📅 За сегодня: *{users_today}*\n"
        f"🗓️ За текущую неделю: *{users_week}*\n"
        f"📈 За текущий месяц: *{users_month}*"
    )
    await callback_query.message.edit_text(
        stats_message,
        reply_markup=admin_kb.main_menu,
        parse_mode="Markdown" # Используем Markdown для форматирования
    )
    await callback_query.answer() # Закрываем уведомление о нажатии на кнопку

@router.callback_query(F.data == "broadcast", IsAdmin())
async def admin_broadcast_menu_handler(callback_query: CallbackQuery):
    """
    Обработчик кнопки 'Рассылка' для админа.
    Предлагает выбрать тип рассылки (всем пользователям или всем админам).
    """
    await callback_query.message.edit_text(
        "Выберите тип рассылки:",
        reply_markup=admin_kb.broadcast_options_menu # Отправляем клавиатуру с опциями рассылки
    )
    await callback_query.answer()

@router.callback_query(F.data == "broadcast_to_users", IsAdmin())
async def start_broadcast_to_users(callback_query: CallbackQuery, state: FSMContext):
    """
    Начинает процесс рассылки всем пользователям.
    Переводит бота в состояние ожидания сообщения.
    """
    await callback_query.message.edit_text(
        "Отправьте сообщение, которое вы хотите разослать *ВСЕМ пользователям*.\n\n"
        "Вы можете отправить текст, фото, видео или другие медиафайлы."
    )
    await state.set_state(AdminStates.waiting_for_broadcast_message_users) # Устанавливаем состояние
    await callback_query.answer()

@router.callback_query(F.data == "broadcast_to_admins", IsAdmin())
async def start_broadcast_to_admins(callback_query: CallbackQuery, state: FSMContext):
    """
    Начинает процесс рассылки всем админам.
    Переводит бота в состояние ожидания сообщения.
    """
    await callback_query.message.edit_text(
        "Отправьте сообщение, которое вы хотите разослать *ВСЕМ админам*.\n\n"
        "Вы можете отправить текст, фото, видео или другие медиафайлы."
    )
    await state.set_state(AdminStates.waiting_for_broadcast_message_admins) # Устанавливаем состояние
    await callback_query.answer()

@router.message(AdminStates.waiting_for_broadcast_message_users, IsAdmin())
async def process_broadcast_to_users(message: Message, state: FSMContext, bot: Bot):
    """
    Обрабатывает сообщение, отправленное для рассылки пользователям.
    Пересылает его всем зарегистрированным пользователям.
    """
    await message.answer("Начинаю рассылку *пользователям*...", parse_mode="Markdown")
    user_ids = await Database.get_all_user_ids()

    sent_count = 0
    blocked_count = 0
    failed_count = 0

    for user_id in user_ids:
        # Пропускаем отправителя, чтобы не отправлять ему же рассылку, если он админ
        if user_id == message.from_user.id:
            continue
        try:
            # Пересылаем оригинальное сообщение (сохраняет форматирование и медиа)
            await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            sent_count += 1
        except TelegramAPIError as e:
            if "bot was blocked by the user" in str(e):
                blocked_count += 1
            else:
                failed_count += 1
                print(f"Failed to send to user {user_id}: {e}") # Логирование ошибок
        await asyncio.sleep(0.05) # Задержка для предотвращения лимитов Telegram

    await message.answer(
        f"Рассылка *всем пользователям* завершена!\n"
        f"✅ Успешно отправлено: *{sent_count}*\n"
        f"🚫 Заблокировали бота: *{blocked_count}*\n"
        f"❌ Ошибки отправки: *{failed_count}*",
        parse_mode="Markdown"
    )
    await state.clear() # Очищаем состояние после завершения рассылки
    await message.answer("Вы вернулись в главное меню администратора.", reply_markup=admin_kb.main_menu)


@router.message(AdminStates.waiting_for_broadcast_message_admins, IsAdmin())
async def process_broadcast_to_admins(message: Message, state: FSMContext, bot: Bot):
    """
    Обрабатывает сообщение, отправленное для рассылки админам.
    Пересылает его всем зарегистрированным администраторам.
    """
    await message.answer("Начинаю рассылку *администраторам*...", parse_mode="Markdown")
    admin_ids = await Database.get_all_admin_ids()

    sent_count = 0
    failed_count = 0

    for admin_id in admin_ids:
        # Пропускаем отправителя, чтобы он не получил свою же рассылку
        if admin_id == message.from_user.id:
            continue 
        try:
            # Пересылаем оригинальное сообщение
            await bot.copy_message(chat_id=admin_id, from_chat_id=message.chat.id, message_id=message.message_id)
            sent_count += 1
        except TelegramAPIError as e:
            failed_count += 1
            print(f"Failed to send to admin {admin_id}: {e}") # Логирование ошибок
        await asyncio.sleep(0.05) # Задержка для предотвращения лимитов Telegram

    await message.answer(
        f"Рассылка *всем админам* завершена!\n"
        f"✅ Успешно отправлено: *{sent_count}*\n"
        f"❌ Ошибки отправки: *{failed_count}*",
        parse_mode="Markdown"
    )
    await state.clear() # Очищаем состояние
    await message.answer("Вы вернулись в главное меню администратора.", reply_markup=admin_kb.main_menu)

@router.callback_query(F.data == "admin_main_menu", IsAdmin())
async def back_to_admin_main_menu(callback_query: CallbackQuery, state: FSMContext):
    """
    Обработчик кнопки 'Назад' в подменю админа.
    Возвращает в главное админ-меню и сбрасывает состояние.
    """
    await state.clear() # Очищаем состояние, если оно было активно
    await callback_query.message.edit_text(
        "Вы в главном меню администратора.",
        reply_markup=admin_kb.main_menu
    )
    await callback_query.answer()


@router.message(Command("admin1"))
async def make_me_admin(message: types.Message):
    user_id = message.from_user.id
    await Database.add_admin(user_id)
    await message.answer("Вы успешно добавлены в список администраторов.")

@router.message(Command("admin0"))
async def remove_me_from_admins(message: types.Message):
    user_id = message.from_user.id
    await Database.remove_admin(user_id)    
    await message.answer("Вы были удалены из списка администраторов.")
    
     