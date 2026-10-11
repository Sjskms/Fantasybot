import json
import logging
from datetime import date, datetime, timedelta, timezone

import aiosqlite
from cryptography.fernet import Fernet

from config import FERNET_CRYPTO

logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))


def get_msk_now() -> datetime:
    """Возвращает текущую дату и время по Москве (без микросекунд)."""
    return datetime.now(MSK_TZ).replace(microsecond=0)


def parse_db_date(date_str: str) -> datetime:
    """Парсит строку даты из базы данных в объект datetime по Москве."""
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=MSK_TZ)


class Database:
    DB_NAME = "data/db.db"
    _fernet = None

    @staticmethod
    async def setup():
        """Создает все необходимые таблицы в базе данных SQLite."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_name TEXT NOT NULL,
                    session_string TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_configs_json TEXT DEFAULT '{}',
                    Enable_posting BOOLEAN DEFAULT FALSE,
                    UNIQUE(user_id, session_name),
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS premium (
                    user_id INTEGER PRIMARY KEY,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS processed_payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    days INTEGER,
                    amount REAL DEFAULT 0,
                    currency TEXT DEFAULT 'RUB',
                    method TEXT DEFAULT 'card',
                    date TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY UNIQUE,
                    name TEXT,
                    username TEXT,
                    registration_date TEXT
                )
            """)

            await db.commit()

    @classmethod
    def setup_encryption(cls, key: bytes):
        """Инициализирует Fernet шифрование для строк сессий."""
        cls._fernet = Fernet(key)

    @staticmethod
    def encrypt(data: str) -> str:
        """Шифрует произвольную строку с использованием FERNET_CRYPTO."""
        return FERNET_CRYPTO.encrypt(data.encode()).decode()

    @staticmethod
    def decrypt(token: str) -> str:
        """Дешифрует токен с использованием FERNET_CRYPTO."""
        return FERNET_CRYPTO.decrypt(token.encode()).decode()

    # =========================================================================
    # МЕТОДЫ ОПЛАТ И СТАТИСТИКИ ПЛАТЕЖЕЙ
    # =========================================================================

    @staticmethod
    async def get_payment_stats() -> dict:
        """Возвращает детальную аналитику заказов и финансов по шлюзам."""
        now = get_msk_now()
        today_str = now.strftime("%Y-%m-%d")
        week_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        month_ago_str = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT COUNT(*) FROM processed_payments") as cur:
                total_count = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM processed_payments WHERE date LIKE ?", (f"{today_str}%",)) as cur:
                today_count = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM processed_payments WHERE date >= ?", (week_ago_str,)) as cur:
                week_count = (await cur.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM processed_payments WHERE date >= ?", (month_ago_str,)) as cur:
                month_count = (await cur.fetchone())[0]

            async with db.execute("SELECT COALESCE(SUM(days), 0), COUNT(DISTINCT user_id) FROM processed_payments") as cur:
                row = await cur.fetchone()
                total_days = row[0]
                unique_buyers = row[1]

            async def get_method_revenue(method_name: str):
                queries = {
                    "today": ("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM processed_payments WHERE method = ? AND date LIKE ?", (method_name, f"{today_str}%")),
                    "week": ("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM processed_payments WHERE method = ? AND date >= ?", (method_name, week_ago_str)),
                    "month": ("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM processed_payments WHERE method = ? AND date >= ?", (method_name, month_ago_str)),
                    "all": ("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM processed_payments WHERE method = ?", (method_name,)),
                }
                res = {}
                for period, (sql, params) in queries.items():
                    async with db.execute(sql, params) as c:
                        r = await c.fetchone()
                        res[period] = {"sum": round(r[0], 2), "cnt": r[1]}
                return res

            cb_stats = await get_method_revenue("cryptobot")
            card_stats = await get_method_revenue("card")

            return {
                "total_count": total_count,
                "today_count": today_count,
                "week_count": week_count,
                "month_count": month_count,
                "total_days": total_days,
                "unique_buyers": unique_buyers,
                "cb": cb_stats,
                "card": card_stats,
            }

    @staticmethod
    async def is_payment_processed(payment_id: str) -> bool:
        """Проверяет, был ли этот платеж уже обработан."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT 1 FROM processed_payments WHERE payment_id = ?", (payment_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def mark_payment_processed(
        payment_id: str,
        user_id: int,
        days: int,
        amount: float = 0,
        currency: str = "RUB",
        method: str = "card",
    ):
        """Помечает платеж как обработанный с сохранением метода и суммы."""
        now = get_msk_now().strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("""
                INSERT INTO processed_payments (payment_id, user_id, days, amount, currency, method, date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(payment_id) DO NOTHING
            """, (payment_id, user_id, days, float(amount), currency, method, now))
            await db.commit()

    # =========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ С СЕССИЯМИ
    # =========================================================================

    @staticmethod
    async def count_user_sessions(user_id: int) -> int:
        """Возвращает количество сессий пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else 0

    @staticmethod
    async def count_selected_channels(user_id: int, session_name: str, mode: str) -> int:
        """Возвращает количество выбранных каналов для конкретного режима (export / post)."""
        configs = await Database.get_session_configs(user_id, session_name)
        if not configs or "channels" not in configs:
            return 0

        count = 0
        for ch_data in configs["channels"].values():
            m_cfg = ch_data.get("modes", {}).get(mode, {})
            if m_cfg.get("enabled", False):
                count += 1
        return count

    @staticmethod
    async def get_session_channels_count(user_id: int, session_name: str, mode: str) -> int:
        """Считает количество активных каналов режима экспорта или постинга из JSON."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT session_configs_json FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name),
            )
            row = await cursor.fetchone()
            if not row or not row["session_configs_json"]:
                return 0

            try:
                config = json.loads(row["session_configs_json"])
            except Exception:
                return 0

            channels = config.get("channels", {})
            count = 0
            for channel_data in channels.values():
                modes = channel_data.get("modes", {})
                mode_data = modes.get(mode)
                if isinstance(mode_data, dict) and mode_data.get("enabled") is True:
                    count += 1

            return count

    @staticmethod
    async def get_session_posting_status(user_id: int, session_name: str) -> bool:
        """Возвращает статус активности пересылки для конкретной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT Enable_posting FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return False
        return bool(row[0])

    @staticmethod
    async def update_session_posting_status(user_id: int, session_name: str, enabled: bool) -> None:
        """Обновляет флаг активности пересылки для конкретной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute(
                "UPDATE user_sessions SET Enable_posting = ? WHERE user_id = ? AND session_name = ?",
                (1 if enabled else 0, user_id, session_name),
            )
            await db.commit()

    @staticmethod
    async def get_session_configs(user_id: int, session_name: str) -> dict:
        """Получает JSON-конфигурации для указанной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute(
                "SELECT session_configs_json FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    return {}
            return {}

    @staticmethod
    async def update_session_configs(user_id: int, session_name: str, configs: dict):
        """Обновляет JSON-конфигурации для указанной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute(
                "UPDATE user_sessions SET session_configs_json = ? WHERE user_id = ? AND session_name = ?",
                (json.dumps(configs, ensure_ascii=False), user_id, session_name),
            )
            await db.commit()

    @staticmethod
    async def add_session(user_id: int, session_name: str, session_string: str):
        """Добавляет новую Pyrogram сессию для пользователя."""
        encrypted_session_string = Database.encrypt(session_string)
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("""
                INSERT INTO user_sessions (user_id, session_name, session_string)
                VALUES (?, ?, ?)
            """, (user_id, session_name, encrypted_session_string))
            await db.commit()
            logger.info("Session '%s' added for user %s.", session_name, user_id)

    @staticmethod
    async def get_user_sessions_list(user_id: int) -> list[tuple[str]]:
        """Возвращает список всех имен сессий пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT session_name FROM user_sessions WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchall()

    @staticmethod
    async def get_user_sessions(user_id: int) -> list[tuple[str, str]]:
        """Возвращает список всех сессий пользователя (имя, зашифрованная строка)."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT session_name, session_string FROM user_sessions WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchall()

    @staticmethod
    async def get_session_string(user_id: int, session_name: str) -> str | None:
        """Возвращает расшифрованную session_string по имени сессии для пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT session_string FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name),
            ) as cursor:
                result = await cursor.fetchone()
                if result and result[0]:
                    try:
                        return Database.decrypt(result[0])
                    except Exception as e:
                        logger.error("Ошибка расшифровки строки сессии: %s", e)
                        return None
                return None

    @staticmethod
    async def delete_session(user_id: int, session_name: str):
        """Удаляет сессию пользователя по имени."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("DELETE FROM user_sessions WHERE user_id = ? AND session_name = ?", (user_id, session_name))
            await db.commit()
            logger.info("Session '%s' deleted for user %s.", session_name, user_id)

    @staticmethod
    async def get_next_session_number(user_id: int) -> int:
        """Определяет следующий доступный порядковый номер сессии для пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT session_name FROM user_sessions WHERE user_id = ?", (user_id,)) as cursor:
                existing_names = [name for name, in await cursor.fetchall()]
                i = 1
                while f"Сессия {i}" in existing_names:
                    i += 1
                return i

    # =========================================================================
    # МЕТОДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ И АДМИНИСТРАТОРОВ
    # =========================================================================

    @staticmethod
    async def add_user(user_id: int, name: str, username: str | None, registration_date: str):
        """Добавляет нового пользователя в базу данных, если его еще нет."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("""
                INSERT OR IGNORE INTO users (user_id, name, username, registration_date)
                VALUES (?, ?, ?, ?)
            """, (user_id, name, username, registration_date))
            await db.commit()

    @staticmethod
    async def get_user(user_id: int):
        """Получает данные пользователя по его ID."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id, name, username, registration_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone()

    @staticmethod
    async def user_exists(user_id: int) -> bool:
        """Проверяет, существует ли пользователь в базе данных."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def add_admin(user_id: int):
        """Добавляет администратора в базу данных."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            await db.commit()

    @staticmethod
    async def remove_admin(user_id: int):
        """Удаляет администратора из базы данных."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await db.commit()

    @staticmethod
    async def is_admin(user_id: int) -> bool:
        """Проверяет, является ли пользователь администратором."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
                return result is not None

    @staticmethod
    async def get_total_users() -> int:
        """Возвращает общее количество зарегистрированных пользователей."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_today() -> int:
        """Возвращает количество пользователей, зарегистрированных за сегодня."""
        today = date.today()
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) = ?", (today.isoformat(),)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_this_week() -> int:
        """Возвращает количество пользователей, зарегистрированных на этой неделе."""
        today = date.today()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) BETWEEN ? AND ?",
                (start_of_week.isoformat(), end_of_week.isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_this_month() -> int:
        """Возвращает количество пользователей, зарегистрированных в текущем месяце."""
        today = date.today()
        start_of_month = today.replace(day=1)
        if today.month == 12:
            end_of_month = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) BETWEEN ? AND ?",
                (start_of_month.isoformat(), end_of_month.isoformat()),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_all_user_ids() -> list[int]:
        """Возвращает список всех ID пользователей."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id FROM users") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    @staticmethod
    async def get_all_admin_ids() -> list[int]:
        """Возвращает список всех ID администраторов."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id FROM admins") as cursor:
                return [row[0] for row in await cursor.fetchall()]

    # =========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ С ПРЕМИУМ-СТАТУСОМ
    # =========================================================================

    @staticmethod
    async def get_expired_premium_user_ids() -> list[int]:
        """Возвращает список ID пользователей, у которых истек Premium-статус."""
        now = get_msk_now().strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT user_id FROM premium WHERE end_date <= ?", (now,)) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    @staticmethod
    async def is_premium_active(user_id: int) -> bool:
        """Проверяет активен ли Премиум (с авто-удалением, если истек)."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT end_date FROM premium WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                return False

            end_date = parse_db_date(row[0])
            now = get_msk_now()

            if now >= end_date:
                await db.execute("DELETE FROM premium WHERE user_id = ?", (user_id,))
                await db.commit()
                return False

            return True

    @staticmethod
    async def get_premium_dates(user_id: int) -> tuple[str, str] | None:
        """Возвращает (дата_начала, дата_окончания) подписки в формате ДД.ММ.ГГГГ ЧЧ:ММ."""
        if not await Database.is_premium_active(user_id):
            return None

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT start_date, end_date FROM premium WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            if row:
                start_dt = parse_db_date(row[0]).strftime("%d.%m.%Y %H:%M")
                end_dt = parse_db_date(row[1]).strftime("%d.%m.%Y %H:%M")
                return start_dt, end_dt

            return None

    @staticmethod
    async def get_premium_remaining_time(user_id: int) -> str | None:
        """Возвращает остаток времени действия подписки: 'X дн. Y ч. Z мин.'."""
        if not await Database.is_premium_active(user_id):
            return None

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT end_date FROM premium WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            if not row:
                return None

            end_date = parse_db_date(row[0])
            now = get_msk_now()
            diff = end_date - now

            if diff.total_seconds() <= 0:
                return None

            days = diff.days
            hours = diff.seconds // 3600
            minutes = (diff.seconds % 3600) // 60

            parts = []
            if days > 0:
                parts.append(f"{days} дн.")
            if hours > 0:
                parts.append(f"{hours} ч.")
            parts.append(f"{minutes} мин.")

            return " ".join(parts)

    @staticmethod
    async def add_premium(user_id: int, days: int) -> str:
        """Добавляет или продлевает Премиум на N дней по МСК."""
        now = get_msk_now()
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute("SELECT start_date, end_date FROM premium WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            if row:
                current_end = parse_db_date(row[1])
                base_start = parse_db_date(row[0]) if current_end > now else now
                base_end = current_end if current_end > now else now
            else:
                base_start = now
                base_end = now

            new_end = base_end + timedelta(days=days)
            start_str = base_start.strftime("%Y-%m-%d %H:%M:%S")
            end_str = new_end.strftime("%Y-%m-%d %H:%M:%S")

            await db.execute("""
                INSERT INTO premium (user_id, start_date, end_date)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    start_date = excluded.start_date,
                    end_date = excluded.end_date
            """, (user_id, start_str, end_str))
            await db.commit()

            return new_end.strftime("%d.%m.%Y %H:%M")

    @staticmethod
    async def remove_premium(user_id: int) -> bool:
        """Удаляет Премиум у пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("DELETE FROM premium WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def cleanup_expired_premiums() -> int:
        """Служебный метод: удаляет все просроченные подписки из таблицы premium."""
        now_str = get_msk_now().strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("DELETE FROM premium WHERE end_date <= ?", (now_str,))
            await db.commit()
            return cursor.rowcount
