# payment/payment_admin.py
import logging
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from database import Database
from keyboards.payment_kb import load_payment_config, save_payment_config, get_plural_days
from my_filters.admin_filter import IsAdmin

router = Router()
logger = logging.getLogger(__name__)

class AdminPaymentStates(StatesGroup):
    waiting_for_new_price = State()
    waiting_for_add_days = State()
    waiting_for_add_price = State()
    waiting_for_card_details = State()

# --- ОСНОВНОЕ МЕНЮ НАСТРОЕК ОПЛАТЫ ---
@router.callback_query(F.data == "payment_settings", IsAdmin())
async def admin_payment_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    config = load_payment_config()
    currency = config.get("currency", "RUB")
    gateways = config.get("gateways", {"cryptobot": True, "card_rub": True})

    cb_on = gateways.get("cryptobot", True)
    card_on = gateways.get("card_rub", True)

    text = (
        "🌟 <b>Управление платежной системой</b>\n\n"
        f"Валюта тарифов: <b>{currency}</b>\n\n"
        "<b>Статус платежных шлюзов:</b>\n"
        f"├ 🤖 CryptoBot: {'✅ Включен' if cb_on else '❌ Выключен'}\n"
        f"└ 💳 Карта РФ: {'✅ Включен' if card_on else '❌ Выключен'}\n\n"
        "<i>Нажмите на тумблер шлюза, чтобы включить или отключить его для пользователей:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"CryptoBot: {'ВКЛ ✅' if cb_on else 'ВЫКЛ ❌'}",
                callback_data="adm_toggle_gw:cryptobot"
            ),
            InlineKeyboardButton(
                text=f"Карта РФ: {'ВКЛ ✅' if card_on else 'ВЫКЛ ❌'}",
                callback_data="adm_toggle_gw:card_rub"
            ),
        ],
        [InlineKeyboardButton(text="📊 Управление тарифами (Дни/Цены)", callback_data="adm_pay_tariffs")],
        [InlineKeyboardButton(text="💳 Изменить реквизиты карты", callback_data="adm_pay_card")],
        [InlineKeyboardButton(text="📈 Статистика платежей", callback_data="adm_pay_stats")],
        [InlineKeyboardButton(text="◀️ В админ-панель", callback_data="admin_panel")]
    ])

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- ТУМБЛЕРЫ ВКЛЮЧЕНИЯ/ВЫКЛЮЧЕНИЯ ШЛЮЗОВ ---
@router.callback_query(F.data.startswith("adm_toggle_gw:"), IsAdmin())
async def toggle_payment_gateway(callback: CallbackQuery, state: FSMContext):
    gateway_key = callback.data.removeprefix("adm_toggle_gw:")
    config = load_payment_config()
    gateways = config.setdefault("gateways", {"cryptobot": True, "card_rub": True})

    # Переключаем статус
    gateways[gateway_key] = not gateways.get(gateway_key, True)
    save_payment_config(config)

    gw_name = "CryptoBot" if gateway_key == "cryptobot" else "Карта РФ"
    status_str = "включен ✅" if gateways[gateway_key] else "отключен ❌"
    await callback.answer(f"Шлюз {gw_name} {status_str}!")

    # Перерисовываем меню
    await admin_payment_main(callback, state)


# --- СТАТИСТИКА ПЛАТЕЖЕЙ И ПРОДАЖ ---
@router.callback_query(F.data == "adm_pay_stats", IsAdmin())
async def show_payment_statistics(callback: CallbackQuery):
    stats = await Database.get_payment_stats()
    cb = stats["cb"]
    card = stats["card"]

    text = (
        "📊 <b>Детальная статистика платежей</b>\n\n"
        f"👥 Уникальных клиентов: <b>{stats['unique_buyers']} чел.</b>\n"
        f"⏳ Выдано подписок суммарно: <b>{stats['total_days']} дн.</b>\n\n"
        "📦 <b>Динамика количества заказов:</b>\n"
        f"  ├ За сегодня: <b>+{stats['today_count']} шт.</b>\n"
        f"  ├ За 7 дней (неделя): <b>+{stats['week_count']} шт.</b>\n"
        f"  ├ За 30 дней (месяц): <b>+{stats['month_count']} шт.</b>\n"
        f"  └ За всё время: <b>{stats['total_count']} шт.</b>\n\n"
        "🤖 <b>CryptoBot (@CryptoBot):</b>\n"
        f"  ├ Сегодня: <b>{cb['today']['sum']} ₽</b> ({cb['today']['cnt']} шт.)\n"
        f"  ├ За 7 дней: <b>{cb['week']['sum']} ₽</b> ({cb['week']['cnt']} шт.)\n"
        f"  ├ За 30 дней: <b>{cb['month']['sum']} ₽</b> ({cb['month']['cnt']} шт.)\n"
        f"  └ Всё время: <b>{cb['all']['sum']} ₽</b> ({cb['all']['cnt']} шт.)\n\n"
        "💳 <b>Карты РФ (СБП / Перевод):</b>\n"
        f"  ├ Сегодня: <b>{card['today']['sum']} ₽</b> ({card['today']['cnt']} шт.)\n"
        f"  ├ За 7 дней: <b>{card['week']['sum']} ₽</b> ({card['week']['cnt']} шт.)\n"
        f"  ├ За 30 дней: <b>{card['month']['sum']} ₽</b> ({card['month']['cnt']} шт.)\n"
        f"  └ Всё время: <b>{card['all']['sum']} ₽</b> ({card['all']['cnt']} шт.)"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adm_pay_stats")],
        [InlineKeyboardButton(text="◀️ Назад в настройки оплаты", callback_data="payment_settings")]
    ])

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- УПРАВЛЕНИЕ РЕКВИЗИТАМИ КАРТЫ ---
@router.callback_query(F.data == "adm_pay_card", IsAdmin())
async def admin_edit_card_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    config = load_payment_config()
    current_details = config.get("card_details", "Не указаны")

    text = (
        "💳 <b>Настройка реквизитов карты РФ</b>\n\n"
        f"Текущие реквизиты:\n{current_details}\n\n"
        "Нажмите кнопку ниже, чтобы изменить их:"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст реквизитов", callback_data="adm_pay_card_edit")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_settings")]
    ])

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "adm_pay_card_edit", IsAdmin())
async def admin_prompt_card_details(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminPaymentStates.waiting_for_card_details)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_pay_card")]
    ])

    await callback.message.edit_text(
        "✍️ Отправьте новые реквизиты для перевода (можно использовать HTML):\n\n"
        "<i>Пример:\n<code>2202 2000 0000 0000</code> (Сбербанк / Т-Банк)\nПолучатель: Иван И.</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminPaymentStates.waiting_for_card_details, IsAdmin())
async def admin_save_card_details(message: Message, state: FSMContext):
    new_details = message.html_text or message.text
    if not new_details.strip():
        return await message.answer("⚠️ Реквизиты не могут быть пустыми. Попробуйте снова.")

    config = load_payment_config()
    config["card_details"] = new_details
    save_payment_config(config)
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К настройкам оплаты", callback_data="payment_settings")]
    ])
    await message.answer(
        f"✅ <b>Реквизиты успешно обновлены!</b>\n\nНовые данные:\n{new_details}",
        reply_markup=kb,
        parse_mode="HTML"
    )


# --- СПИСОК ТАРИФОВ ---
@router.callback_query(F.data == "adm_pay_tariffs", IsAdmin())
async def admin_tariffs_list(callback: CallbackQuery):
    config = load_payment_config()
    tariffs = config.get("tariffs", [])
    currency = "₽" if config.get("currency") == "RUB" else "USDT"

    rows = []
    for t in tariffs:
        days = t['days']
        price = t['price']
        rows.append([
            InlineKeyboardButton(text=f"📝 {get_plural_days(days)}: {price} {currency}", callback_data=f"adm_pay_edit:{days}"),
            InlineKeyboardButton(text="❌", callback_data=f"adm_pay_del:{days}")
        ])

    rows.append([InlineKeyboardButton(text="➕ Добавить новый тариф", callback_data="adm_pay_add")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="payment_settings")])

    try:
        await callback.message.edit_text(
            "📋 <b>Список текущих тарифов:</b>\nНажмите на тариф для изменения цены или на крестик для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- УДАЛЕНИЕ ТАРИФА ---
@router.callback_query(F.data.startswith("adm_pay_del:"), IsAdmin())
async def admin_delete_tariff(callback: CallbackQuery):
    days_to_del = int(callback.data.split(":")[1])
    config = load_payment_config()
    config["tariffs"] = [t for t in config["tariffs"] if t["days"] != days_to_del]
    save_payment_config(config)
    await callback.answer(f"Тариф на {days_to_del} дн. удален")
    await admin_tariffs_list(callback)


# --- ИЗМЕНЕНИЕ ЦЕНЫ (ШАГ 1) ---
@router.callback_query(F.data.startswith("adm_pay_edit:"), IsAdmin())
async def admin_edit_price_start(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split(":")[1])
    await state.update_data(edit_days=days)
    await state.set_state(AdminPaymentStates.waiting_for_new_price)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_pay_tariffs")]])
    try:
        await callback.message.edit_text(
            f"✍️ Введите новую цену для тарифа <b>{get_plural_days(days)}</b>:",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- ИЗМЕНЕНИЕ ЦЕНЫ (ШАГ 2) ---
@router.message(AdminPaymentStates.waiting_for_new_price, IsAdmin())
async def admin_edit_price_save(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Введите целое число.")

    new_price = int(message.text)
    data = await state.get_data()
    days = data['edit_days']

    config = load_payment_config()
    for t in config["tariffs"]:
        if t["days"] == days:
            t["price"] = new_price
            break

    save_payment_config(config)
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку тарифов", callback_data="adm_pay_tariffs")]])
    await message.answer(f"✅ Цена для {get_plural_days(days)} изменена на {new_price} ₽.", reply_markup=kb)


# --- ДОБАВЛЕНИЕ ТАРИФА (ШАГ 1: Дни) ---
@router.callback_query(F.data == "adm_pay_add", IsAdmin())
async def admin_add_tariff_days(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminPaymentStates.waiting_for_add_days)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="adm_pay_tariffs")]])
    try:
        await callback.message.edit_text("🔢 Введите количество дней для нового тарифа:", reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


# --- ДОБАВЛЕНИЕ ТАРИФА (ШАГ 2: Цена) ---
@router.message(AdminPaymentStates.waiting_for_add_days, IsAdmin())
async def admin_add_tariff_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Введите число дней.")
    await state.update_data(add_days=int(message.text))
    await state.set_state(AdminPaymentStates.waiting_for_add_price)
    await message.answer("💰 Теперь введите цену для этого тарифа (в рублях):")


# --- ДОБАВЛЕНИЕ ТАРИФА (ШАГ 3: Сохранение) ---
@router.message(AdminPaymentStates.waiting_for_add_price, IsAdmin())
async def admin_add_tariff_final(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("⚠️ Введите числовую цену.")

    price = int(message.text)
    data = await state.get_data()
    days = data['add_days']

    config = load_payment_config()
    config["tariffs"] = [t for t in config["tariffs"] if t["days"] != days]
    config["tariffs"].append({"days": days, "price": price})
    config["tariffs"].sort(key=lambda x: x["days"])

    save_payment_config(config)
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ К списку тарифов", callback_data="adm_pay_tariffs")]])
    await message.answer(f"✅ Успешно добавлен тариф: {get_plural_days(days)} за {price} ₽.", reply_markup=kb)