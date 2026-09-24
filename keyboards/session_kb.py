# keyboards/session_kb.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Any, Dict, List, Set
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.forwarder.engine import (
   calculate_session_stats,
)
from services.forwarder.state import (
    has_session_unapplied_changes,
)

from database import Database
db = Database() 

# Кнопка отмены для FSM
cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="⚙️ Отмена", callback_data="cans")]
])

def get_session_settings_keyboard(sessions: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    buttons = []
    if sessions:
        for session_name, _ in sessions:
            buttons.append([InlineKeyboardButton(text=session_name, callback_data=f"select_session_{session_name}")])

    buttons.append([InlineKeyboardButton(text="➕ Добавить новую сессию", callback_data="add_new_session")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
    
skip_password_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Пропустить (у меня нет 2FA)", callback_data="skip_2fa_password")]
])

def get_session_stats_keyboard(session_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Подробная статистика", callback_data=f"session_stats_detailed_{session_name}")],
        [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"session_config_{session_name}")]
    ])
    
    
    

    
    

def get_forwarding_menu_keyboard(
    session_name: str,
    enabled: bool,
) -> InlineKeyboardMarkup:
    if enabled:
        toggle_text = "⏹ Выключить пересылку"
    else:
        toggle_text = "▶️ Включить пересылку"

    buttons = [
        [
            InlineKeyboardButton(
                text=toggle_text,
                callback_data=f"toggle_posting_{session_name}",
            )
        ],
    ]

    if enabled:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔄 Перезапустить пересылку",
                    callback_data=f"restart_posting_{session_name}",
                )
            ]
        )

    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки каналов",
                    callback_data=f"session_config2_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад к сессии",
                    callback_data=f"select_session_{session_name}",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    
    
def get_text_transform_keyboard(session_name: str, transform_config: dict) -> InlineKeyboardMarkup:
    mode = transform_config.get("mode", "keep")
    
    m_keep = "🔘" if mode == "keep" else "⚪️"
    m_append = "🔘" if mode == "append" else "⚪️"
    m_prepend = "🔘" if mode == "prepend" else "⚪️"
    m_replace = "🔘" if mode == "replace" else "⚪️"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m_append} Добавлять в конец", callback_data=f"tt_mode_{session_name}_append")],
        [InlineKeyboardButton(text=f"{m_prepend} Добавлять в начало", callback_data=f"tt_mode_{session_name}_prepend")],
        [InlineKeyboardButton(text=f"{m_replace} Заменять текст целиком", callback_data=f"tt_mode_{session_name}_replace")],
        [InlineKeyboardButton(text=f"{m_keep} Только замены (без добавления)", callback_data=f"tt_mode_{session_name}_keep")],
        
        [InlineKeyboardButton(text="📝 Задать кастомный текст (HTML)", callback_data=f"tt_settext_{session_name}")],
        [InlineKeyboardButton(text="🔄 Добавить замену слова", callback_data=f"tt_addword_{session_name}")],
        [InlineKeyboardButton(text="🔗 Добавить замену ссылки", callback_data=f"tt_addlink_{session_name}")],
        [InlineKeyboardButton(text="🗑 Очистить все замены", callback_data=f"tt_clear_{session_name}")],
        [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data=f"session_config_{session_name}")]
    ])
    
    
def get_session_management_keyboard(
    session_name: str,
    enable_posting: bool,
) -> InlineKeyboardMarkup:
    toggle_button_text = (
        "⏹ Выключить пересылку"
        if enable_posting
        else "▶️ Запустить пересылку"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки каналов",
                    callback_data=f"session_config2_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Текст и ссылки (HTML)",
                    callback_data=f"text_transform_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=toggle_button_text,
                    callback_data=f"toggle_posting_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Настройки логов",
                    callback_data=f"session_logging_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                  text="📊 Статистика",
                   callback_data=f"session_stats_{session_name}"),
             ],
            [
                InlineKeyboardButton(
                    text="❌ Удалить сессию",
                    callback_data=f"delete_session_{session_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад к списку сессий",
                    callback_data="session_settings",
                )
            ],
        ]
    )
    
    

def _build_filter_limits_keyboard(chat_id: int, filter_key: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить МИН", callback_data=f"set_limit_{chat_id}_{filter_key}_min")
    builder.button(text="✏️ Изменить МАКС", callback_data=f"set_limit_{chat_id}_{filter_key}_max")
    builder.button(text="⬅️ Назад к каналу", callback_data=f"config_channel_{chat_id}")
    builder.adjust(2, 1)
    return builder.as_markup()              
    
    
    
CONTENT_TYPES = {
    "photos": "Фото",
    "videos": "Видео",
    "text": "Текст",
    "documents": "Файлы",
    "music": "Музыка",
    "voices": "Голосовые",
    "video_notes": "Кружки"
}
    
def _build_channel_settings_keyboard(chat_id: int, session_name: str, mode: str, channel_mode_config: dict):
    builder = InlineKeyboardBuilder()
    filters = channel_mode_config.get("filters", {})

    for filter_key, title in CONTENT_TYPES.items():
        filter_val = filters.get(filter_key, {})
        
        if isinstance(filter_val, dict):
            is_enabled = filter_val.get("enabled", True)
            min_val = filter_val.get("min", 0)
            max_val = filter_val.get("max", 999999)
        else:
            is_enabled = bool(filter_val)
            min_val, max_val = 0, 999999

        status_icon = "✅" if is_enabled else "❌"
        
        # Кнопка 1: Вкл/Выкл
        builder.button(
            text=f"{status_icon} {title}",
            callback_data=f"toggle_filter_{chat_id}_{filter_key}"
        )
        # Кнопка 2: Настройки Min/Max
        builder.button(
            text=f"⚙️ {min_val} - {max_val}",
            callback_data=f"edit_limits_{chat_id}_{filter_key}"
        )

    builder.adjust(2)

    # Всегда обычная кнопка возврата к списку каналов
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"list_{mode}_{session_name}"
        )
    )
    return builder.as_markup()
    
    
    





async def _build_channels_keyboard(
    cached_channels: list,
    selected_channels_ids: set,
    session_name: str,
    mode: str,
    user_id: int,
    session_configs: dict
):
    builder = InlineKeyboardBuilder()
    row_sizes = []

    for ch in cached_channels:
        ch_id = ch["id"]
        is_selected = ch_id in selected_channels_ids
        title = ch.get("title", f"Канал {ch_id}")

        if mode == "export":
            if is_selected:
                # ВЫБРАН: [ Название ] [ ❌ ] [ ⚙️ ]
                builder.button(text=title, callback_data=f"toggle_select_{ch_id}")
                builder.button(text="❌", callback_data=f"toggle_select_{ch_id}")
                builder.button(text="⚙️", callback_data=f"config_channel_{ch_id}")
                row_sizes.append(3)
            else:
                # НЕ ВЫБРАН: [ Название ] [ ✅ Выбрать ]
                builder.button(text=title, callback_data=f"toggle_select_{ch_id}")
                builder.button(text="✅ Выбрать", callback_data=f"toggle_select_{ch_id}")
                row_sizes.append(2)

        else:
            # Режим POST: всегда 2 кнопки без настроек
            status_text = "❌" if is_selected else "✅ Выбрать"
            builder.button(text=title, callback_data=f"toggle_select_{ch_id}")
            builder.button(text=status_text, callback_data=f"toggle_select_{ch_id}")
            row_sizes.append(2)

    # Применяем динамическую разметку строк
    if row_sizes:
        builder.adjust(*row_sizes)

    # Кнопка сохранения и перезапуска при наличии изменений
    is_posting_active = await Database.get_session_posting_status(user_id, session_name)
    has_changes = is_posting_active and has_session_unapplied_changes(user_id, session_name, session_configs)

    if has_changes:
        builder.row(
            InlineKeyboardButton(
                text="💾 Сохранить изменения и перезапустить",
                callback_data=f"apply_all_changes_{session_name}"
            )
        )

    # Кнопка возврата в меню сессии
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"session_config2_{session_name}"
        )
    )
    return builder.as_markup()
    
    

def get_logging_settings_keyboard(session_name: str, log_config: dict) -> InlineKeyboardMarkup:
    # Иконки тумблеров
    main_icon = "✅ Включены" if log_config.get("enabled", True) else "❌ Выключены"
    success_icon = "✅" if log_config.get("log_success", True) else "❌"
    filtered_icon = "✅" if log_config.get("log_filtered", False) else "❌"
    errors_icon = "✅" if log_config.get("log_errors", True) else "❌"

    # Куда слать
    chat_id = log_config.get("log_chat_id")
    chat_display = f"ID: {chat_id}" if chat_id else "ЛС бота"

    keyboard = [
        [InlineKeyboardButton(text=f"Логи сессии: {main_icon}", callback_data=f"toggle_log_{session_name}_main")],
        [InlineKeyboardButton(text=f"Успешная перессылка: {success_icon}", callback_data=f"toggle_log_{session_name}_success")],
        [InlineKeyboardButton(text=f"Отфильтровано: {filtered_icon}", callback_data=f"toggle_log_{session_name}_filtered")],
        [InlineKeyboardButton(text=f"Ошибки отправки: {errors_icon}", callback_data=f"toggle_log_{session_name}_errors")],
        [InlineKeyboardButton(text=f"🎯 Получатель: {chat_display}", callback_data=f"toggle_log_{session_name}_chatprompt")],
        [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"session_config_{session_name}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    
    
# Добавьте это в файл keyboards/session_kb.py

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_session_stats_keyboard(session_name: str) -> InlineKeyboardMarkup:
    """Клавиатура главного экрана статистики сессии."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Подробная статистика", callback_data=f"session_stats_detailed_{session_name}")],
        [InlineKeyboardButton(text="◀️ Назад к сессии", callback_data=f"manage_session_{session_name}")]
    ])


def get_session_detailed_stats_keyboard(
    session_name: str, 
    current_page: int = 1, 
    total_pages: int = 1
) -> InlineKeyboardMarkup:
    """Клавиатура подробной статистики с поддержкой пагинации страниц."""
    keyboard = []

    # Кнопки пагинации (если страниц больше одной)
    if total_pages > 1:
        nav_buttons = []
        if current_page > 1:
            nav_buttons.append(
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"statspage_{session_name}_{current_page - 1}")
            )
        
        nav_buttons.append(
            InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="noop")
        )
        
        if current_page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(text="Вперед ➡️", callback_data=f"statspage_{session_name}_{current_page + 1}")
            )
        keyboard.append(nav_buttons)

    # Кнопка возврата к общей статистике сессии
    keyboard.append([
        InlineKeyboardButton(text="◀️ Назад к статистике", callback_data=f"session_stats_{session_name}")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)    