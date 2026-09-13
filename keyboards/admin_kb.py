from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")] # НОВАЯ КНОПКА
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