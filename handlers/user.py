# handlers/user.py
import html
import datetime
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from database import Database
from services.logging_service import log_event
from keyboards.user_kb import (
    get_main_menu_keyboard,
    get_profile_keyboard,
)

router = Router()


async def check_is_admin(user_id: int) -> bool:
    """Вспомогательная функция для быстрой и надежной проверки прав админа"""
    return await Database.is_admin(user_id)


@router.message(Command("start"))
async def start_handler(message: types.Message):
    try:
        user_id = message.from_user.id
        user_full_name = html.escape(message.from_user.full_name or "Пользователь")
        user_username = message.from_user.username
        registration_date = datetime.datetime.now().isoformat()

        # Проверяем, новый ли это пользователь
        if not await Database.user_exists(user_id):
            await Database.add_user(user_id, user_full_name, user_username, registration_date)
            
            # Логируем регистрацию нового пользователя с передачей user_id для блока админа
            username_str = f"@{user_username}" if user_username else "не задан"
            await log_event(
                "new_user",
                f"👤 <b>Новый пользователь!</b>\n"
                f"├ Имя: <b>{user_full_name}</b>\n"
                f"├ ID: <code>{user_id}</code>\n"
                f"└ Username: {username_str}",
                user_id=user_id
            )

        user_sessions = await Database.get_user_sessions(user_id)
        session_count = len(user_sessions) if user_sessions else 0

        welcome_text = (
            f"👋 <b>Привет, {user_full_name}!</b>\n\n"
            f"Добро пожаловать в систему автоматической пересылки и постинга контента!\n\n"
            f"📊 <b>Статус вашего аккаунта:</b>\n"
            f"└ Активных сессий: <code>{session_count}</code>\n\n"
            f"Выберите необходимый раздел в меню ниже 👇"
        )

        await message.answer(
            welcome_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка в команде /start: {e}", user_id=message.from_user.id)


@router.callback_query(F.data == "main_menu")
async def main_menu_callback_handler(callback_query: CallbackQuery):
    """Возврат в Главное меню."""
    try:
        user_id = callback_query.from_user.id
        user_full_name = html.escape(callback_query.from_user.full_name or "Пользователь")
        
        is_admin = await check_is_admin(user_id)
        
        # ИСПРАВЛЕНИЕ: Аналогично исправлен подсчет сессий через Database.get_user_sessions
        user_sessions = await Database.get_user_sessions(user_id)
        session_count = len(user_sessions) if user_sessions else 0

        menu_text = (
            f"🏠 <b>Главное меню</b>\n\n"
            f"👤 Пользователь: <b>{user_full_name}</b>\n"
            f"📱 Подключено сессий: <code>{session_count}</code>\n\n"
            f"Выберите раздел:"
        )

        try:
            await callback_query.message.edit_text(
                menu_text,
                reply_markup=get_main_menu_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
            
        await callback_query.answer()
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка в колбэке main_menu: {e}", user_id=callback_query.from_user.id)


@router.callback_query(F.data == "profile")
async def profile_callback_handler(callback_query: CallbackQuery):
    """Раздел Профиль."""
    try:
        user_id = callback_query.from_user.id
        user_data = await Database.get_user(user_id)

        if user_data:
            name = html.escape(user_data[1] if len(user_data) > 1 and user_data[1] else "Без имени")
            username = user_data[2] if len(user_data) > 2 and user_data[2] else None
            reg_date_str = user_data[3] if len(user_data) > 3 else None

            if reg_date_str:
                try:
                    reg_date_obj = datetime.datetime.fromisoformat(reg_date_str)
                    formatted_reg_date = reg_date_obj.strftime('%d.%m.%Y %H:%M')
                except Exception:
                    formatted_reg_date = reg_date_str
            else:
                formatted_reg_date = "Не указана"

            # Получаем сессии и форматируем список
            sessions = await Database.get_user_sessions_list(user_id)
            if sessions:
                session_lines = [f"  ▫️ <code>{row[0]}</code>" for row in sessions]
                sessions_text = "\n".join(session_lines)
            else:
                sessions_text = "  <i>Сессий пока нет</i>"

            profile_text = (
                f"👤 <b>Ваш Профиль</b>\n\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"📛 <b>Имя:</b> {name}\n"
                f"🌐 <b>Username:</b> @{username if username else 'не задан'}\n"
                f"📅 <b>Регистрация:</b> {formatted_reg_date}\n\n"
                f"📱 <b>Подключенные сессии:</b>\n{sessions_text}"
            )
        else:
            profile_text = "❌ Не удалось найти данные профиля. Нажмите /start."

        try:
            await callback_query.message.edit_text(
                profile_text,
                reply_markup=get_profile_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass

        await callback_query.answer()
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка в колбэке profile: {e}", user_id=callback_query.from_user.id)


@router.callback_query(F.data == "help_instruction")
async def help_instruction_handler(callback_query: CallbackQuery):
    """Раздел с актуальной инструкцией."""
    try:
        help_text = (
            "📖 <b>Инструкция по использованию бота:</b>\n\n"
            "1️⃣ <b>Подключение сессии:</b>\n"
            "• Зайдите в меню «Управление сессиями» ➡️ «Добавить сессию».\n"
            "• Введите номер телефона аккаунта.\n"
            "• Введите одноразовый код из приложения Telegram через кнопки.\n"
            "• Введите облачный пароль (2FA), если он у вас включен.\n\n"
            "2️⃣ <b>Настройка каналов:</b>\n"
            "• Выберите каналы-источники для <b>Экспорта</b> (откуда брать посты).\n"
            "• Выберите целевой канал для <b>Постинга</b> (куда отправлять).\n\n"
            "3️⃣ <b>Фильтры контента и текста:</b>\n"
            "• Настройте типы медиа (фото, видео, документы, голосовые).\n"
            "• Установите мин/макс значения (длина текста, длительность видео/аудио).\n"
            "• Настройте автозамену слов, ссылок или подписей в HTML-формате.\n\n"
            "4️⃣ <b>Запуск пересылки:</b>\n"
            "• Нажмите <b>«▶️ Включить пересылку»</b> — бот начнет автопостинг в реальном времени."
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛡 О безопасности данных", callback_data="security_info")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
        ])

        try:
            await callback_query.message.edit_text(help_text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await callback_query.answer()
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка в колбэке help_instruction: {e}", user_id=callback_query.from_user.id)


@router.callback_query(F.data == "security_info")
async def security_info_handler(callback_query: CallbackQuery):
    """Раздел о безопасности хранения сессий."""
    try:
        security_text = (
            "🛡 <b>Безопасность и защита ваших данных</b>\n\n"
            "Мы серьезно относимся к приватности ваших аккаунтов Telegram:\n\n"
            "🔑 <b>Криптографическое шифрование:</b>\n"
            "Все сессии перед сохранением в базу данных шифруются по стандарту <b>Fernet (AES-128 + HMAC)</b>.\n\n"
            "🔐 <b>Изолированное хранение ключей:</b>\n"
            "Ключ шифрования хранится отдельно от базы данных в изолированном файле конфигурации окружения (<code>.env</code>). Без этого ключа данные сессий невозможно прочитать.\n\n"
            "⚡️ <b>Безопасность в оперативной памяти:</b>\n"
            "Юзербот запускается в режиме <code>in_memory</code>. Сессионные файлы не сохраняются на диске сервера, исключая утечку данных.\n\n"
            "🚫 <b>Ограничение прав:</b>\n"
            "Юзербот выполняет строго заданные функции (пересылка сообщений из выбранных вами каналов) и не совершает никаких сторонних действий."
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Перейти к инструкции", callback_data="help_instruction")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
        ])

        try:
            await callback_query.message.edit_text(security_text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await callback_query.answer()
    except Exception as e:
        await log_event("bot_error", f"❌ Ошибка в колбэке security_info: {e}", user_id=callback_query.from_user.id)
