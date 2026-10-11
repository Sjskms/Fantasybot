from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.access_service import ACCESS_MODES, FEATURE_NAMES, load_access_config


main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="stats")],
        [InlineKeyboardButton(text="📜 Логирование системы", callback_data="settings_logging")],
        [InlineKeyboardButton(text="🔐 Доступ", callback_data="settings_access")],
    ]
)

broadcast_options_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="👥 Всем пользователям", callback_data="broadcast_to_users")],
        [InlineKeyboardButton(text="👑 Всем администраторам", callback_data="broadcast_to_admins")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")],
    ]
)

EVENT_TITLES = {
    "new_user": "👤 Новый пользователь",
    "session_added": "📥 Добавлена сессия",
    "session_removed": "🗑 Удалена сессия",
    "forward_success": "✅ Успешная пересылка",
    "forward_filtered": "ℹ️ Отфильтрованное сообщение",
    "forward_error": "⚠️ Ошибка отправки",
    "forwarding_enabled": "🟢 Пересылка включена",
    "forwarding_disabled": "🔴 Пересылка выключена",
    "bot_error": "❌ Ошибка в боте",
    "payment_success": "💰 Успешная оплата",
    "payment_pending": "⏳ Создан счет / отправлен чек",
    "payment_rejected": "❌ Отклоненный платеж",
    "payment_cryptobot": "🤖 Логировать CryptoBot",
    "payment_card": "💳 Логировать Карты РФ",
    "payment_only_unpaid": "⚠️ Логировать ТОЛЬКО незавершенные",
    "other": "📝 Другое логирование",
}


def get_logging_main_kb(cfg: dict) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру главных настроек логирования."""
    bot_log_icon = "✅" if cfg.get("bot_logging", True) else "❌"
    tg_log_icon = "✅" if cfg.get("telegram_logging", True) else "❌"
    con_log_icon = "✅" if cfg.get("console_logging", True) else "❌"
    file_log_icon = "✅" if cfg.get("file_logging", True) else "❌"
    chat_display = str(cfg.get("telegram_log_chat_id") or "Админу")

    period_map = {
        "1_day": "1 день",
        "3_days": "3 дня",
        "1_week": "1 неделя",
        "2_weeks": "2 недели",
        "1_month": "1 месяц",
        "never": "Не удалять",
    }
    period_str = period_map.get(cfg.get("file_cleanup_period", "1_week"), "1 неделя")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Логирование бота: {bot_log_icon}", callback_data="toggle_glog_bot_logging")],
            [InlineKeyboardButton(text=f"Логирование в Telegram: {tg_log_icon}", callback_data="toggle_glog_telegram_logging")],
            [InlineKeyboardButton(text=f"Логирование в консоль: {con_log_icon}", callback_data="toggle_glog_console_logging")],
            [InlineKeyboardButton(text=f"Логирование в файл: {file_log_icon}", callback_data="toggle_glog_file_logging")],
            [InlineKeyboardButton(text=f"🎯 Куда слать в TG: {chat_display}", callback_data="settings_log_chat_prompt")],
            [InlineKeyboardButton(text=f"📁 Очистка файла: {period_str}", callback_data="settings_log_cleanup_menu")],
            [InlineKeyboardButton(text="📥 Выгрузить файл логов", callback_data="download_log_file")],
            [InlineKeyboardButton(text="⚙️ Настроить события логирования", callback_data="settings_logging_events")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")],
        ]
    )


def get_cleanup_period_kb() -> InlineKeyboardMarkup:
    """Создает клавиатуру выбора периода очистки файла логов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 день", callback_data="set_clean_1_day"), InlineKeyboardButton(text="3 дня", callback_data="set_clean_3_days")],
            [InlineKeyboardButton(text="1 неделя", callback_data="set_clean_1_week"), InlineKeyboardButton(text="2 недели", callback_data="set_clean_2_weeks")],
            [InlineKeyboardButton(text="1 месяц", callback_data="set_clean_1_month"), InlineKeyboardButton(text="Не удалять", callback_data="set_clean_never")],
            [InlineKeyboardButton(text="🔥 Удалить прямо сейчас", callback_data="set_clean_delete_now")],
            [InlineKeyboardButton(text="◀️ Назад к логированию", callback_data="settings_logging")],
        ]
    )


def get_logging_events_kb(cfg: dict) -> InlineKeyboardMarkup:
    """Создает клавиатуру переключения типов логируемых событий."""
    events_cfg = cfg.get("events", {})
    rows = []
    for event_key, title in EVENT_TITLES.items():
        icon = "✅" if events_cfg.get(event_key, True) else "❌"
        rows.append([InlineKeyboardButton(text=f"{title}: {icon}", callback_data=f"toggle_gevt_{event_key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад к общим настройкам", callback_data="settings_logging")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_access_main_kb() -> InlineKeyboardMarkup:
    """Создает меню доступности функций и кнопки их настройки."""
    config = load_access_config()
    rows = []
    for feature_key, title in FEATURE_NAMES.items():
        mode = config.get(feature_key, "all")
        enabled = mode != "off"
        row = [InlineKeyboardButton(
            text=f"{title} {'🟢' if enabled else '🔴'}",
            callback_data=f"acc_toggle_{feature_key}",
        )]
        if enabled:
            row.append(InlineKeyboardButton(text="⚙️", callback_data=f"acc_config_{feature_key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Назад в админку", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_feature_access_mode_kb(feature_key: str) -> InlineKeyboardMarkup:
    """Создает меню выбора аудитории для выбранной функции."""
    config = load_access_config()
    current_mode = config.get(feature_key, "all")
    rows = []
    for mode, title in ACCESS_MODES.items():
        prefix = "✅ " if mode == current_mode else ""
        rows.append([InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"acc_set_{feature_key}_{mode}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад к доступу", callback_data="settings_access")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
