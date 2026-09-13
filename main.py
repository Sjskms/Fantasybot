import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import TOKEN, FERNET_KEY
from database import Database
from handlers import admin, user, session_handler,account_login
from middlewares.auth import AuthMiddleware
from services.scheduler import start_scheduler
from my_filters.admin_filter import IsAdmin

from services.Additional_Feature import restore_active_forwarders, set_bot_instance


async def main():
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )
    
    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    # 1. Передаем объект бота для отправки логов пользователям
    set_bot_instance(bot)
    
    # 2. Настройка БД
    await Database.setup()
    logging.info("База данных запущена.")

    # 3. Инициализация шифрования
    Database.setup_encryption(FERNET_KEY)
    logging.info("Шифрование запущено.")
    
    # 4. Запуск планировщика
    await start_scheduler()
    logging.info("Планировщик задач настроен.")

    # 5. Восстановление активных юзерботов автопостинга после перезапуска
    await restore_active_forwarders()
    logging.info("Перессылка запущена..")
    
    # 6. Регистрация роутеров
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(session_handler.router) 
    dp.include_router(account_login.router) 
    logging.info("Зарегистрированы хендлеры.")
    
    # 7. Мидлвари
    dp.message.middleware(AuthMiddleware())
    
    # 8. Запуск поллинга
    await bot.delete_webhook(drop_pending_updates=True)    
    await dp.start_polling(bot)
    logging.info("Запускаем опрос бота...")


if __name__ == "__main__": 
    asyncio.run(main())