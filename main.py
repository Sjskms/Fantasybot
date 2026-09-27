# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import TOKEN, FERNET_KEY
from database import Database
from handlers import admin, user, account_login,admin_limits
from middlewares.auth import AuthMiddleware
from services.scheduler import start_scheduler
from services.logging_service import set_logging_bot_instance, load_global_logging_config, log_event

from handlers.session import router as session_router


import uvicorn
from web_admin import web_admin_app


from services.forwarder.supervisor import restore_active_forwarders
# main.py
import asyncio
from database import Database

async def periodic_premium_cleanup():
    """Фоновая задача: проверяет и очищает истекшие подписки каждые 30 минут."""
    while True:
        try:
            removed_count = await Database.cleanup_expired_premiums()
            if removed_count > 0:
                logging.info(f"🧹 Фоновая очистка: удалено {removed_count} истекших Премиум-подписок.")
        except Exception as e:
            logging.error(f"Ошибка при очистке Премиумов: {e}")
        
        # Пауза 30 минут (1800 секунд)
        await asyncio.sleep(1800)

async def main():
   
    # Запускаем фоновую очистку премиума в asyncio-задаче
    

    # Запуск polling бота...
    await dp.start_polling(bot)
    
    # Запуск FastAPI вместе с Aiogram



    

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
    dp.include_router(session_router)
    dp.include_router(account_login.router) 
    dp.include_router(admin_limits.router) 
    logging.info("Зарегистрированы хендлеры.")
    
    config = uvicorn.Config(
        app=web_admin_app, 
        host="0.0.0.0", 
        port=8080, 
        loop="asyncio"
    )
    server = uvicorn.Server(config)

    # Запускаем сайт и бота параллельно
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot)
    )
    logging.info("Зарегистрирован веб.")
    
    
    # 7. Мидлвари
    dp.message.middleware(AuthMiddleware())
    
    # 8. Лог о включении бота
    await log_event(
        "other",
        "✅ <b>Бот успешно запущен и готов к работе!</b>"
    )
    
    #9
    asyncio.create_task(periodic_premium_cleanup())

    # 10. Запуск поллинга
    await bot.delete_webhook(drop_pending_updates=False)    
    logging.info("Запускаем опрос бота...")
    await dp.start_polling(bot)


if __name__ == "__main__": 
    asyncio.run(main())