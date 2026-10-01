# keyboards/payment_kb.py
import json
import os
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PAYMENT_CONFIG_PATH = "payment.json"
DEFAULT_PAYMENT_CONFIG = {
    "currency": "RUB",
    "tariffs": [
        {"days": 1, "price": 15},
        {"days": 3, "price": 30},
        {"days": 7, "price": 60},
        {"days": 14, "price": 100},
        {"days": 30, "price": 200}
    ],
    "card_details": "<code>2202 2000 0000 0000</code> (Сбербанк / Т-Банк)\nПолучатель: Иван И.",
    "gateways": {
        "cryptobot": True,
        "card_rub": True
    }
}


def load_payment_config() -> dict:
    """Загружает конфигурацию оплаты из payment.json или создает дефолтную."""
    if not os.path.exists(PAYMENT_CONFIG_PATH):
        try:
            with open(PAYMENT_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_PAYMENT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception:
            return DEFAULT_PAYMENT_CONFIG

    try:
        with open(PAYMENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return DEFAULT_PAYMENT_CONFIG
            return data
    except Exception:
        return DEFAULT_PAYMENT_CONFIG


def get_plural_days(days: int) -> str:
    """Склонение слова 'день' (1 день, 3 дня, 5 дней)."""
    if 11 <= days % 100 <= 19:
        return f"{days} дней"
    rem = days % 10
    if rem == 1:
        return f"{days} день"
    if 2 <= rem <= 4:
        return f"{days} дня"
    return f"{days} дней"


def get_tariffs_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с выбором периода подписки (тарифа)."""
    config = load_payment_config()
    tariffs = config.get("tariffs", [])
    currency = config.get("currency", "RUB")
    symbol = "₽" if currency == "RUB" else "USDT"

    rows = []
    for t in tariffs:
        days = t.get("days", 1)
        price = t.get("price", 0)
        text = f"⭐ {get_plural_days(days)} — {price} {symbol}"
        callback_data = f"pay_select_tariff_{days}_{price}_{currency}"
        rows.append([InlineKeyboardButton(text=text, callback_data=callback_data)])

    rows.append([InlineKeyboardButton(text="◀️ В профиль", callback_data="profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_payment_methods_keyboard(days: int, price: float, currency: str) -> InlineKeyboardMarkup:
    """Клавиатура с выбором способа оплаты."""
    config = load_payment_config()
    gateways = config.get("gateways", {})

    rows = []

    if gateways.get("cryptobot", True):
        rows.append([
            InlineKeyboardButton(
                text="🤖 CryptoBot (@CryptoBot)",
                callback_data=f"pay_gw_cb_{days}_{price}_{currency}"
            )
        ])

    if gateways.get("card_rub", True):
        rows.append([
            InlineKeyboardButton(
                text="💳 Карта РФ (СБП / Перевод)",
                callback_data=f"pay_gw_card_{days}_{price}_{currency}"
            )
        ])

    rows.append([
        InlineKeyboardButton(text="◀️ К выбору тарифов", callback_data="payment")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_card_confirmation_keyboard(user_id: int, days: int, payment_id: str) -> InlineKeyboardMarkup:
    """Клавиатура админа для подтверждения/отклонения ручного чека."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить и выдать",
                    callback_data=f"adm_pay_confirm:{user_id}:{days}:{payment_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить платеж",
                    callback_data=f"adm_pay_reject:{user_id}:{payment_id}"
                )
            ]
        ]
    )