import asyncio
import logging
from aiogram import Bot

from database import Database
from services.config_service import get_user_limits
from services.forwarder.state import active_forwarder_tasks

logger = logging.getLogger(__name__)


async def enforce_free_limits_for_user(bot: Bot, user_id: int):
    """
    Приводит сессии и каналы пользователя к лимитам бесплатного тарифа (Free).
    Сессии НЕ удаляются безвозвратно: лишние 'замораживаются' (останавливается пересылка),
    а в активных сессиях лишние каналы деактивируются без удаления настроек.
    """
    limits = await get_user_limits(user_id)
    max_sessions = limits.get("max_sessions", 1)
    max_export = limits.get("max_export_channels", 2)
    max_post = limits.get("max_post_channels", 1)

    # 1. Получаем все сессии пользователя
    user_sessions = await Database.get_user_sessions(user_id)
    if not user_sessions:
        return

    # Сессии представлены как [(session_name, ...), ...] или [session_name, ...]
    session_names = [
        s[0] if isinstance(s, (list, tuple)) else s
        for s in user_sessions
    ]

    active_sessions = session_names[:max_sessions]
    overflow_sessions = session_names[max_sessions:]

    frozen_sessions_count = 0

    # 2. 'Замораживаем' сессии, превышающие лимит Free:
    # Останавливаем запущенные таски автопостинга и отключаем статус posting
    for s_name in overflow_sessions:
        task_key = (user_id, s_name)
        if task_key in active_forwarder_tasks:
            try:
                task = active_forwarder_tasks.pop(task_key)
                task.cancel()
                logger.info("Остановлена пересылка для замороженной сессии %s (пользователь %s)", s_name, user_id)
            except Exception as e:
                logger.debug("Ошибка остановки таски: %s", e)

        # Выключаем флаг posting в сохраненной конфигурации сессии
        try:
            cfg = await Database.get_session_configs(user_id, s_name) or {}
            cfg["posting_enabled"] = False
            await Database.update_session_configs(user_id, s_name, cfg)
            frozen_sessions_count += 1
        except Exception as e:
            logger.debug("Ошибка обновления конфига сессии %s: %s", s_name, e)

    trimmed_channels_count = 0

    # 3. Приводим каналы в оставшихся активных сессиях к лимитам Free:
    for s_name in active_sessions:
        try:
            cfg = await Database.get_session_configs(user_id, s_name) or {}
            channels = cfg.get("channels", {})
            if not isinstance(channels, dict):
                continue

            active_exports = []
            active_posts = []

            for ch_id, ch_data in channels.items():
                modes = ch_data.get("modes", {})
                if modes.get("export", {}).get("enabled", False):
                    active_exports.append(ch_id)
                if modes.get("post", {}).get("enabled", False):
                    active_posts.append(ch_id)

            changed = False

            # Если каналов экспорта больше лимита — отключаем лишние (но не удаляем из списка)
            if len(active_exports) > max_export:
                for ch_id in active_exports[max_export:]:
                    channels[ch_id]["modes"]["export"]["enabled"] = False
                    trimmed_channels_count += 1
                    changed = True

            # Если каналов постинга больше лимита — отключаем лишние
            if len(active_posts) > max_post:
                for ch_id in active_posts[max_post:]:
                    channels[ch_id]["modes"]["post"]["enabled"] = False
                    trimmed_channels_count += 1
                    changed = True

            if changed:
                cfg["channels"] = channels
                await Database.update_session_configs(user_id, s_name, cfg)

        except Exception as e:
            logger.error("Ошибка обрезки каналов для сессии %s: %s", s_name, e)

    # 4. Уведомляем пользователя об окончании подписки и корректировке лимитов
    try:
        msg = (
            "⏳ <b>Ваша подписка Premium закончилась!</b>\n\n"
            "Ваш аккаунт переведен на стандартные лимиты <b>Free</b>:\n"
            f"• Доступных сессий: <b>{max_sessions}</b>\n"
            f"• Каналов экспорта: <b>{max_export}</b>\n"
            f"• Каналов постинга: <b>{max_post}</b>\n\n"
        )
        if frozen_sessions_count > 0:
            msg += f"⏸ <i>Заморожено сессий: {frozen_sessions_count} (они сохранены, но пересылка остановлена).</i>\n"
        if trimmed_channels_count > 0:
            msg += f"ℹ️ <i>Отключено лишних каналов: {trimmed_channels_count}.</i>\n"

        msg += "\n⭐ Продлите Premium, чтобы моментально разблокировать все функции без повторной настройки!"
        await bot.send_message(chat_id=user_id, text=msg, parse_mode="HTML")
    except Exception as e:
        logger.debug("Не удалось отправить уведомление пользователю %s: %s", user_id, e)


async def periodic_premium_cleanup(bot: Bot):
    """
    Фоновая задача: проверяет и очищает истекшие подписки каждые 15-30 минут,
    после чего автоматически возвращает аккаунты пользователей к бесплатным лимитам.
    """
    while True:
        try:
            expired_users = await Database.get_expired_premium_user_ids()
            if expired_users:
                removed_count = await Database.cleanup_expired_premiums()
                logger.info("🧹 Очистка: сброшен Premium у %s пользователей", removed_count)

                for uid in expired_users:
                    try:
                        await enforce_free_limits_for_user(bot, uid)
                    except Exception as err:
                        logger.error("Ошибка приведения лимитов для %s: %s", uid, err)
        except Exception as e:
            logger.error("Ошибка в периодической очистке премиумов: %s", e)

        # Интервал проверки: 10 минут (600 секунд)
        await asyncio.sleep(600)
