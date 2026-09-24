# handlers/session/state.py
from aiogram.fsm.state import State, StatesGroup

class UserSessionLoggingStates(StatesGroup):
    waiting_for_session_log_chat_id = State()
    
class TextTransformStates(StatesGroup):
    waiting_for_custom_text = State()
    waiting_for_replace_word = State()
    waiting_for_replace_link = State()

class FilterLimitsStates(StatesGroup):
    waiting_for_limit_value = State()

CONTENT_TYPES = {
    "photos": "Фото",
    "videos": "Видео",
    "text": "Текст",
    "documents": "Файлы",
    "music": "Музыка",
    "voices": "Голосовые",
    "video_notes": "Кружки",
}

FILTER_UNITS = {
    "text": "символах (длина текста)",
    "videos": "секундах (длительность)",
    "video_notes": "секундах (длительность кружочка)",
    "voices": "секундах (длительность)",
    "music": "секундах (длительность)",
    "photos": "байтах (размер файла)",
    "documents": "байтах (размер файла)",
}

mode_names = {
    "keep": "Только замена слов/ссылок",
    "append": "Добавление в конец",
    "prepend": "Добавление в начало",
    "replace": "Полная замена текста",
}