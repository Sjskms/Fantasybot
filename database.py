#database.py база данныз
import aiosqlite
import datetime 
from config import FERNET_CRYPTO  # Импортируем готовый объект из конфига
from cryptography.fernet import Fernet
import logging

logger = logging.getLogger(__name__)

import json

class Database:
    DB_NAME = 'data/db.db'
    _fernet = None 
    _is_initialized = False
    
    
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
                )
            ''')
            
            await db.execute('''CREATE TABLE IF NOT EXISTS settings 
                                (key TEXT PRIMARY KEY, value TEXT)''')

            # Таблица для хранения контента
            await db.execute('''CREATE TABLE IF NOT EXISTS posts 
                                (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                                 original_id INTEGER, 
                                 source_chat_id INTEGER, 
                                 status TEXT, 
                                 file_data TEXT, 
                                 timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

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
                                registration_date TEXT, -- Храним дату как текст в формате ISO 8601
                                session TEXT DEFAULT NULL
                            )''')
                            
                            
            # НОВАЯ ТАБЛИЦА: sessions
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL,
                    chan_type TEXT,
                    channel_id TEXT,
                    chat_title TEXT
                )
            """)
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS session_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_name TEXT NOT NULL,
                    chan_type TEXT NOT NULL, -- 'export' или 'import'
                    channel_id INTEGER NOT NULL,
                    chat_title TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (session_name, chan_type), -- Одна сессия может иметь только один экспортный и один импортный канал
                    FOREIGN KEY (session_name) REFERENCES sessions (session_name) ON DELETE CASCADE
                )
            ''')
            
            await db.execute('''
                    CREATE TABLE IF NOT EXISTS session_settings (
                        session_name TEXT PRIMARY KEY,
                        auto_forward INTEGER DEFAULT 0,
                        forward_filter TEXT,
                        custom_header TEXT,
                        replace_words TEXT,
                        send_original_link INTEGER DEFAULT 0,
                        delete_original_after_forward INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (session_name) REFERENCES sessions (session_name) ON DELETE CASCADE
                    )
                ''')
            
            await db.commit()
    
    
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
    
    
    
    



    
    @staticmethod
    async def update_session_channel(session_name: str, chan_type: str, channel_id: int, chat_title: str):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('''
                INSERT OR REPLACE INTO session_channels (session_name, chan_type, channel_id, chat_title)
                VALUES (?, ?, ?, ?)
            ''', (session_name, chan_type, channel_id, chat_title))
            await db.commit()
            logger.info(f"Channel {chat_title} updated.")
    
    @staticmethod
    async def get_session_settings(session_name: str) -> dict:
        async with aiosqlite.connect(Database.DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            
            # 1. Получаем общие настройки сессии (предположим, есть таблица sessions)
            cursor_settings = await db.execute(
                "SELECT auto_reply, notify FROM sessions WHERE session_name = ?",
                (session_name,)
            )
            settings_row = await cursor_settings.fetchone()
            result = dict(settings_row) if settings_row else {'auto_reply': False, 'notify': False}

            # 2. Получаем все каналы для этой сессии
            cursor_channels = await db.execute(
                "SELECT chan_type, channel_id FROM session_channels WHERE session_name = ?",
                (session_name,)
            )
            channels = await cursor_channels.fetchall()
            
            # 3. Добавляем каналы в общий словарь настроек
            for row in channels:
                # Теперь ключ 'post' будет добавлен в словарь
                result[row['chan_type']] = row['channel_id']
                
            return result
            
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
            
        

    # --- Методы для настроек ---

    @staticmethod
    async def get_setting(key: str):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            async with db.execute('SELECT value FROM settings WHERE key = ?', (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    @staticmethod
    async def update_setting(key: str, value: str):
        async with aiosqlite.connect(Database.DB_NAME) as db:
            await db.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
            await db.commit()

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
            async with db.execute('SELECT user_id, name, username, registration_date, session FROM users WHERE user_id = ?', (user_id,)) as cursor:
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