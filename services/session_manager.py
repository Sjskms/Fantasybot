# services/session_manager.py
from pyrogram import Client
from database import Database

class SessionManager:
    _instance = None
    clients = {}  # Здесь будут храниться активные объекты Client

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def get_or_create_client(cls, user_id: int, session_name: str, api_id: int, api_hash: str):
        """
        Возвращает активный клиент. Если он не запущен — запускает его.
        """
        client_key = f"{user_id}_{session_name}"
        
        if client_key not in cls.clients:
            # Получаем зашифрованную строку из БД и расшифровываем (предполагаем, что Database.get_user_session это делает)
            session_string = await Database.get_user_session(user_id, session_name)
            
            if not session_string:
                return None

            app = Client(
                name=f"session_{user_id}_{session_name}",
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string
            )
            await app.start()
            cls.clients[client_key] = app
            
        return cls.clients[client_key]

    @classmethod
    async def stop_client(cls, user_id: int, session_name: str):
        client_key = f"{user_id}_{session_name}"
        if client_key in cls.clients:
            await cls.clients[client_key].stop()
            del cls.clients[client_key]