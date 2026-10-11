import asyncio
import logging
from aiogram import Bot, Dispatcher

from config import FERNET_KEY, TOKEN
from database import Database
from handlers import admin, admin_limits, user
from middlewares.auth import AuthMiddleware
from payment import router as payment_router
from services.duplicate_detector import init_duplicate_db
from services.forwarder.supervisor import restore_active_forwarders
from services.logging_service import (
    load_global_logging_config,
    log_event,
    set_logging_bot_instance,
)
from services.premium_manager import periodic_premium_cleanup
from services.scheduler import start_scheduler
from session import router as session_router
from handlers import admin_access


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    # 1. Загрузка конфигурации логирования и привязка инстанса бота
    await load_global_logging_config()
    set_logging_bot_instance(bot)

    # 2. Настройка БД и детектора дубликатов
    await Database.setup()
    logging.info("База данных запущена.")
    init_duplicate_db()
    logging.info("База данных анти-повтора инициализирована.")

    # 3. Инициализация шифрования сессий
    Database.setup_encryption(FERNET_KEY)
    logging.info("Шифрование запущено.")

    # 4. Запуск планировщика задач
    await start_scheduler()
    logging.info("Планировщик задач настроен.")

    # 5. Восстановление активных юзерботов автопостинга после перезапуска
    await restore_active_forwarders()
    logging.info("Автопостинг восстановлен.")

    # 6. Регистрация роутеров
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(session_router)
    dp.include_router(admin_limits.router)
    dp.include_router(payment_router)
    dp.include_router(admin_access.router)
    logging.info("Зарегистрированы хендлеры.")

    # 7. Middleware
    dp.message.middleware(AuthMiddleware())

    # 8. Лог успешного включения
    await log_event(
        "other",
        "✅ <b>Бот успешно запущен и готов к работе!</b>",
    )

    # 9. Фоновая проверка и мягкое приведение лимитов при истечении Premium
    asyncio.create_task(periodic_premium_cleanup(bot))

    # 10. Запуск поллинга
    await bot.delete_webhook(drop_pending_updates=False)
    logging.info("Запускаем опрос бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
