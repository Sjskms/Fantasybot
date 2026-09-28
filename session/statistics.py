# handlers/session/statistics.py
import math
from html import escape
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database import Database
from keyboards.session_kb import get_session_stats_keyboard, get_session_detailed_stats_keyboard
from services.forwarder.engine import calculate_session_stats

router = Router()
CHANNELS_PER_PAGE_STATS = 10

@router.callback_query(F.data.startswith("session_stats_") & ~F.data.startswith("session_stats_detailed_"))
async def session_stats_handler(callback: CallbackQuery):
    session_name = callback.data.removeprefix("session_stats_")
    user_id = callback.from_user.id

    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    forwarded, filtered, errors = calculate_session_stats(session_configs)

    text = (
        f"📊 <b>Статистика сессии:</b> <code>{escape(session_name)}</code>\n\n"
        f"📤 Переслано сообщений: <b>{forwarded}</b>\n"
        f"🚫 Отфильтровано сообщений: <b>{filtered}</b>\n"
        f"❌ Ошибок отправки: <b>{errors}</b>"
    )

    keyboard = get_session_stats_keyboard(session_name)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("session_stats_detailed_") | F.data.startswith("statspage_"))
async def session_detailed_stats_handler(callback: CallbackQuery):
    if callback.data.startswith("statspage_"):
        raw_data = callback.data.removeprefix("statspage_")
        session_name, page_str = raw_data.rsplit("_", 1)
        current_page = int(page_str)
    else:
        session_name = callback.data.removeprefix("session_stats_detailed_")
        current_page = 1

    user_id = callback.from_user.id
    session_configs = await Database.get_session_configs(user_id, session_name) or {}
    channels_dict = session_configs.get("channels", {})

    if not channels_dict:
        text = f"📊 <b>Подробная статистика сессии:</b> <code>{escape(session_name)}</code>\n\n<i>Каналы пока не настроены.</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад к статистике", callback_data=f"session_stats_{session_name}")]])
        return await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

    channel_items = list(channels_dict.items())
    total_channels = len(channel_items)
    total_pages = max(1, math.ceil(total_channels / CHANNELS_PER_PAGE_STATS))
    current_page = max(1, min(current_page, total_pages))

    start_idx = (current_page - 1) * CHANNELS_PER_PAGE_STATS
    end_idx = start_idx + CHANNELS_PER_PAGE_STATS
    page_channels = channel_items[start_idx:end_idx]

    text = (
        f"📊 <b>Подробная статистика по каналам:</b> <code>{escape(session_name)}</code>\n"
        f"<i>Показаны каналы {start_idx + 1}–{min(end_idx, total_channels)} из {total_channels}</i>\n\n"
    )

    for ch_id, ch_data in page_channels:
        title = ch_data.get("title", f"ID {ch_id}")
        stats = ch_data.get("stats", {})
        text += (
            f"📢 <b>{escape(title)}</b> [<code>{ch_id}</code>]\n"
            f"  ├ 📤 Переслано: <b>{stats.get('forwarded', 0)}</b>\n"
            f"  ├ 🚫 Отфильтровано: <b>{stats.get('filtered', 0)}</b>\n"
            f"  └ ❌ Ошибок: <b>{stats.get('errors', 0)}</b>\n\n"
        )

    keyboard = get_session_detailed_stats_keyboard(session_name, current_page, total_pages)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()