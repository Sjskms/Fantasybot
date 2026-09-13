#config.py
import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet 

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DB_PATH = os.getenv("DB_PATH", "db.db") 


API_ID = int(os.getenv("API_ID", 0)) 
API_HASH = os.getenv("API_HASH")

# Генерируется один раз и хранится в переменной окружения
FERNET_KEY = os.getenv("FERNET_KEY")

# Если FERNET_KEY не установлен, генерируем его и выводим инструкцию.
# Это должно произойти ТОЛЬКО ОДИН РАЗ при первом запуске!
if not FERNET_KEY:
    new_key = Fernet.generate_key().decode()
    print("\n" + "="*80)
    print("⚠️  ВНИМАНИЕ: Сгенерирован новый FERNET_KEY!  ⚠️")
    print("   СКОПИРУЙТЕ ЭТОТ КЛЮЧ И ДОБАВЬТЕ ЕГО В ВАШ ФАЙЛ .env:")
    print(f"   FERNET_KEY={new_key}")
    print("   ПОСЛЕ ЭТОГО ПЕРЕЗАПУСТИТЕ БОТ!")
    print("="*80 + "\n")
    # Для продакшена или если вы хотите избежать ручного копирования,
    # можно использовать
    os.environ.setdefault("FERNET_KEY", new_key)
    # или записать в .env файл (но это менее безопасно).
    # Лучше всего - остановить выполнение и заставить пользователя добавить ключ.
    exit("FERNET_KEY не найден. Добавьте его в .env и перезапустите бот.")

# Преобразуем ключ в Fernet объект для использования в Database
FERNET_CRYPTO = Fernet(FERNET_KEY.encode())