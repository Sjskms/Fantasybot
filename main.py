import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import TOKEN, FERNET_KEY,ADMIN_ID
from database import Database

from handlers import (
    admin,
    user,
    session_handler,
    account_login,
)

from middlewares.auth import AuthMiddleware
from services.scheduler import start_scheduler

from services.Additional_Feature import (
    restore_active_forwarders,
    set_bot_instance,
)


try:
    from config import ADMIN_IDS
except ImportError:
    ADMIN_IDS = [ADMIN_ID]


async def notify_admins(bot: Bot, text: str) -> None:
    """
    Отправляет уведомление всем администраторам.
    Ошибка отправки одному администратору не остановит запуск бота.
    """
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )

            logging.info(
                "Уведомление отправлено администратору: %s",
                admin_id,
            )

        except Exception:
            logging.exception(
                "Не удалось отправить уведомление администратору: %s",
                admin_id,
            )


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s - %(levelname)s - "
            "%(name)s - %(message)s"
        ),
    )

    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    try:
        # Передаём объект Aiogram-бота в Additional_Feature.py,
        # чтобы работала отправка логов пользователям.
        set_bot_instance(bot)

        # Инициализация базы данных
        await Database.setup()
        logging.info("База данных запущена.")

        # Инициализация шифрования Fernet
        Database.setup_encryption(FERNET_KEY)
        logging.info("Шифрование запущено.")

        # Запуск планировщика
        await start_scheduler()
        logging.info("Планировщик задач настроен.")

        # Восстановление активных юзербот-сессий
        await restore_active_forwarders()
        logging.info("Автопостинг активных сессий восстановлен.")

        # Регистрация роутеров
        dp.include_router(admin.router)
        dp.include_router(user.router)
        dp.include_router(session_handler.router)
        dp.include_router(account_login.router)

        logging.info("Хендлеры зарегистрированы.")

        # Подключение middleware
        dp.message.middleware(AuthMiddleware())

        # ВАЖНО:
        # False сохраняет накопленные обновления.
        # Благодаря этому бот обработает команды и сообщения,
        # которые пришли во время его выключения.
        await bot.delete_webhook(
            drop_pending_updates=False,
        )

        # Уведомляем администраторов перед началом polling
        await notify_admins(
            bot,
            (
                "✅ <b>Бот включён</b>\n\n"
                "База данных подключена.\n"
                "Шифрование запущено.\n"
                "Планировщик задач работает.\n"
                "Ожидание команд и сообщений запущено."
            ),
        )

        logging.info("Запускаем опрос бота...")

        # Бот начнёт обрабатывать накопленные обновления,
        # а затем перейдёт к новым.
        await dp.start_polling(bot)

    except asyncio.CancelledError:
        logging.info("Работа бота остановлена.")

    except Exception:
        logging.exception("Критическая ошибка при запуске или работе бота.")
        raise

    finally:
        # Корректно закрываем HTTP-сессию Aiogram
        await bot.session.close()
        logging.info("Сессия Aiogram закрыта.")


if __name__ == "__main__":
    asyncio.run(main())