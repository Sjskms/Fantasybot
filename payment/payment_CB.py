# payment/payment_CB.py
import asyncio
import logging
import os
import secrets
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database import Database
from keyboards.payment_kb import get_plural_days, load_payment_config
router = Router()
logger = logging.getLogger(__name__)

# Configure CRYPTOBOT_API_TOKEN in environment/.env.
# Use @CryptoBot's official Crypto Pay API token, not the Telegram bot token.
CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "").strip()
CRYPTOBOT_API_BASE = "https://pay.crypt.bot/api"

# In-memory invoice mapping; invoice payload also carries user/days for restart recovery.
pending_invoice_payloads: dict[str, dict] = {}


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
        raise ValueError("Сумма тарифа должна быть положительным числом.")
    return format(amount.normalize(), "f")


async def _crypto_pay_request(method: str, payload: dict) -> dict:
    """Вызов официального Crypto Pay API через aiohttp."""
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


@router.callback_query(F.data == "payment")
async def show_premium_tariffs(callback: CallbackQuery):
    config = load_payment_config()
    currency = config.get("currency", "RUB").upper()
    currency_label = "рублях" if currency == "RUB" else "USDT"
    text = (
        "⭐ <b>Премиум-подписка</b>\n\n"
        f"Выберите срок подписки. Оплата принимается в {currency_label}."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_payment_keyboard(),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()





@router.callback_query(F.data.startswith("pay_gw_cb_"))  # 👈 ИСПРАВЛЕНО ЗДЕСЬ
async def create_premium_invoice(callback: CallbackQuery):
    # Убираем префикс pay_gw_cb_ вместо pay_tariff_
    parts = callback.data.removeprefix("pay_gw_cb_").split("_")
    if len(parts) != 3:
        await callback.answer("Некорректный тариф. Обновите меню оплаты.", show_alert=True)
        return

    try:
        days = int(parts[0])
        price = _amount_string(parts[1])
        currency = _currency_code(parts[2])
    except (ValueError, IndexError) as error:
        await callback.answer(str(error), show_alert=True)
        return

    # Validate against the current server-side tariff config; never trust callback values alone.
    config = load_payment_config()
    configured_currency = _currency_code(config.get("currency", "RUB"))
    tariff_exists = any(
        int(item.get("days", -1)) == days
        and _amount_string(item.get("price", 0)) == price
        for item in config.get("tariffs", [])
    )
    if currency != configured_currency or not tariff_exists:
        await callback.answer("Тариф изменился. Откройте меню оплаты заново.", show_alert=True)
        return

    await callback.answer("Создаю счет...")
    payload_id = secrets.token_urlsafe(18)
    payload = f"premium:{callback.from_user.id}:{days}:{payload_id}"
    pending_invoice_payloads[payload] = {
        "user_id": callback.from_user.id,
        "days": days,
        "currency": currency,
        "price": price,
    }

    try:
        invoice = await _crypto_pay_request(
            "createInvoice",
            {
                "currency_type": "fiat" if currency == "RUB" else "crypto",
                "fiat": "RUB" if currency == "RUB" else None,
                "asset": "USDT" if currency == "USDT" else None,
                "amount": price,
                "description": f"Premium: {get_plural_days(days)}",
                "payload": payload,
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )
    except Exception as error:
        pending_invoice_payloads.pop(payload, None)
        logger.exception("Crypto Pay invoice creation failed")
        await callback.message.answer(
            "Не удалось создать счет. Попробуйте позже или обратитесь к администратору."
        )
        return

    invoice_id = int(invoice["invoice_id"])
    invoice_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
    if not invoice_url:
        pending_invoice_payloads.pop(payload, None)
        await callback.message.answer("Платежный сервис не вернул ссылку на оплату.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"pay_check_{invoice_id}")],
            [InlineKeyboardButton(text="◀️ К тарифам", callback_data="payment")],
        ]
    )
    await callback.message.edit_text(
        f"🧾 <b>Счет создан</b>\n\n"
        f"Тариф: <b>{get_plural_days(days)}</b>\n"
        f"Сумма: <b>{price} {currency}</b>\n"
        "После оплаты нажмите «Проверить оплату». Счет действует 1 час.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pay_check_"))
async def check_premium_invoice(callback: CallbackQuery):
    try:
        invoice_id = int(callback.data.removeprefix("pay_check_"))
    except ValueError:
        await callback.answer("Некорректный номер счета.", show_alert=True)
        return

    try:
        result = await _crypto_pay_request(
            "getInvoices",
            {"invoice_ids": str(invoice_id)},
        )
        invoices = result.get("items", [])
        invoice = next((item for item in invoices if int(item["invoice_id"]) == invoice_id), None)
    except Exception:
        logger.exception("Crypto Pay invoice lookup failed: invoice_id=%s", invoice_id)
        await callback.answer("Не удалось проверить платеж. Попробуйте позже.", show_alert=True)
        return

    if not invoice:
        await callback.answer("Счет не найден.", show_alert=True)
        return

    # Payload is the authority linking the invoice to its purchaser/tariff.
    payload = invoice.get("payload", "")
    try:
        prefix, raw_user_id, raw_days, _ = payload.split(":", 3)
        user_id = int(raw_user_id)
        days = int(raw_days)
    except (ValueError, AttributeError):
        await callback.answer("Некорректные данные счета. Обратитесь к администратору.", show_alert=True)
        return

    if user_id != callback.from_user.id:
        await callback.answer("Этот счет создан для другого пользователя.", show_alert=True)
        return

    if invoice.get("status") != "paid":
        await callback.answer("Оплата пока не найдена. После оплаты проверьте снова.", show_alert=True)
        return

    # Idempotency: an invoice is granted once. DB helper should atomically record invoice_id.
    try:
        already_processed = await Database.is_payment_processed(str(invoice_id))
        if already_processed:
            await callback.answer("Этот платеж уже учтен.", show_alert=True)
            return
        end_date = await Database.add_premium(user_id, days)
        await Database.mark_payment_processed(str(invoice_id), user_id, days)
    except AttributeError:
        logger.exception("Payment idempotency DB methods are missing")
        await callback.answer(
            "Оплата подтверждена, но запись платежа не настроена. Не повторяйте проверку; обратитесь к администратору.",
            show_alert=True,
        )
        return
    except Exception:
        logger.exception("Failed to grant premium: invoice_id=%s user_id=%s", invoice_id, user_id)
        await callback.answer("Платеж подтвержден, но не удалось выдать Premium. Обратитесь к администратору.", show_alert=True)
        return

    pending_invoice_payloads.pop(payload, None)
    await callback.message.edit_text(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"Премиум продлен на <b>{get_plural_days(days)}</b>.\n"
        f"Доступен до: <b>{end_date}</b>",
        parse_mode="HTML",
    )
    await callback.answer("Premium активирован!")
