# payment/payment_CB.py
import logging
import os
import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database import Database
from keyboards.payment_kb import get_plural_days, load_payment_config
from services.logging_service import log_event

router = Router()
logger = logging.getLogger(__name__)

CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "").strip()
CRYPTOBOT_API_BASE = "https://pay.crypt.bot/api"


def _currency_code(config_currency: str) -> str:
    currency = config_currency.upper()
    if currency in {"RUB", "RUR", "RUBLE", "RUBLES"}:
        return "RUB"
    if currency == "USDT":
        return "USDT"
    raise ValueError("Поддерживаются только RUB и USDT.")


def _amount_string(value) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Некорректная сумма тарифа.")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Сумма должна быть положительной.")
    return format(amount.normalize(), "f")


async def _crypto_pay_request(method: str, payload: dict) -> dict:
    if not CRYPTOBOT_API_TOKEN:
        raise RuntimeError("Не настроен CRYPTOBOT_API_TOKEN в окружении.")

    import aiohttp
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{CRYPTOBOT_API_BASE}/{method}",
            json=payload,
            headers=headers,
        ) as response:
            data = await response.json(content_type=None)
            if response.status != 200 or not data.get("ok"):
                raise RuntimeError(f"Crypto Pay API error: {data}")
            return data["result"]


@router.callback_query(F.data.startswith("pay_gw_cb_"))
async def create_cryptobot_invoice(callback: CallbackQuery):
    parts = callback.data.removeprefix("pay_gw_cb_").split("_")
    if len(parts) != 3:
        return await callback.answer("Некорректные параметры тарифа", show_alert=True)

    try:
        days = int(parts[0])
        price = _amount_string(parts[1])
        currency = _currency_code(parts[2])
    except (ValueError, IndexError) as error:
        return await callback.answer(str(error), show_alert=True)

    config = load_payment_config()
    configured_currency = _currency_code(config.get("currency", "RUB"))
    tariff_exists = any(
        int(item.get("days", -1)) == days and _amount_string(item.get("price", 0)) == price
        for item in config.get("tariffs", [])
    )
    if currency != configured_currency or not tariff_exists:
        return await callback.answer("Тариф изменился. Обновите меню.", show_alert=True)

    await callback.answer("Создаю счет в CryptoBot...")
    payload_id = secrets.token_urlsafe(16)
    payload = f"premium:{callback.from_user.id}:{days}:{payload_id}"

    try:
        invoice = await _crypto_pay_request(
            "createInvoice",
            {
                "currency_type": "fiat" if currency == "RUB" else "crypto",
                "fiat": "RUB" if currency == "RUB" else None,
                "asset": "USDT" if currency == "USDT" else None,
                "amount": price,
                "description": f"Премиум: {get_plural_days(days)}",
                "payload": payload,
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )
    except Exception:
        logger.exception("Не удалось создать счет в CryptoBot")
        return await callback.message.answer("⚠️ Не удалось связаться с @CryptoBot. Попробуйте позже.")

    invoice_id = int(invoice["invoice_id"])
    invoice_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    if not invoice_url:
        return await callback.message.answer("⚠️ Платежный сервис не вернул ссылку на оплату.")

    # 📝 ЛОГИРОВАНИЕ: Создан счет (незавершенный платеж)
    await log_event(
        "payment_pending",
        f"🤖 <b>Создан счет в CryptoBot</b>\n"
        f"💰 Сумма: <code>{price} {currency}</code> | Тариф: <b>{get_plural_days(days)}</b>\n"
        f"🆔 Инвойс: <code>{invoice_id}</code>",
        user_id=callback.from_user.id,
        payment_method="cryptobot"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить в CryptoBot", url=invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"pay_check_cb_{invoice_id}")],
            [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="payment")],
        ]
    )
    await callback.message.edit_text(
        f"🤖 <b>Счет через CryptoBot создан</b>\n\n"
        f"Тариф: <b>{get_plural_days(days)}</b>\n"
        f"Сумма к оплате: <b>{price} {currency}</b>\n\n"
        "После успешной оплаты нажмите кнопку <b>«🔄 Проверить оплату»</b> ниже.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pay_check_cb_"))
async def verify_cryptobot_invoice(callback: CallbackQuery):
    try:
        invoice_id = int(callback.data.removeprefix("pay_check_cb_"))
    except ValueError:
        return await callback.answer("Неверный номер счета", show_alert=True)

    try:
        result = await _crypto_pay_request("getInvoices", {"invoice_ids": str(invoice_id)})
        invoices = result.get("items", [])
        invoice = next((item for item in invoices if int(item["invoice_id"]) == invoice_id), None)
    except Exception:
        logger.exception("Ошибка проверки счета в CryptoBot: invoice_id=%s", invoice_id)
        return await callback.answer("Не удалось проверить статус платежа. Попробуйте позже.", show_alert=True)

    if not invoice:
        return await callback.answer("Счет не найден в системе.", show_alert=True)

    payload = invoice.get("payload", "")
    try:
        _, raw_user_id, raw_days, _ = payload.split(":", 3)
        user_id = int(raw_user_id)
        days = int(raw_days)
    except (ValueError, AttributeError):
        return await callback.answer("Ошибка данных счета.", show_alert=True)

    if user_id != callback.from_user.id:
        return await callback.answer("Этот счет принадлежит другому пользователю.", show_alert=True)

    if invoice.get("status") != "paid":
        return await callback.answer("❌ Оплата еще не поступила. Попробуйте через минуту.", show_alert=True)

    try:
        already_processed = await Database.is_payment_processed(str(invoice_id))
        if already_processed:
            return await callback.answer("Этот платеж уже был зачислен ранее!", show_alert=True)

        price = float(invoice.get("amount", 0))
        currency = invoice.get("asset") or invoice.get("fiat") or "RUB"

        end_date = await Database.add_premium(user_id, days)
        await Database.mark_payment_processed(
            payment_id=str(invoice_id),
            user_id=user_id,
            days=days,
            amount=price,
            currency=currency,
            method="cryptobot"
        )

        # 📝 ЛОГИРОВАНИЕ: Успешная оплата CryptoBot
        await log_event(
            "payment_success",
            f"✅ <b>Успешная оплата CryptoBot!</b>\n"
            f"💰 Зачислено: <code>{price} {currency}</code> | Выдано: <b>{get_plural_days(days)}</b>\n"
            f"📅 Активен до: <b>{end_date}</b>",
            user_id=user_id,
            payment_method="cryptobot"
        )

    except Exception:
        logger.exception("Ошибка выдачи премиума по счету %s", invoice_id)
        return await callback.answer("Платеж подтвержден, но произошла ошибка при выдаче Premium. Обратитесь к админу.", show_alert=True)

    await callback.message.edit_text(
        f"✅ <b>Оплата успешно получена!</b>\n\n"
        f"Вам начислен Премиум на <b>{get_plural_days(days)}</b>.\n"
        f"Срок действия до: <b>{end_date}</b>",
        parse_mode="HTML",
    )
    await callback.answer("Премиум успешно активирован!")