#states/admin_states.py
from aiogram.fsm.state import State, StatesGroup


class BroadcastStates(StatesGroup):
    """Состояния FSM конструктора и предпросмотра рассылки."""
    waiting_for_initial_content = State()
    preview_menu = State()
    waiting_for_text = State()
    waiting_for_media = State()
    waiting_for_buttons = State()


class AdminManageStates(StatesGroup):
    """Состояния FSM управления администраторами."""
    waiting_for_add_admin_id = State()
    waiting_for_remove_admin_id = State()


class LoggingManageStates(StatesGroup):
    """Состояния FSM настройки чата системных логов."""
    waiting_for_log_chat_id = State()
