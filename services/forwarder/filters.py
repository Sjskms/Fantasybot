# services/forwarder/filters.py
from pyrogram.types import Message


def is_message_allowed(message: Message, export_filters: dict) -> bool:
    """Проверяет соответствие сообщения заданным фильтрам (размер, длительность, тип)."""
    if not isinstance(export_filters, dict):
        return False

    def get_rules(key: str):
        value = export_filters.get(key, {})
        if isinstance(value, dict):
            return (
                bool(value.get("enabled", False)),
                int(value.get("min", 0)),
                int(value.get("max", 999999999)),
            )
        if isinstance(value, bool):
            return value, 0, 999999999
        return False, 0, 999999999

    if message.text and not message.media:
        enabled, min_val, max_val = get_rules("text")
        return enabled and min_val <= len(message.text) <= max_val

    # Видео и GIF/Анимации
    if message.video or message.animation:
        enabled, min_val, max_val = get_rules("videos")
        dur = 0
        if message.video:
            dur = message.video.duration or 0
        elif message.animation:
            dur = message.animation.duration or 0
        return enabled and min_val <= dur <= max_val

    if message.photo:
        enabled, min_val, max_val = get_rules("photos")
        if max_val == 999999:
            max_val = 2000000000
        return enabled and min_val <= (message.photo.file_size or 0) <= max_val

    if message.video_note:
        enabled, min_val, max_val = get_rules("video_notes")
        return enabled and min_val <= (message.video_note.duration or 0) <= max_val

    if message.voice:
        enabled, min_val, max_val = get_rules("voices")
        return enabled and min_val <= (message.voice.duration or 0) <= max_val

    if message.audio:
        enabled, min_val, max_val = get_rules("music")
        return enabled and min_val <= (message.audio.duration or 0) <= max_val

    if message.document:
        enabled, min_val, max_val = get_rules("documents")
        if max_val == 999999:
            max_val = 2000000000
        return enabled and min_val <= (message.document.file_size or 0) <= max_val

    return False
