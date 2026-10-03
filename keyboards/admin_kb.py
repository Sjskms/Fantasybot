from aiogram.types import WebAppInfo, InlineKeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton





main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Настроить доступ", callback_data="settings_access")],
        [InlineKeyboardButton(text="📜 Настроить логирование", callback_data="settings_logging")],
        [InlineKeyboardButton(text="⚙️ Лимиты", callback_data="admin_limits_menu")],
        [InlineKeyboardButton(text="🌟 Платежная система", callback_data="payment_settings")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")]
    ]
)

# НОВАЯ КЛАВИАТУРА ДЛЯ ВЫБОРА ТИПА РАССЫЛКИ
broadcast_options_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Всем пользователям", callback_data="broadcast_to_users")],
        [InlineKeyboardButton(text="Всем админам", callback_data="broadcast_to_admins")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_main_menu")] # Кнопка для возврата
    ]
)