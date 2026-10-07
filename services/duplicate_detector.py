# services/duplicate_detector.py
import asyncio
import hashlib
import io
import logging
import os
import sqlite3
from PIL import Image

from database import Database
from services.config_service import read_full_config

logger = logging.getLogger(__name__)

DB_PATH = "data/duplicate_detector.db"
DEFAULT_HAMMING_THRESHOLD = 4


def init_duplicate_db():
    """Инициализация базы данных и таблиц детектора дубликатов."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            target_chat_id INTEGER,
            source_chat_id INTEGER,
            message_id INTEGER,
            target_message_id INTEGER,
            post_type TEXT,
            content_hash TEXT,
            meta_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_antidup_settings (
            user_id INTEGER PRIMARY KEY,
            is_enabled INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()


async def is_antidup_enabled_for_user(user_id: int) -> bool:
    """Проверяет, включена ли функция у пользователя."""
    config = read_full_config()
    mode = config.get("anti_duplicate_mode", "all")

    if mode == "off":
        return False

    if mode == "premium":
        is_prem = await Database.is_premium_active(user_id)
        if not is_prem:
            return False

    def _check_db():
        init_duplicate_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT is_enabled FROM user_antidup_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return True
        return bool(row[0])

    return await asyncio.to_thread(_check_db)


async def set_user_antidup_status(user_id: int, enabled: bool):
    """Включает или выключает анти-повтор для конкретного юзера."""
    def _save_db():
        init_duplicate_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_antidup_settings (user_id, is_enabled) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_enabled = excluded.is_enabled
        """, (user_id, 1 if enabled else 0))
        conn.commit()
        conn.close()
    await asyncio.to_thread(_save_db)


def get_text_hash(text: str) -> str:
    """Генерирует SHA-256 хеш текста."""
    if not text:
        return ""
    normalized = " ".join(text.strip().lower().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def calculate_average_hash(image_bytes: bytes) -> str:
    """Вычисляет перцептивный хеш (aHash) для изображения."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('L').resize((8, 8), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / 64
        return "".join("1" if p > avg else "0" for p in pixels)
    except Exception as e:
        logger.error("Ошибка расчета хеша картинки: %s", e)
        return ""


def get_hamming_distance(hash1: str, hash2: str) -> int:
    """Вычисляет расстояние Хэмминга между хэшами картинок."""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def _make_link(chat_id: int, message_id: int, username: str = None) -> str:
    """Формирует прямую ссылку на сообщение в Telegram."""
    if not chat_id or not message_id:
        return ""
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    str_id = str(chat_id)
    if str_id.startswith("-100"):
        return f"https://t.me/c/{str_id[4:]}/{message_id}"
    return f"https://t.me/c/{str_id.lstrip('-')}/{message_id}"


async def process_and_clean_duplicate(
    client,
    target_chat_id: int,
    source_chat_id: int,
    source_title: str,
    source_message_id: int,
    sent_message_id: int,
    user_id: int,
    post_type: str,
    current_hash: str,
    session_configs: dict,
    meta_info: str = ""
) -> bool:
    """Проверяет пост на дубликат, формирует отчет с тремя ссылками и удаляет повтор при включенном режиме."""
    config = read_full_config()
    threshold = config.get("anti_duplicate_hamming_threshold", DEFAULT_HAMMING_THRESHOLD)

    def _fetch_records():
        init_duplicate_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            SELECT message_id, target_message_id, source_chat_id, post_type, content_hash, meta_info 
            FROM channel_posts 
            WHERE user_id = ? AND target_chat_id = ?
        """, (user_id, target_chat_id))
        rows = cur.fetchall()
        conn.close()
        return rows

    records = await asyncio.to_thread(_fetch_records)
    is_duplicate = False
    original_msg_id = None
    prev_published_msg_id = None
    original_source_chat = None

    for orig_id, prev_target_id, orig_src_chat, orig_type, orig_hash, orig_meta in records:
        if post_type == "text" and orig_type == "text":
            if orig_hash and orig_hash == current_hash:
                is_duplicate = True
                original_msg_id = orig_id
                prev_published_msg_id = prev_target_id
                original_source_chat = orig_src_chat
                break

        elif post_type == "photo" and orig_type == "photo":
            if get_hamming_distance(orig_hash, current_hash) <= threshold:
                is_duplicate = True
                original_msg_id = orig_id
                prev_published_msg_id = prev_target_id
                original_source_chat = orig_src_chat
                break

        elif post_type == "video" and orig_type == "video":
            if current_hash and current_hash == orig_hash:
                is_duplicate = True
                original_msg_id = orig_id
                prev_published_msg_id = prev_target_id
                original_source_chat = orig_src_chat
                break
            if meta_info and orig_meta and meta_info == orig_meta:
                is_duplicate = True
                original_msg_id = orig_id
                prev_published_msg_id = prev_target_id
                original_source_chat = orig_src_chat
                break

    if is_duplicate:
        # 1. Ссылка на оригинал в источнике
        orig_link = _make_link(original_source_chat, original_msg_id)
        # 2. Ссылка на ранее опубликованный пост в канале постинга
        prev_link = _make_link(target_chat_id, prev_published_msg_id) if prev_published_msg_id else "#"
        # 3. Ссылка на новый дубликат
        dup_link = _make_link(target_chat_id, sent_message_id)

        antidup_enabled = await is_antidup_enabled_for_user(user_id)
        from services.forwarder.engine import send_user_log

        if antidup_enabled:
            try:
                await client.delete_messages(chat_id=target_chat_id, message_ids=sent_message_id)
                logger.info("🛡 Анти-повтор: удален дубликат в канале %s", target_chat_id)

                log_msg = (
                    f"⚠️ <b>Анти-повтор: Обнаружен и удален дубликат!</b>\n"
                    f"├ 📌 Тип: <b>{post_type.upper()}</b>\n"
                    f"├ 📤 Из источника: <b>{source_title}</b>\n"
                    f"├ 🔗 <b>1. Оригинал в источнике:</b> <a href='{orig_link}'>Открыть оригинал</a>\n"
                    f"├ 🔗 <b>2. Ранее публиковался:</b> <a href='{prev_link}'>Открыть прошлый пост</a>\n"
                    f"└ 🗑 <b>3. Удаленный дубликат:</b> <a href='{dup_link}'>Посмотреть пост</a>"
                )
                await send_user_log(user_id, "success", log_msg, session_configs)
                return True
            except Exception as e:
                logger.error("❌ Ошибка удаления дубликата: %s", e)
                return False
        else:
            log_msg = (
                f"ℹ️ <b>Анти-повтор (Выключен): Найден повтор!</b>\n"
                f"├ 📌 Тип: <b>{post_type.upper()}</b>\n"
                f"├ 📤 Из источника: <b>{source_title}</b>\n"
                f"├ 🔗 <b>1. Оригинал в источнике:</b> <a href='{orig_link}'>Открыть оригинал</a>\n"
                f"├ 🔗 <b>2. Ранее публиковался:</b> <a href='{prev_link}'>Открыть прошлый пост</a>\n"
                f"└ 🔗 <b>3. Новый дубликат (оставлен):</b> <a href='{dup_link}'>Открыть дубликат</a>"
            )
            await send_user_log(user_id, "filtered", log_msg, session_configs)
            return False

    # Если пост уникальный — сохраняем с указанием ID целевого сообщения
    def _insert_record():
        init_duplicate_db()
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO channel_posts (user_id, target_chat_id, source_chat_id, message_id, target_message_id, post_type, content_hash, meta_info)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, target_chat_id, source_chat_id, source_message_id, sent_message_id, post_type, current_hash, meta_info))
        conn.commit()
        conn.close()

    await asyncio.to_thread(_insert_record)
    return False