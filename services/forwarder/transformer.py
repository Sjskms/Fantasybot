# services/forwarder/transformer.py
import re
from pyrogram.types import Message


def is_transform_needed(transform_config: dict) -> bool:
    """Проверяет, требуются ли изменения в тексте сообщения."""
    if not isinstance(transform_config, dict):
        return False

    mode = transform_config.get("mode", "keep")
    custom_text = str(transform_config.get("custom_text", "")).strip()
    replace_words = transform_config.get("replace_words", [])
    replace_links = transform_config.get("replace_links", [])

    return bool(
        (mode != "keep" and custom_text)
        or replace_words
        or replace_links
    )


def transform_text(
    original_html: str,
    transform_config: dict,
    is_media: bool = False,
) -> str:
    """Выполняет замену слов, ссылок и подстановку текста по настройкам."""
    if not isinstance(transform_config, dict):
        return original_html or ""

    text = original_html or ""

    for item in transform_config.get("replace_links", []):
        old_link = item.get("from")
        new_link = item.get("to")
        if old_link:
            text = text.replace(old_link, new_link or "")

    for item in transform_config.get("replace_words", []):
        old_word = item.get("from")
        new_word = item.get("to")
        if old_word:
            text = re.sub(
                re.escape(old_word),
                new_word or "",
                text,
                flags=re.IGNORECASE,
            )

    mode = transform_config.get("mode", "keep")
    custom_text = transform_config.get("custom_text", "")

    if mode == "replace" and is_media:
        text = custom_text
    elif mode == "append" and custom_text:
        text = f"{text}\n\n{custom_text}" if text else custom_text
    elif mode == "prepend" and custom_text:
        text = f"{custom_text}\n\n{text}" if text else custom_text

    return text.strip()


def get_message_html(message: Message) -> tuple[str, bool]:
    """Извлекает исходный HTML-текст или подпись из сообщения."""
    is_media = bool(message.media)
    source = message.caption if is_media else message.text
    html_text = getattr(source, "html", None) or source or ""
    return html_text, is_media
