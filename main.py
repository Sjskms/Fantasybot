# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher

from config import TOKEN, FERNET_KEY
from database import Database
from handlers import admin, user, session_handler, account_login
from middlewares.auth import AuthMiddleware
from services.scheduler import start_scheduler
from services.logging_service import set_logging_bot_instance, load_global_logging_config, log_event

from services.forwarder.supervisor import restore_active_forwarders


async def main():
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )
    
    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    # 1. Загрузка конфигурации логирования и привязка инстанса бота
    await load_global_logging_config()
    set_logging_bot_instance(bot)
    
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
    logging.info("Автопостинг восстановлен.")
    
    # 6. Регистрация роутеров
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(session_handler.router) 
    dp.include_router(account_login.router) 
    logging.info("Зарегистрированы хендлеры.")
    
    # 7. Мидлвари
    dp.message.middleware(AuthMiddleware())
    
    # 8. Лог о включении бота
    await log_event(
        "other",
        "✅ <b>Бот успешно запущен и готов к работе!</b>"
    )

    # 9. Запуск поллинга
    await bot.delete_webhook(drop_pending_updates=False)    
    logging.info("Запускаем опрос бота...")
    await dp.start_polling(bot)


if __name__ == "__main__": 
    asyncio.run(main())