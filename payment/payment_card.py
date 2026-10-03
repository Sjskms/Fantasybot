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
from services.logging_service import load_global_logging_config, log_event
from config import ADMIN_ID

router = Router()
logger = logging.getLogger(__name__)


class CardPaymentStates(StatesGroup):
    waiting_for_receipt = State()


@router.callback_query(F.data.startswith("pay_gw_card_"))
async def show_card_payment_details(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.removeprefix("pay_gw_card_").split("_")
    if len(parts) != 3:
        return await callback.answer("Ошибка параметров тарифа", show_alert=True)

    try:
        days = int(parts[0])
        price = float(parts[1])
        currency = parts[2]
    except (ValueError, IndexError):
        return await callback.answer("Некорректный тариф", show_alert=True)

    config = load_payment_config()
    card_details = config.get("card_details", "Реквизиты уточняйте у администратора")

    await state.update_data(
        card_pay_days=days,
        card_pay_price=price,
        card_pay_currency=currency,
    )
    await state.set_state(CardPaymentStates.waiting_for_receipt)

    text = (
        f"💳 <b>Оплата банковской картой РФ</b>\n\n"
        f"Тариф: <b>{get_plural_days(days)}</b>\n"
        f"Сумма к переводу: <b>{int(price) if price.is_integer() else price} ₽</b>\n\n"
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
    raw_price = data.get("card_pay_price")

    if not raw_price or float(raw_price) == 0:
        cfg = load_payment_config()
        matched_tariff = next((t for t in cfg.get("tariffs", []) if int(t.get("days", 0)) == int(days)), None)
        price = float(matched_tariff.get("price", 0)) if matched_tariff else 0.0
    else:
        price = float(raw_price)

    user_id = message.from_user.id
    user_name = message.from_user.full_name
    username = f"@{message.from_user.username}" if message.from_user.username else "нет"

    if message.document:
        mime = message.document.mime_type or ""
        file_name = (message.document.file_name or "").lower()
        if not (mime == "application/pdf" or file_name.endswith((".pdf", ".jpg", ".jpeg", ".png"))):
            return await message.answer("⚠️ Пожалуйста, отправьте чек в виде <b>фотографии</b> или <b>PDF-файла</b>.", parse_mode="HTML")

    await state.clear()
    payment_id = secrets.token_hex(6)
    display_price = int(price) if price.is_integer() else price

    # 📝 ЛОГИРОВАНИЕ: Пользователь загрузил чек (незавершенная покупка)
    await log_event(
        "payment_pending",
        f"⏳ <b>Отправлен чек на оплату картой</b>\n"
        f"💰 Сумма: <code>{display_price} ₽</code> | Тариф: <b>{get_plural_days(days)}</b>\n"
        f"🆔 Чек ID: <code>{payment_id}</code>",
        user_id=user_id,
        payment_method="card"
    )

    recipients = set()
    if isinstance(ADMIN_ID, (list, tuple, set)):
        for adm in ADMIN_ID:
            try:
                recipients.add(int(adm))
            except (ValueError, TypeError):
                pass
    elif ADMIN_ID:
        try:
            recipients.add(int(ADMIN_ID))
        except (ValueError, TypeError):
            pass

    admin_caption = (
        f"🧾 <b>Новый чек на оплату картой!</b>\n\n"
        f"👤 <b>Пользователь:</b> {escape(user_name)} (ID: <code>{user_id}</code>, {username})\n"
        f"⭐ <b>Тариф:</b> {get_plural_days(days)}\n"
        f"💰 <b>Сумма:</b> <code>{display_price} ₽</code>\n"
        f"🆔 <b>ID платежа:</b> <code>{payment_id}</code>\n\n"
        "Проверьте поступление средств и нажмите кнопку:"
    )

    admin_kb = get_admin_card_confirmation_keyboard(user_id, days, price, payment_id)

    for adm_id in recipients:
        try:
            if message.photo:
                await bot.send_photo(
                    chat_id=adm_id,
                    photo=message.photo[-1].file_id,
                    caption=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            elif message.document:
                await bot.send_document(
                    chat_id=adm_id,
                    document=message.document.file_id,
                    caption=admin_caption,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Не удалось доставить чек получателю {adm_id}: {e}")

    await message.answer(
        "✅ <b>Ваш чек успешно отправлен на проверку!</b>\n\n"
        "Обычно проверка занимает от 2 до 15 минут. Как только администратор подтвердит платёж, "
        "Премиум-статус активируется автоматически.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_pay_confirm:"))
async def admin_confirm_payment(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    
    if len(parts) == 5:
        user_id = int(parts[1])
        days = int(parts[2])
        price = float(parts[3])
        payment_id = parts[4]
    elif len(parts) == 4:
        user_id = int(parts[1])
        days = int(parts[2])
        price = 0.0
        payment_id = parts[3]
    else:
        return await callback.answer("Ошибка формата колбэка", show_alert=True)

    try:
        already_processed = await Database.is_payment_processed(payment_id)
        if already_processed:
            return await callback.answer("Этот платёж уже был подтверждён ранее!", show_alert=True)

        end_date = await Database.add_premium(user_id, days)
        await Database.mark_payment_processed(
            payment_id=payment_id,
            user_id=user_id,
            days=days,
            amount=price,
            currency="RUB",
            method="card"
        )

        # 📝 ЛОГИРОВАНИЕ: Админ подтвердил оплату картой
        await log_event(
            "payment_success",
            f"✅ <b>Подтверждена оплата картой!</b>\n"
            f"💰 Сумма: <code>{price} ₽</code> | Выдано: <b>{get_plural_days(days)}</b>\n"
            f"📅 Активен до: <b>{end_date}</b>\n"
            f"👑 Подтвердил админ ID: <code>{callback.from_user.id}</code>",
            user_id=user_id,
            payment_method="card"
        )

    except Exception as e:
        logger.exception("Ошибка выдачи премиума админом: %s", e)
        return await callback.answer(f"Ошибка базы данных: {e}", show_alert=True)

    try:
        if callback.message.caption:
            new_caption = callback.message.caption + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> (Администратор: <code>{callback.from_user.id}</code>)"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.text + f"\n\n✅ <b>ПОДТВЕРЖДЕНО</b> (Администратор: <code>{callback.from_user.id}</code>)"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Ваша оплата подтверждена!</b>\n\n"
            f"Премиум-подписка активирована/продлена на <b>{get_plural_days(days)}</b>.\n"
            f"Срок действия: до <b>{end_date}</b>.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer("Платеж подтвержден, Премиум выдан!")


@router.callback_query(F.data.startswith("adm_pay_reject:"))
async def admin_reject_payment(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Ошибка формата", show_alert=True)

    user_id = int(parts[1])
    payment_id = parts[2]

    # 📝 ЛОГИРОВАНИЕ: Чек отклонен
    await log_event(
        "payment_rejected",
        f"❌ <b>Отклонен чек на оплату картой</b>\n"
        f"🆔 Чек ID: <code>{payment_id}</code>\n"
        f"👑 Отклонил админ ID: <code>{callback.from_user.id}</code>",
        user_id=user_id,
        payment_method="card"
    )

    try:
        if callback.message.caption:
            new_caption = callback.message.caption + f"\n\n❌ <b>ОТКЛОНЕНО</b> (Администратор: <code>{callback.from_user.id}</code>)"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.text + f"\n\n❌ <b>ОТКЛОНЕНО</b> (Администратор: <code>{callback.from_user.id}</code>)"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
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