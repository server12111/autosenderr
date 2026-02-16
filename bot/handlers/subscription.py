import time
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    subscription_keyboard,
    payment_keyboard,
    payment_method_keyboard,
    ton_payment_keyboard,
    main_menu_keyboard,
    back_to_subscription_keyboard,
    cancel_keyboard,
    back_to_menu_keyboard,
)
from ..config import config
from ..services import CryptoBotService, TonPaymentService

router = Router()


class SubscriptionStates(StatesGroup):
    waiting_promocode = State()


@router.callback_query(F.data == "subscription")
async def callback_subscription(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)

    if user.subscription_end and user.subscription_end > datetime.now():
        days_left = (user.subscription_end - datetime.now()).days
        text = (
            f"💳 Ваша подписка\n\n"
            f"✅ Подписка активна\n"
            f"Действует до: {user.subscription_end.strftime('%d.%m.%Y %H:%M')}\n"
            f"Осталось дней: {days_left}\n\n"
            f"Стоимость продления: {config.SUBSCRIPTION_PRICE} {config.SUBSCRIPTION_CURRENCY}/мес"
        )
        has_subscription = True
    else:
        text = (
            f"💳 Ваша подписка\n\n"
            f"❌ Подписка не активна\n\n"
            f"Для использования всех функций бота необходима подписка.\n\n"
            f"Стоимость: {config.SUBSCRIPTION_PRICE} {config.SUBSCRIPTION_CURRENCY}/мес"
        )
        has_subscription = False

    await callback.message.edit_text(
        text, reply_markup=subscription_keyboard(has_subscription)
    )
    await callback.answer()


@router.callback_query(F.data == "buy_subscription")
async def callback_buy_subscription(callback: CallbackQuery, db: Database, ton_service: TonPaymentService = None):
    if config.TON_WALLET_ADDRESS and ton_service:
        ton_amount = await ton_service.calculate_ton_amount(config.SUBSCRIPTION_PRICE)
        if ton_amount:
            ton_text = f"💠 TON — ~{ton_amount} TON (≈ {config.SUBSCRIPTION_PRICE} USDT)"
        else:
            ton_text = f"💠 TON — ≈ {config.SUBSCRIPTION_PRICE} USDT в TON"
        text = (
            "💳 Выберите способ оплаты:\n\n"
            f"💎 CryptoBot — {config.SUBSCRIPTION_PRICE} {config.SUBSCRIPTION_CURRENCY}\n"
            f"{ton_text}"
        )
        await callback.message.edit_text(text, reply_markup=payment_method_keyboard())
    else:
        # No TON configured, go directly to CryptoBot
        await _create_cryptobot_subscription(callback, db)
    await callback.answer()


@router.callback_query(F.data == "pay_cryptobot")
async def callback_pay_cryptobot(
    callback: CallbackQuery, db: Database, cryptobot: CryptoBotService
):
    await _create_cryptobot_subscription(callback, db, cryptobot)
    await callback.answer()


async def _create_cryptobot_subscription(
    callback: CallbackQuery, db: Database, cryptobot: CryptoBotService = None
):
    """Create CryptoBot invoice for subscription."""
    if cryptobot is None:
        cryptobot = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)

    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text("⏳ Создаём платёж...")

    invoice = await cryptobot.create_invoice(
        amount=config.SUBSCRIPTION_PRICE,
        currency=config.SUBSCRIPTION_CURRENCY,
        description="Подписка на бота рассылок (30 дней)",
        expires_in=3600,
    )

    if not invoice:
        error_msg = "Невідома помилка"
        if cryptobot.last_error:
            error_msg = cryptobot.last_error.message
        await callback.message.edit_text(
            f"❌ Помилка створення платежу:\n{error_msg}",
            reply_markup=main_menu_keyboard(),
        )
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice.invoice_id,
        amount=invoice.amount,
        currency=invoice.currency,
    )

    text = (
        f"💳 Оплата подписки\n\n"
        f"Сумма: {invoice.amount} {invoice.currency}\n"
        f"Срок: 30 дней\n\n"
        f"Нажмите «Оплатить» для перехода к оплате через CryptoBot.\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    await callback.message.edit_text(
        text, reply_markup=payment_keyboard(invoice.pay_url, invoice.invoice_id)
    )


@router.callback_query(F.data == "pay_ton")
async def callback_pay_ton(
    callback: CallbackQuery, db: Database, ton_service: TonPaymentService
):
    user = await db.get_user(callback.from_user.id)
    comment = f"sub_{user.telegram_id}_{int(time.time())}"

    await callback.message.edit_text("⏳ Получаем курс TON...")

    amount = await ton_service.calculate_ton_amount(config.SUBSCRIPTION_PRICE)
    if not amount:
        await callback.message.edit_text(
            "❌ Не удалось получить курс TON. Попробуйте позже.",
            reply_markup=payment_method_keyboard(),
        )
        await callback.answer()
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=comment,
        amount=amount,
        currency="TON",
    )

    pay_url = ton_service.generate_payment_link(amount, comment)

    text = (
        f"💠 Оплата подписки через TON\n\n"
        f"Сумма: <b>{amount} TON</b> (≈ {config.SUBSCRIPTION_PRICE} USDT)\n"
        f"Срок: 30 дней\n\n"
        f"Кошелёк: <code>{config.TON_WALLET_ADDRESS}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        f"Нажмите кнопку ниже для оплаты через Tonkeeper.\n"
        f"<b>Важно:</b> комментарий должен совпадать точно!\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    await callback.message.edit_text(
        text, reply_markup=ton_payment_keyboard(pay_url, comment)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_ton_payment:"))
async def callback_check_ton_payment(
    callback: CallbackQuery, db: Database, ton_service: TonPaymentService
):
    comment = callback.data.split(":", 1)[1]

    payment = await db.get_payment_by_invoice(comment)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    is_paid = await ton_service.check_payment(payment.amount, comment)

    if is_paid:
        await db.update_payment_status(comment, "paid")

        user = await db.get_user(callback.from_user.id)

        if user.subscription_end and user.subscription_end > datetime.now():
            new_end = user.subscription_end + timedelta(days=30)
        else:
            new_end = datetime.now() + timedelta(days=30)

        await db.update_subscription(user.id, new_end)

        await callback.message.edit_text(
            f"✅ Оплата получена!\n\n"
            f"Ваша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Оплата получена!")
    else:
        await callback.answer(
            "⏳ Оплата ещё не поступила. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("check_payment:"))
async def callback_check_payment(
    callback: CallbackQuery, db: Database, cryptobot: CryptoBotService
):
    invoice_id = callback.data.split(":")[1]

    payment = await db.get_payment_by_invoice(invoice_id)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    is_paid = await cryptobot.check_invoice_paid(invoice_id)

    if is_paid:
        await db.update_payment_status(invoice_id, "paid")

        user = await db.get_user(callback.from_user.id)

        if user.subscription_end and user.subscription_end > datetime.now():
            new_end = user.subscription_end + timedelta(days=30)
        else:
            new_end = datetime.now() + timedelta(days=30)

        await db.update_subscription(user.id, new_end)

        await callback.message.edit_text(
            f"✅ Оплата получена!\n\n"
            f"Ваша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Оплата получена!")
    else:
        await callback.answer(
            "⏳ Оплата ещё не поступила. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data == "enter_promocode")
async def callback_enter_promocode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SubscriptionStates.waiting_promocode)
    await callback.message.edit_text(
        "🎟 Введите промокод:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(SubscriptionStates.waiting_promocode)
async def process_promocode(message: Message, state: FSMContext, db: Database):
    code = message.text.strip()
    promo = await db.get_promocode(code)

    if not promo:
        await message.answer(
            "❌ Промокод не найден. Проверьте правильность и попробуйте ещё раз:",
            reply_markup=cancel_keyboard(),
        )
        return

    if promo.uses_count >= promo.max_uses:
        await message.answer(
            "❌ Этот промокод уже был использован максимальное количество раз.",
            reply_markup=back_to_subscription_keyboard(),
        )
        await state.clear()
        return

    user = await db.get_user(message.from_user.id)

    if await db.has_user_used_promocode(promo.id, user.id):
        await message.answer(
            "❌ Вы уже использовали этот промокод.",
            reply_markup=back_to_subscription_keyboard(),
        )
        await state.clear()
        return

    if user.subscription_end and user.subscription_end > datetime.now():
        new_end = user.subscription_end + timedelta(days=promo.duration_days)
    else:
        new_end = datetime.now() + timedelta(days=promo.duration_days)

    await db.update_subscription(user.id, new_end)
    await db.use_promocode(code, user.id, promo.id)
    await state.clear()

    await message.answer(
        f"✅ Промокод активирован!\n\n"
        f"Добавлено дней: {promo.duration_days}\n"
        f"Подписка активна до: {new_end.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "pay_card")
async def callback_pay_card(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 Оплата банковской картой\n\n"
        "Для оплаты подписки банковской картой (Visa/MasterCard) "
        "напишите нашему менеджеру:\n\n"
        "👤 Менеджер: @autosenderkarta\n\n"
        "📌 Как это работает:\n"
        "1. Напишите менеджеру, что хотите оплатить подписку\n"
        "2. Менеджер отправит вам реквизиты для перевода\n"
        "3. После оплаты отправьте скриншот чека менеджеру\n"
        "4. Подписка будет активирована в течение нескольких минут\n\n"
        "⏰ Время работы менеджера: ежедневно с 9:00 до 23:00",
        reply_markup=back_to_subscription_keyboard(),
    )
    await callback.answer()
