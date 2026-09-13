from aiogram.fsm.state import State, StatesGroup

class SessionStates(StatesGroup):
    waiting_for_channel = State() # Ждем, пока пользователь выберет канал из списка
    
    
    
class SessionAdd(StatesGroup):
    """
    Состояния для процесса добавления Pyrogram сессии.
    """
    waiting_for_phone = State() # Ожидание номера телефона
    waiting_for_code = State()  # Ожидание кода из Telegram
    waiting_for_password = State() # Ожидание 2FA пароля (если требуется)