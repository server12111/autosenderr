import time
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    subscription_keyboard,
    subscription_plan_keyboard,
    payment_keyboard,
    payment_method_keyboard,
    ton_payment_keyboard,
    platega_payment_keyboard,
    main_menu_keyboard,
    back_to_subscription_keyboard,
    cancel_keyboard,
    back_to_menu_keyboard,
)
from ..config import config
from ..services import CryptoBotService, TonPaymentService, PlategaService, get_usd_uah_rate
from ..utils.premium_emoji import pe

router = Router()


class SubscriptionStates(StatesGroup):
    waiting_promocode = State()
    choosing_plan = State()


@router.callback_query(F.data == "subscription")
async def callback_subscription(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)

    if user.subscription_end and user.subscription_end > datetime.now():
        days_left = (user.subscription_end - datetime.now()).days
        price_7d = await db.get_price(7)
        price_30d = await db.get_price(30)
        text = (
            f"💳 Ваша подписка\n\n"
            f"✅ Подписка активна\n"
            f"Действует до: {user.subscription_end.strftime('%d.%m.%Y %H:%M')}\n"
            f"Осталось дней: {days_left}\n\n"
            f"Стоимость продления:\n"
            f"• 7 дней — {price_7d} USDT\n"
            f"• 30 дней — {price_30d} USDT"
        )
        has_subscription = True
    else:
        price_7d = await db.get_price(7)
        price_30d = await db.get_price(30)
        text = (
            f"💳 Ваша подписка\n\n"
            f"❌ Подписка не активна\n\n"
            f"Для использования всех функций бота необходима подписка.\n\n"
            f"Стоимость:\n"
            f"• 7 дней — {price_7d} USDT\n"
            f"• 30 дней — {price_30d} USDT"
        )
        has_subscription = False

    await callback.message.edit_text(
        pe(text), parse_mode="HTML", reply_markup=subscription_keyboard(has_subscription)
    )
    await callback.answer()


@router.callback_query(F.data == "buy_subscription")
async def callback_buy_subscription(callback: CallbackQuery, state: FSMContext, db: Database):
    price_7d = await db.get_price(7)
    price_30d = await db.get_price(30)
    await callback.message.edit_text(
        pe(f"💳 Выберите план подписки:\n\n"
        f"📅 7 дней — {price_7d} USDT\n"
        f"📅 30 дней — {price_30d} USDT"),
        parse_mode="HTML",
        reply_markup=subscription_plan_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_plan:"))
async def callback_sub_plan(
    callback: CallbackQuery, state: FSMContext, db: Database,
    ton_service: TonPaymentService = None, platega_service: PlategaService = None
):
    plan_days = int(callback.data.split(":")[1])
    await state.update_data(plan_days=plan_days)

    price = await db.get_price(plan_days)
    show_platega = bool(config.PLATEGA_MERCHANT_ID and config.PLATEGA_SECRET)
    has_ton = bool(config.TON_WALLET_ADDRESS and ton_service)

    if has_ton or show_platega:
        lines = [f"💎 CryptoBot — {round(price * 1.03, 2)} USDT (+3%)"]
        if has_ton:
            ton_amount = await ton_service.calculate_ton_amount(price)
            if ton_amount:
                lines.append(f"💠 TON — ~{ton_amount} TON (≈ {price} USDT)")
            else:
                lines.append(f"💠 TON — ≈ {price} USDT в TON")
        if show_platega and platega_service:
            rub_price = await platega_service.calculate_rub_price(price)
            lines.append(f"💳 СБП (рубли) — ~{rub_price:.0f} ₽")
        text = pe(f"💳 Способ оплаты ({plan_days} дней):\n\n" + "\n".join(lines))
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=payment_method_keyboard(show_platega=show_platega, show_ton=has_ton),
        )
    else:
        await _create_cryptobot_subscription(callback, db, plan_days=plan_days)
    await callback.answer()


@router.callback_query(F.data == "pay_cryptobot")
async def callback_pay_cryptobot(
    callback: CallbackQuery, state: FSMContext, db: Database, cryptobot: CryptoBotService
):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)
    await _create_cryptobot_subscription(callback, db, cryptobot, plan_days=plan_days)
    await callback.answer()


async def _create_cryptobot_subscription(
    callback: CallbackQuery, db: Database, cryptobot: CryptoBotService = None, plan_days: int = 30
):
    if cryptobot is None:
        cryptobot = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)

    user = await db.get_user(callback.from_user.id)
    price = await db.get_price(plan_days)
    crypto_price = round(price * 1.03, 2)  # +3% processing fee

    await callback.message.edit_text(pe("⏳ Создаём платёж..."), parse_mode="HTML")

    invoice = await cryptobot.create_invoice(
        amount=crypto_price,
        currency=config.SUBSCRIPTION_CURRENCY,
        description=f"Подписка на бота рассылок ({plan_days} дней)",
        expires_in=3600,
    )

    if not invoice:
        error_msg = "Неизвестная ошибка"
        if cryptobot.last_error:
            error_msg = cryptobot.last_error.message
        await callback.message.edit_text(
            pe(f"❌ Ошибка создания платежа:\n{error_msg}"),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice.invoice_id,
        amount=price,
        currency=invoice.currency,
        plan_days=plan_days,
    )

    text = pe(
        f"💳 Оплата подписки\n\n"
        f"Сумма: {invoice.amount} {invoice.currency}\n"
        f"Срок: {plan_days} дней\n\n"
        f"Нажмите «Оплатить» для перехода к оплате через CryptoBot.\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=payment_keyboard(invoice.pay_url, invoice.invoice_id, plan_days, support_username=support)
    )


@router.callback_query(F.data == "pay_ton")
async def callback_pay_ton(
    callback: CallbackQuery, state: FSMContext, db: Database, ton_service: TonPaymentService
):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)

    user = await db.get_user(callback.from_user.id)
    comment = f"sub_{user.telegram_id}_{int(time.time())}"

    await callback.message.edit_text(pe("⏳ Получаем курс TON..."), parse_mode="HTML")

    price = await db.get_price(plan_days)
    amount = await ton_service.calculate_ton_amount(price)
    if not amount:
        await callback.message.edit_text(
            pe("❌ Не удалось получить курс TON. Попробуйте позже."),
            parse_mode="HTML",
            reply_markup=payment_method_keyboard(show_platega=bool(config.PLATEGA_MERCHANT_ID and config.PLATEGA_SECRET)),
        )
        await callback.answer()
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=comment,
        amount=amount,
        currency="TON",
        plan_days=plan_days,
        payment_method="ton",
    )

    pay_url = ton_service.generate_payment_link(amount, comment)

    text = (
        f"💠 Оплата подписки через TON\n\n"
        f"Сумма: <b>{amount} TON</b> (≈ {price} USDT)\n"
        f"Срок: {plan_days} дней\n\n"
        f"Кошелёк: <code>{config.TON_WALLET_ADDRESS}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        f"Нажмите кнопку ниже для оплаты через Tonkeeper.\n"
        f"<b>Важно:</b> комментарий должен совпадать точно!\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await callback.message.edit_text(
        text, reply_markup=ton_payment_keyboard(pay_url, comment, plan_days, support_username=support)
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
        updated = await db.update_payment_status(comment, "paid")
        if not updated:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return
        user = await db.get_user(callback.from_user.id)
        plan_days = getattr(payment, "plan_days", 30) or 30

        if user.subscription_end and user.subscription_end > datetime.now():
            new_end = user.subscription_end + timedelta(days=plan_days)
        else:
            new_end = datetime.now() + timedelta(days=plan_days)

        await db.update_subscription(user.id, new_end)
        price_usdt = await db.get_price(plan_days)
        await _pay_referral(user, db, price_usdt)

        await callback.message.edit_text(
            pe(f"✅ Оплата получена!\n\n"
            f"Ваша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}"),
            parse_mode="HTML",
            reply_markup=subscription_keyboard(True),
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
        updated = await db.update_payment_status(invoice_id, "paid")
        if not updated:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return
        user = await db.get_user(callback.from_user.id)
        plan_days = getattr(payment, "plan_days", 30) or 30

        if user.subscription_end and user.subscription_end > datetime.now():
            new_end = user.subscription_end + timedelta(days=plan_days)
        else:
            new_end = datetime.now() + timedelta(days=plan_days)

        await db.update_subscription(user.id, new_end)
        await _pay_referral(user, db, payment.amount)

        await callback.message.edit_text(
            pe(f"✅ Оплата получена!\n\n"
            f"Ваша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}"),
            parse_mode="HTML",
            reply_markup=subscription_keyboard(True),
        )
        await callback.answer("Оплата получена!")
    else:
        await callback.answer(
            "⏳ Оплата ещё не поступила. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data == "pay_platega")
async def callback_pay_platega(
    callback: CallbackQuery, state: FSMContext, db: Database, platega_service: PlategaService = None
):
    if not platega_service or not config.PLATEGA_SECRET:
        await callback.answer("Platega не настроена", show_alert=True)
        return

    data = await state.get_data()
    plan_days = data.get("plan_days", 30)
    user = await db.get_user(callback.from_user.id)
    price_usdt = await db.get_price(plan_days)
    amount_rub = await platega_service.calculate_rub_price(price_usdt)

    order_id = f"platega_{user.telegram_id}_{int(time.time())}"

    await callback.message.edit_text(pe("⏳ Создаём платёж через СБП..."), parse_mode="HTML")

    invoice = await platega_service.create_invoice(
        amount_rub=amount_rub,
        order_id=order_id,
        description=f"Подписка на бота рассылок ({plan_days} дней)",
    )

    if not invoice or not invoice.get("payment_url"):
        await callback.message.edit_text(
            pe("❌ Ошибка создания платежа через Platega. Попробуйте позже."),
            parse_mode="HTML",
            reply_markup=payment_method_keyboard(show_platega=True),
        )
        await callback.answer()
        return

    transaction_id = invoice["payment_id"]  # UUID from Platega

    await db.create_payment(
        user_id=user.id,
        invoice_id=transaction_id,
        amount=amount_rub,
        currency="RUB",
        plan_days=plan_days,
        payment_method="platega",
    )

    text = pe(
        f"💳 Оплата через СБП\n\n"
        f"Сумма: <b>{amount_rub:.0f} ₽</b>\n"
        f"Срок: {plan_days} дней\n\n"
        f"Нажмите «Оплатить через СБП» для перехода к оплате.\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=platega_payment_keyboard(invoice["payment_url"], transaction_id, plan_days, support_username=support),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_platega:"))
async def callback_check_platega_payment(
    callback: CallbackQuery, db: Database, platega_service: PlategaService = None
):
    order_id = callback.data.split(":", 1)[1]

    payment = await db.get_payment_by_invoice(order_id)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    if not platega_service:
        await callback.answer("Platega не настроена", show_alert=True)
        return

    is_paid = await platega_service.check_payment(order_id)

    if is_paid:
        updated = await db.update_payment_status(order_id, "paid")
        if not updated:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return
        user = await db.get_user(callback.from_user.id)
        plan_days = getattr(payment, "plan_days", 30) or 30

        if user.subscription_end and user.subscription_end > datetime.now():
            new_end = user.subscription_end + timedelta(days=plan_days)
        else:
            new_end = datetime.now() + timedelta(days=plan_days)

        await db.update_subscription(user.id, new_end)
        price_usdt = await db.get_price(plan_days)
        await _pay_referral(user, db, price_usdt)

        await callback.message.edit_text(
            pe(f"✅ Оплата через СБП получена!\n\n"
            f"Ваша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}"),
            parse_mode="HTML",
            reply_markup=subscription_keyboard(True),
        )
        await callback.answer("Оплата получена!")
    else:
        await callback.answer(
            "⏳ Оплата ещё не поступила. Попробуйте позже.",
            show_alert=True,
        )


async def _pay_referral(user, db: Database, payment_amount: float):
    """Pay referral reward to the user's referrer."""
    if not user.referred_by:
        return
    try:
        ref_percent = await db.get_ref_percent()
        reward = round(payment_amount * ref_percent / 100, 4)
        if reward > 0:
            await db.add_ref_balance(user.referred_by, reward)
    except Exception:
        pass


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
            pe("❌ Промокод не найден. Проверьте правильность и попробуйте ещё раз:"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if promo.uses_count >= promo.max_uses:
        await message.answer(
            pe("❌ Этот промокод уже был использован максимальное количество раз."),
            parse_mode="HTML",
            reply_markup=back_to_subscription_keyboard(),
        )
        await state.clear()
        return

    user = await db.get_user(message.from_user.id)

    if await db.has_user_used_promocode(promo.id, user.id):
        await message.answer(
            pe("❌ Вы уже использовали этот промокод."),
            parse_mode="HTML",
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

    if promo.is_subscription:
        await db.create_paid_promo_payment(
            user_id=user.id,
            invoice_id=f"promo_{promo.code}_{user.id}",
            plan_days=promo.duration_days,
        )

    await state.clear()

    await message.answer(
        pe(f"✅ Промокод активирован!\n\n"
        f"Добавлено дней: {promo.duration_days}\n"
        f"Подписка активна до: {new_end.strftime('%d.%m.%Y %H:%M')}"),
        parse_mode="HTML",
        reply_markup=subscription_keyboard(True),
    )


@router.callback_query(F.data == "pay_card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)

    manager = await db.get_setting("card_manager_username") or "autosenderkarta"
    price_usdt = await db.get_price(plan_days)
    uah_rate = await get_usd_uah_rate()
    price_uah = round(price_usdt * uah_rate)

    await callback.message.edit_text(
        pe(f"🇺🇦 Оплата картой (грн)\n\n"
        f"📅 Срок: {plan_days} дней\n"
        f"💰 Сумма: <b>~{price_uah} ₴</b>\n\n"
        "Принимаем оплату только в гривнах (UAH).\n"
        "Напишите нашему менеджеру:\n\n"
        f"👤 Менеджер: @{manager}\n\n"
        "📌 Как это работает:\n"
        "1. Напишите менеджеру, что хотите оплатить подписку\n"
        "2. Менеджер отправит реквизиты для перевода\n"
        "3. После оплаты отправьте скриншот чека менеджеру\n"
        "4. Подписка будет активирована в течение нескольких минут\n\n"
        "⏰ Время работы менеджера: ежедневно с 9:00 до 23:00"),
        parse_mode="HTML",
        reply_markup=back_to_subscription_keyboard(),
    )
    await callback.answer()
