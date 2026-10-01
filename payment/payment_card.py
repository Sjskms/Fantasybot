# payment/payment_card.py
import logging
import secrets
from html import escape

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from database import Database
from keyboards.payment_kb import (
    load_payment_config,
    get_plural_days,
    get_admin_card_confirmation_keyboard,
)
from services.logging_service import load_global_logging_config
from config import ADMIN_ID

card_name ="+799999999 Альфа Наталья"

router = Router()
logger = logging.getLogger(__name__)


class CardPaymentStates(StatesGroup):
    waiting_for_receipt = State()


@router.callback_query(F.data.startswith("pay_gw_card_"))
async def show_card_payment_details(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.removeprefix("pay_gw_card_").split("_")
    if len(parts) != 3:
        return await callback.answer("Ошибка параметров тарифа", show_alert=True)

    days = int(parts[0])
    price = parts[1]

    config = load_payment_config()
    card_details = config.get("card_details", card_name)

    await state.update_data(
        card_pay_days=days,
        card_pay_price=price,
    )
    await state.set_state(CardPaymentStates.waiting_for_receipt)

    text = (
        f"💳 <b>Оплата банковской картой РФ</b>\n\n"
        f"Тариф: <b>{get_plural_days(days)}</b>\n"
        f"Сумма к переводу: <b>{price} ₽</b>\n\n"
        f"📌 <b>Реквизиты для перевода:</b>\n"
        f"{card_details}\n\n"
        "ℹ️ <b>Инструкция:</b>\n"
        "1. Совершите перевод на указанную сумму.\n"
        "2. Отправьте в этот чат <b>скриншот/фото чека или PDF-документ</b>.\n\n"
        "<i>Ожидаю чек об оплате...</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="payment")]
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(CardPaymentStates.waiting_for_receipt, F.photo | F.document)
async def process_user_receipt_upload(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    days = data.get("card_pay_days", 1)
    price = data.get("card_pay_price", 0)
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "нет"

    if message.document:
        mime = message.document.mime_type or ""
        file_name = (message.document.file_name or "").lower()
        if not (mime == "application/pdf" or file_name.endswith((".pdf", ".jpg", ".jpeg", ".png"))):
            return await message.answer("⚠️ Пожалуйста, отправьте чек в виде <b>фотографии</b> или <b>PDF-файла</b>.")

    await state.clear()
    payment_id = secrets.token_hex(6)

    logging_cfg = await load_global_logging_config()
    target_chat = logging_cfg.get("telegram_log_chat_id")
    admin_chat_ids = [ADMIN_ID]

    admin_caption = (
        f"🧾 <b>Новый чек на оплату картой!</b>\n\n"
        f"👤 <b>Пользователь:</b> {escape(user_name)} (ID: <code>{user_id}</code>, {username})\n"
        f"⭐ <b>Тариф:</b> {get_plural_days(days)}\n"
        f"💰 <b>Сумма:</b> <code>{price} ₽</code>\n"
        f"🆔 <b>ID платежа:</b> <code>{payment_id}</code>\n\n"
        "Проверьте поступление средств и нажмите нужную кнопку:"
    )

    admin_kb = get_admin_card_confirmation_keyboard(user_id, days, payment_id)

    for adm_id in admin_chat_ids:
        try:
            if message.photo:
                await bot.send_photo(
                    adm_id,
                    photo=message.photo[-1].file_id,
                    caption=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            elif message.document:
                await bot.send_document(
                    adm_id,
                    document=message.document.file_id,
                    caption=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Не удалось отправить чек администратору {adm_id}: {e}")

    await message.answer(
        "✅ <b>Ваш чек успешно отправлен на проверку!</b>\n\n"
        "Администратор проверит платеж в ближайщее время, после чего Премиум-статус активируется автоматически.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_pay_confirm:"))
async def admin_confirm_payment(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Ошибка формата", show_alert=True)

    user_id = int(parts[1])
    days = int(parts[2])
    payment_id = parts[3]

    try:
        already_processed = await Database.is_payment_processed(payment_id)
        if already_processed:
            return await callback.answer("Этот платёж уже был подтверждён!", show_alert=True)

        end_date = await Database.add_premium(user_id, days)
        await Database.mark_payment_processed(payment_id, user_id, days)
    except Exception as e:
        return await callback.answer(f"Ошибка БД: {e}", show_alert=True)

    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=callback.message.text + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Ваша оплата подтверждена!</b>\n\n"
            f"Премиум-подписка активирована на <b>{get_plural_days(days)}</b>.\n"
            f"Срок действия: до <b>{end_date}</b>.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("Премиум успешно выдан!")


@router.callback_query(F.data.startswith("adm_pay_reject:"))
async def admin_reject_payment(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Ошибка формата", show_alert=True)

    user_id = int(parts[1])

    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=callback.message.caption + "\n\n❌ <b>ОТКЛОНЕНО</b>", reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=callback.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>", reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            "❌ <b>Ваш платёж был отклонён администратором.</b>\nЕсли у вас есть вопросы, обратитесь в поддержку.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("Платеж отклонен.")