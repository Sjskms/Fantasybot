# handlers/session/common.py
from html import escape

def get_enabled_filter_names(filters: dict) -> list[str]:
    content_names = {
        "photos": "фото", "videos": "видео", "text": "текст",
        "documents": "файлы", "music": "музыка", "voices": "голосовые",
        "video_notes": "кружки",
    }
    result = []
    if isinstance(filters, dict):
        for key, title in content_names.items():
            value = filters.get(key, {})
            enabled = value.get("enabled", False) if isinstance(value, dict) else bool(value)
            if enabled:
                result.append(title)
    return result

def get_default_filters():
    return {
        "photos": {"enabled": True, "min": 0, "max": 2000000000},
        "videos": {"enabled": True, "min": 0, "max": 999999},
        "text": {"enabled": True, "min": 0, "max": 999999},
        "documents": {"enabled": True, "min": 0, "max": 2000000000},
        "music": {"enabled": True, "min": 0, "max": 999999},
        "voices": {"enabled": True, "min": 0, "max": 999999},
        "video_notes": {"enabled": True, "min": 0, "max": 999999},
    }

def build_forwarding_status_text(session_name: str, session_configs: dict, enabled: bool) -> str:
    if not isinstance(session_configs, dict):
        session_configs = {}
    channels = session_configs.get("channels", {})

    if not enabled:
        return (
            "⏹ <b>Пересылка выключена</b>\n\n"
            f"Сессия: <code>{escape(session_name)}</code>\n\n"
            "Для запуска нажмите кнопку ниже."
        )

    export_lines = []
    post_lines = []

    for raw_chat_id, channel_data in channels.items():
        if not isinstance(channel_data, dict):
            continue
        title_raw = channel_data.get("title") or channel_data.get("name") or f"Канал {raw_chat_id}"
        title = escape(str(title_raw))
        modes = channel_data.get("modes", {})

        export_mode = modes.get("export", {})
        if isinstance(export_mode, dict) and export_mode.get("enabled", False):
            enabled_types = get_enabled_filter_names(export_mode.get("filters", {}))
            media_text = ", ".join(enabled_types) if enabled_types else "все типы"
            export_lines.append(f"• <b>{title}</b> (<i>{media_text}</i>)")

        post_mode = modes.get("post", {})
        if isinstance(post_mode, dict) and post_mode.get("enabled", False):
            post_lines.append(f"• <b>{title}</b>")

    text = (
        "✅ <b>Пересылка включена!</b>\n\n"
        f"Сессия: <code>{escape(session_name)}</code>\n\n"
        "📤 <b>Каналы для экспорта:</b>\n"
    )
    text += "\n".join(export_lines) if export_lines else "• <i>Не выбраны</i>"
    text += "\n\n📥 <b>Каналы для постинга:</b>\n"
    text += "\n".join(post_lines) if post_lines else "• <i>Не выбраны</i>"
    return text