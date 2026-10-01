# payment/payment_main.py
import logging
from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from keyboards.payment_kb import (
    get_tariffs_keyboard, 
    get_payment_methods_keyboard,
    get_plural_days,
    load_payment_config,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "payment")
async def show_tariffs_menu(callback: CallbackQuery):
    config = load_payment_config()
    currency = config.get("currency", "RUB").upper()
    curr_name = "рублях" if currency == "RUB" else "USDT"

    text = (
        "⭐ <b>Выбор Премиум-подписки</b>\n\n"
        f"Выберите период подписки (цены указаны в {curr_name}):"
    )

    try:
        await callback.message.edit_text(text, reply_markup=get_tariffs_keyboard(), parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("pay_select_tariff_"))
async def select_payment_gateway_menu(callback: CallbackQuery):
    parts = callback.data.removeprefix("pay_select_tariff_").split("_")
    if len(parts) != 3:
        return await callback.answer("Некорректный тариф", show_alert=True)

    days = int(parts[0])
    price = parts[1]
    currency = parts[2]

    days_text = get_plural_days(days)
    symbol = "₽" if currency == "RUB" else "USDT"

    text = (
        f"⭐ <b>Выбран тариф:</b> {days_text}\n"
        f"💰 <b>Стоимость:</b> {price} {symbol}\n\n"
        "Выберите удобный способ оплаты:"
    )

    kb = get_payment_methods_keyboard(days, price, currency)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()