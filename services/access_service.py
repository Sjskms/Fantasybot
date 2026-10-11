import json
import logging
import os
from typing import Any, Dict

from database import Database

logger = logging.getLogger(__name__)

CONFIG_DIR = "configuration"
ACCESS_CONFIG_PATH = os.path.join(CONFIG_DIR, "access.json")

# Ключи функций и их читаемые названия
FEATURE_NAMES: Dict[str, str] = {
    "antidup": "Анти повтор",
    "text_transform": "Текст и ссылки (HTML)",
    "logs": "Настройка логов",
    "stats": "Статистика",
}

# Режимы доступа
ACCESS_MODES: Dict[str, str] = {
    "all": "Всех пользователей",
    "premium": "Премиум пользователям",
    "admin": "Админам",
    "off": "Отключить",
}

DEFAULT_ACCESS_CONFIG: Dict[str, Any] = {
    "antidup": "all",
    "text_transform": "all",
    "logs": "all",
    "stats": "all",
}


def ensure_access_config_dir():
    """Создает директорию configuration, если она отсутствует."""
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)


def load_access_config() -> Dict[str, Any]:
    """Синхронно загружает конфигурацию доступа из configuration/access.json."""
    ensure_access_config_dir()
    if not os.path.exists(ACCESS_CONFIG_PATH):
        save_access_config(DEFAULT_ACCESS_CONFIG)
        return DEFAULT_ACCESS_CONFIG.copy()

    try:
        with open(ACCESS_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = DEFAULT_ACCESS_CONFIG.copy()
            merged.update(data)
            return merged
    except Exception as e:
        logger.error("Ошибка при чтении %s: %s", ACCESS_CONFIG_PATH, e)
        return DEFAULT_ACCESS_CONFIG.copy()


def save_access_config(cfg: Dict[str, Any]):
    """Синхронно сохраняет настройки доступа в configuration/access.json."""
    ensure_access_config_dir()
    try:
        with open(ACCESS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Ошибка при записи %s: %s", ACCESS_CONFIG_PATH, e)


def get_feature_access_mode(feature_key: str) -> str:
    """Возвращает текущий режим доступа для указанной функции (all, premium, admin, off)."""
    cfg = load_access_config()
    return cfg.get(feature_key, "all")


def set_feature_access_mode(feature_key: str, mode: str):
    """Устанавливает режим доступа для функции и сохраняет в JSON."""
    cfg = load_access_config()
    cfg[feature_key] = mode
    save_access_config(cfg)
    
    
async def check_user_access(user_id: int, feature_key: str) -> tuple[bool, str]:
    """
    Проверяет, имеет ли пользователь доступ к конкретной функции.
    Возвращает (имеет_доступ, причина):
    - (True, "ok")
    - (False, "off") -> отключена админом
    - (False, "premium") -> требуется Премиум
    - (False, "admin") -> только для администраторов
    """
    mode = get_feature_access_mode(feature_key)

    if mode == "off":
        return False, "off"

    if mode == "all":
        return True, "ok"

    is_admin = await Database.is_admin(user_id)
    if is_admin:
        return True, "ok"

    if mode == "admin":
        return False, "admin"

    if mode == "premium":
        is_prem = await Database.is_premium_active(user_id)
        if is_prem:
            return True, "ok"
        return False, "premium"

    return False, "off"