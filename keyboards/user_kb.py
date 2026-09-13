#keyboards/user_kb.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Генерирует актуальное Главное меню."""
    buttons = [
        [InlineKeyboardButton(text="📱 Управление сессиями", callback_data="session_settings")],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
            InlineKeyboardButton(text="📖 Инструкция", callback_data="help_instruction")
        ],
        [InlineKeyboardButton(text="🛡 Безопасность и Защита", callback_data="security_info")] # 👈 НОВАЯ КНОПКА
    ]
    
    if is_admin:
        buttons.append([InlineKeyboardButton(text="👑 Панель Администратора", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура раздела Профиль."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Управление сессиями", callback_data="session_settings")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
    ])

