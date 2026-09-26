#database.py база данныз
import aiosqlite
import datetime 
from config import FERNET_CRYPTO  # Импортируем готовый объект из конфига
from cryptography.fernet import Fernet
import logging
import json
from datetime import datetime, timedelta, timezone

# Московское время (UTC+3)
MSK_TZ = timezone(timedelta(hours=3))

def get_msk_now() -> datetime:
    """Возвращает текущую дату и время по Москве (без микросекунд)."""
    return datetime.now(MSK_TZ).replace(microsecond=0)

def parse_db_date(date_str: str) -> datetime:
    """Парсит строку даты из базы данных в объект datetime по Москве."""
    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=MSK_TZ)
    
    
    
    
logger = logging.getLogger(__name__)

import json

class Database:
    DB_NAME = 'data/db.db'
    _fernet = None 
    _is_initialized = False
    
   # Добавить в class Database:

    @staticmethod
    async def count_user_sessions(user_id: int) -> int:
        """Возвращает количество сессий пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else 0

    @staticmethod
    async def count_selected_channels(user_id: int, session_name: str, mode: str) -> int:
        """Возвращает количество выбранных каналов для конкретного режима."""
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
        async with aiosqlite.connect(Database.DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT session_configs_json
                FROM user_sessions
                WHERE user_id = ? AND session_name = ?
                """,
                (user_id, session_name)
            )
            row = await cursor.fetchone()

            if not row:
                return 0

            raw_json = row["session_configs_json"]

            if not raw_json:
                return 0

            try:
                config = json.loads(raw_json)
            except Exception:
                return 0

            channels = config.get("channels", {})
            count = 0

            for channel_id, channel_data in channels.items():
                modes = channel_data.get("modes", {})
                mode_data = modes.get(mode)

                if isinstance(mode_data, dict) and mode_data.get("enabled") is True:
                    count += 1

            return count
    
   
    @staticmethod
    async def get_session_posting_status(
        user_id: int,
        session_name: str,
    ) -> bool:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                """
                SELECT Enable_posting
                FROM user_sessions
                WHERE user_id = ? AND session_name = ?
                """,
                (user_id, session_name),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return False

        return bool(row[0])

    @staticmethod
    async def update_session_posting_status(
        user_id: int,
        session_name: str,
        enabled: bool,
    ) -> None:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute(
                """
                UPDATE user_sessions
                SET Enable_posting = ?
                WHERE user_id = ? AND session_name = ?
                """,
                (
                    1 if enabled else 0,
                    user_id,
                    session_name,
                ),
            )

            await db.commit()
            
   
    
    
    @staticmethod
    async def setup():
        async with aiosqlite.connect(Database.DB_NAME) as db:
            # Таблица для хранения настроек
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_name TEXT NOT NULL,
                    session_string TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_configs_json TEXT DEFAULT '{}', -- Дефолтное значение для JSON
                    Enable_posting BOOLEAN DEFAULT FALSE, -- Постинг
                    UNIQUE(user_id, session_name), -- Пользователь не может иметь две сессии с одним именем
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )''')
            
            

            
            # Создаем таблицу премиума, если её нет
            await db.execute("""
                CREATE TABLE IF NOT EXISTS premium (
                    user_id INTEGER PRIMARY KEY,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL
                )
            """)
            
            # Таблица для администраторов
            await db.execute('''CREATE TABLE IF NOT EXISTS admins (
                          id INTEGER PRIMARY KEY AUTOINCREMENT,
                          user_id INTEGER UNIQUE,
                          username TEXT)''')

            # НОВАЯ ТАБЛИЦА: users
            await db.execute('''CREATE TABLE IF NOT EXISTS users (
                                user_id INTEGER PRIMARY KEY UNIQUE,
                                name TEXT,
                                username TEXT,
                                registration_date TEXT -- Храним дату как текст в формате ISO 8601
                            )''')
                            
     
            
            await db.commit()
    
    
    
    #для админ статы
    @staticmethod
    async def get_users_registered_today() -> int:
    	async with aiosqlite.connect(Database.DB_NAME) as db:
    		async with db.execute(
      		"SELECT COUNT(*) FROM users WHERE date(registration_date) = date('now')"
    		) as cursor:
    			row = await cursor.fetchone()
    			return row[0] if row else 0
    			
    			
    @staticmethod
    async def get_users_registered_this_week() -> int:
    	async with aiosqlite.connect(Database.DB_NAME) as db:
    		async with db.execute(
    		"SELECT COUNT(*) FROM users WHERE registration_date >= datetime('now', '-7 days')"
    		) as cursor:
    			row = await cursor.fetchone()
    			return row[0] if row else 0
    
    
    @staticmethod
    async def get_users_registered_this_month() -> int:
    	async with aiosqlite.connect(Database.DB_NAME) as db:
    		async with db.execute(
    		"SELECT COUNT(*) FROM users WHERE registration_date >= datetime('now', '-30 days')"
    		) as cursor:
    			row = await cursor.fetchone()
    			return row[0] if row else 0







    @staticmethod
    async def get_user_session(user_id: int, session_name: str) -> str | None:
        if not Database._fernet:
            raise RuntimeError("Fernet key not initialized. Call Database.initialize_fernet(key) first.")

        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute(
                "SELECT session_string FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name)
            )
            row = await cursor.fetchone()
            if row:
                encrypted_session = row[0]
                try:
                    decrypted_session = Database._fernet.decrypt(encrypted_session.encode()).decode()
                    return decrypted_session
                except Exception as e:
                    print(f"Error decrypting session string: {e}")
                    return None
            return None

    
     
        
    @staticmethod   
    async def get_session_configs(user_id: int, session_name: str) -> dict:
        """Получает JSON-конфигурации для указанной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute(
                "SELECT session_configs_json FROM user_sessions WHERE user_id = ? AND session_name = ?",
                (user_id, session_name)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return {} # Возвращаем пустой словарь, если нет конфигураций

    @staticmethod
    async def update_session_configs(user_id: int, session_name: str, configs: dict):
        """Обновляет JSON-конфигурации для указанной сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute(
                "UPDATE user_sessions SET session_configs_json = ? WHERE user_id = ? AND session_name = ?",
                (json.dumps(configs, ensure_ascii=False), user_id, session_name)
            )
            await db.commit()
    
    
    
            
    @classmethod
    def get_session_settings(cls, session_name: str): 
        return {
            "auto_reply": True,
            "notify": True
        }
    
    @classmethod
    def setup_encryption(cls, key: bytes):
        cls._fernet = Fernet(key)

    
 
    @staticmethod
    async def add_session(user_id, session_name, session_string):
        # Проверка, инициализирован ли он
        if not Database._fernet:
            raise RuntimeError("Шифрование не инициализировано! Вызовите Database.setup_encryption()")
        
        encrypted_session = Database._fernet.encrypt(session_string.encode()).decode()
        
        
        

    
    
            
            
            
      # --- Методы для шифрования ---
    
    
    @staticmethod
    def encrypt(data: str) -> str:
        """Шифрует строку."""
        return FERNET_CRYPTO.encrypt(data.encode()).decode()

    @staticmethod
    def decrypt(token: str) -> str:
        """Дешифрует строку."""
        return FERNET_CRYPTO.decrypt(token.encode()).decode()

    # --- Пример использования в методах ---

    @staticmethod
    async def save_user_session(user_id: int, session_name: str, session_string: str):
        """Пример сохранения зашифрованной сессии."""
        encrypted_session = Database.encrypt(session_string)
        
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('''
                INSERT INTO user_sessions (user_id, session_name, session_string)
                VALUES (?, ?, ?)
            ''', (user_id, session_name, encrypted_session))
            await db.commit()

    @staticmethod
    async def get_user_session(user_id: int, session_name: str):
        """Пример получения и расшифровки сессии."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute('''
                SELECT session_string FROM user_sessions 
                WHERE user_id = ? AND session_name = ?
            ''', (user_id, session_name))
            row = await cursor.fetchone()
            
            if row:
                return Database.decrypt(row[0])
            return None
            
        


    # --- Методы для администраторов ---

    #добавтть админа
    @staticmethod
    async def add_admin(user_id: int):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (user_id,))
            await db.commit()

    #удалить админа
    @staticmethod
    async def remove_admin(user_id: int):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
            await db.commit()

     #админ ли?
    @staticmethod
    async def is_admin(user_id: int) -> bool:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)) as cursor:
                result = await cursor.fetchone()
                return result is not None

    # --- Методы для постов (пример) ---

    @staticmethod
    async def add_post(original_id, source_chat_id, status, file_data):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('''INSERT INTO posts (original_id, source_chat_id, status, file_data) 
                                VALUES (?, ?, ?, ?)''', (original_id, source_chat_id, status, file_data))
            await db.commit()

    # --- НОВЫЕ МЕТОДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---

    @staticmethod
    async def add_user(user_id: int, name: str, username: str | None, registration_date: str):
        """Добавляет нового пользователя в базу данных, если его еще нет."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('''
                INSERT OR IGNORE INTO users (user_id, name, username, registration_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, name, username, registration_date))
            await db.commit()

    @staticmethod
    async def get_user(user_id: int):
        """Получает данные пользователя по его ID."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT user_id, name, username, registration_date FROM users WHERE user_id = ?', (user_id,)) as cursor:
                return await cursor.fetchone() # Возвращает кортеж или None

    @staticmethod
    async def user_exists(user_id: int) -> bool:
        """Проверяет, существует ли пользователь в базе данных."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def update_user_session(user_id: int, session_data: str | None):
        """Обновляет информацию о сессии пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('UPDATE users SET session = ? WHERE user_id = ?', (session_data, user_id))
            await db.commit()

    # --- НОВЫЕ МЕТОДЫ ДЛЯ СТАТИСТИКИ И РАССЫЛКИ (АДМИН) ---

    @staticmethod
    async def get_total_users() -> int:
        """Возвращает общее количество зарегистрированных пользователей."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT COUNT(*) FROM users') as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_today() -> int:
        """Возвращает количество пользователей, зарегистрированных сегодня."""
        today_str = datetime.date.today().isoformat()
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) = ?', (today_str,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_this_week() -> int:
        """Возвращает количество пользователей, зарегистрированных на текущей неделе (с понедельника)."""
        today = datetime.date.today()
        # Понедельник текущей недели
        start_of_week = today - datetime.timedelta(days=today.weekday())
        # Воскресенье текущей недели
        end_of_week = start_of_week + datetime.timedelta(days=6)

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) BETWEEN ? AND ?', 
                                  (start_of_week.isoformat(), end_of_week.isoformat())) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_users_registered_this_month() -> int:
        """Возвращает количество пользователей, зарегистрированных в текущем месяце."""
        today = datetime.date.today()
        start_of_month = today.replace(day=1)
        # Для конца месяца: начало следующего месяца минус один день
        if today.month == 12:
            end_of_month = today.replace(year=today.year + 1, month=1, day=1) - datetime.timedelta(days=1)
        else:
            end_of_month = today.replace(month=today.month + 1, day=1) - datetime.timedelta(days=1)

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT COUNT(*) FROM users WHERE substr(registration_date, 1, 10) BETWEEN ? AND ?', 
                                  (start_of_month.isoformat(), end_of_month.isoformat())) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    async def get_all_user_ids() -> list[int]:
        """Возвращает список всех user_id из таблицы users."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT user_id FROM users') as cursor:
                return [row[0] for row in await cursor.fetchall()]

    @staticmethod
    async def get_all_admin_ids() -> list[int]:
        """Возвращает список всех user_id из таблицы admins."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT user_id FROM admins') as cursor:
                return [row[0] for row in await cursor.fetchall()]
                
                
                
    @staticmethod
    async def add_user(user_id, name, user_username, registration_date,session):
        """Добавляет нового пользователя, если его нет в базе."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('''
                INSERT OR IGNORE INTO users (user_id, name, username, registration_date, session)
                VALUES (?, ?, ?, ?,?)
            ''', (user_id, name, user_username, registration_date,session))
            await db.commit()
            #logger.info(f"User {user_id} added or already exists.")


    @staticmethod
    async def add_session(user_id: int, session_name: str, session_string: str):
        """Добавляет новую Pyrogram сессию для пользователя."""
        if not Database._fernet:
            raise ValueError("Encryption key (FERNET_KEY) is not set in config.py. Cannot encrypt session string.")

        encrypted_session_string = Database._fernet.encrypt(session_string.encode()).decode()
        async with aiosqlite.connect(Database.DB_NAME) as db:
                await db.execute('''
                INSERT INTO user_sessions (user_id, session_name, session_string)
                VALUES (?, ?, ?)
                ''', (user_id, session_name, encrypted_session_string))
                await db.commit()
                logger.info(f"Session '{session_name}' added for user {user_id}.")

    
    
    @staticmethod
    async def get_user_sessions_list(user_id: int) -> list[tuple[str, str]]:
        """Возвращает список всех сессий пользователя (имя)."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT session_name FROM user_sessions WHERE user_id = ?', (user_id,)) as cursor:
                return await cursor.fetchall()
                
                
                
    @staticmethod
    async def get_user_sessions(user_id: int) -> list[tuple[str, str]]:
        """Возвращает список всех сессий пользователя (имя, зашифрованная строка)."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT session_name, session_string FROM user_sessions WHERE user_id = ?', (user_id,)) as cursor:
                return await cursor.fetchall()
                    

    @staticmethod
    async def get_session_string(user_id: int, session_name: str) -> str | None:
        """Возвращает расшифрованную session_string по имени сессии для пользователя."""
        if not Database._fernet:
            raise ValueError("Encryption key (FERNET_KEY) is not set in config.py. Cannot decrypt session string.")

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT session_string FROM user_sessions WHERE user_id = ? AND session_name = ?', 
                                  (user_id, session_name)) as cursor:
                result = await cursor.fetchone()
                if result:
                    encrypted_session_string = result[0].encode()
                    return Database._fernet.decrypt(encrypted_session_string).decode()
                return None

    @staticmethod
    async def delete_session(user_id: int, session_name: str):
        """Удаляет сессию пользователя по имени."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('DELETE FROM user_sessions WHERE user_id = ? AND session_name = ?', (user_id, session_name))
            await db.commit()
            logger.info(f"Session '{session_name}' deleted for user {user_id}.")

    @staticmethod
    async def get_next_session_number(user_id: int) -> int:
        """Определяет следующий доступный номер сессии для пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT session_name FROM user_sessions WHERE user_id = ?', (user_id,)) as cursor:
                existing_names = [name for name, in await cursor.fetchall()]
                i = 1
                while f"Сессия {i}" in existing_names:
                    i += 1
                return i	
                
                				
                								
    

    # =========================================================================
    # 5 ФУНКЦИЙ ДЛЯ РАБОТЫ С ПРЕМИУМ-СТАТУСОМ
    # =========================================================================

                								
    @staticmethod
    async def is_premium_active(user_id: int) -> bool:
        """1. Проверяет активен ли Премиум (с авто-удалением, если истек)."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT end_date FROM premium WHERE user_id = ?", (user_id,)
            ) as cursor:
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
        """2. Возвращает (дата_начала, дата_окончания) в формате ДД.ММ.ГГГГ ЧЧ:ММ."""
        if not await Database.is_premium_active(user_id):
            return None

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT start_date, end_date FROM premium WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                start_dt = parse_db_date(row[0]).strftime("%d.%m.%Y %H:%M")
                end_dt = parse_db_date(row[1]).strftime("%d.%m.%Y %H:%M")
                return start_dt, end_dt

            return None

    @staticmethod
    async def get_premium_remaining_time(user_id: int) -> str | None:
        """3. Возвращает оставшееся время: 'X дн. Y ч. Z мин.'."""
        if not await Database.is_premium_active(user_id):
            return None

        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT end_date FROM premium WHERE user_id = ?", (user_id,)
            ) as cursor:
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
        """4. Добавляет или продлевает Премиум на N дней по МСК."""
        now = get_msk_now()
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute(
                "SELECT start_date, end_date FROM premium WHERE user_id = ?", (user_id,)
            ) as cursor:
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
        """5. Удаляет Премиум у пользователя."""
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("DELETE FROM premium WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def cleanup_expired_premiums() -> int:
        """Служебный метод: удаляет все просроченные подписки."""
        now_str = get_msk_now().strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(Database.DB_NAME) as db:
            cursor = await db.execute("DELETE FROM premium WHERE end_date <= ?", (now_str,))
            await db.commit()
            return cursor.rowcount