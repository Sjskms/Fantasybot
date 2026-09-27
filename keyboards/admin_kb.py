from aiogram.types import WebAppInfo, InlineKeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton



web = '127.0.0.1'


main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="⚙️ Системные лимиты", callback_data="admin_limits_menu")],
        [InlineKeyboardButton(text="🌐 Открыть Веб-Админку", web_app=WebAppInfo(url=f"http://{web}:8080"))],
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