import asyncio
import html
import logging
import time
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    subscription_keyboard,
    subscription_plan_keyboard,
    free_tier_info_keyboard,
    payment_keyboard,
    payment_method_keyboard,
    ton_payment_keyboard,
    platega_payment_keyboard,
    main_menu_keyboard,
    back_to_subscription_keyboard,
    cancel_keyboard,
    back_to_menu_keyboard,
)
from ..config import config, get_stars_price
from ..services import CryptoBotService, TonPaymentService, PlategaService, get_usd_uah_rate
from ..utils.premium_emoji import pe
from ..utils.tg import safe_edit
from ..utils.time_utils import now_moscow

router = Router()
logger = logging.getLogger(__name__)


class SubscriptionStates(StatesGroup):
    waiting_promocode = State()
    choosing_plan = State()


@router.callback_query(F.data == "subscription")
async def callback_subscription(callback: CallbackQuery, db: Database, state: FSMContext):
    # Always-reachable nav button — without clearing state, a stray text
    # message sent after navigating here from mid-wizard would still be
    # caught by whatever text handler that old state points to and get
    # silently applied to it (same bug class already fixed for the
    # referral wallet flow, see callback_referral).
    await state.clear()
    user = await db.get_user(callback.from_user.id)

    if user.subscription_end and user.subscription_end > now_moscow():
        days_left = (user.subscription_end - now_moscow()).days
        price_1d = await db.get_price(1)
        price_7d = await db.get_price(7)
        price_30d = await db.get_price(30)
        text = (
            f"💳 Ваша подписка\n\n"
            f"✅ Подписка активна\n"
            f"Действует до: {user.subscription_end.strftime('%d.%m.%Y %H:%M')}\n"
            f"Осталось дней: {days_left}\n\n"
            f"Стоимость продления:\n"
            f"• 1 день — {price_1d} USDT\n"
            f"• 7 дней — {price_7d} USDT\n"
            f"• 30 дней — {price_30d} USDT"
        )
        has_subscription = True
    else:
        price_1d = await db.get_price(1)
        price_7d = await db.get_price(7)
        price_30d = await db.get_price(30)
        text = (
            f"💳 Ваша подписка\n\n"
            f"❌ Подписка не активна\n\n"
            f"Для использования всех функций бота необходима подписка.\n\n"
            f"Стоимость:\n"
            f"• 1 день — {price_1d} USDT\n"
            f"• 7 дней — {price_7d} USDT\n"
            f"• 30 дней — {price_30d} USDT\n\n"
            f"🆓 Или используйте бота бесплатно с рекламной подписью."
        )
        has_subscription = False

    await safe_edit(callback.message, pe(text), parse_mode="HTML", reply_markup=subscription_keyboard(has_subscription), retries=0)
    await callback.answer()


@router.callback_query(F.data == "buy_subscription")
async def callback_buy_subscription(callback: CallbackQuery, state: FSMContext, db: Database):
    price_1d = await db.get_price(1)
    price_7d = await db.get_price(7)
    price_30d = await db.get_price(30)
    await safe_edit(
        callback.message,
        pe(f"💳 Выберите план подписки:\n\n"
        f"📅 1 день — {price_1d} USDT\n"
        f"📅 7 дней — {price_7d} USDT\n"
        f"📅 30 дней — {price_30d} USDT"),
        parse_mode="HTML",
        reply_markup=subscription_plan_keyboard(),
        retries=0,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_plan:"))
async def callback_sub_plan(
    callback: CallbackQuery, state: FSMContext, db: Database,
    ton_service: TonPaymentService = None, platega_service: PlategaService = None
):
    plan_days = int(callback.data.split(":")[1])
    await state.update_data(plan_days=plan_days)
    # Fetching the GRAM(TON)/rouble exchange rate below can take a real
    # network round-trip (up to ~10-20s worst case if the price cache is
    # cold and the upstream API is slow) — answer right away so the button
    # doesn't sit in Telegram's loading spinner for that whole time; the
    # message itself still gets a quick placeholder below while the actual
    # prices load.
    await callback.answer()

    price = await db.get_price(plan_days)
    show_platega = bool(config.PLATEGA_MERCHANT_ID and config.PLATEGA_SECRET)
    has_ton = bool(config.TON_WALLET_ADDRESS and ton_service)
    stars_price = get_stars_price(plan_days)

    # Stars is a native Telegram payment — always available regardless of
    # any provider config — so the method-selection screen is always worth
    # showing now (previously skipped straight to CryptoBot when neither
    # TON nor Platega were configured, which would have hidden Stars too).
    crypto_price = round(price * 1.03, 2)
    lines = [
        f"💎 CryptoBot — {price:.2f} USDT (+3%)",
        f"⭐️ Telegram Stars — {stars_price}",
    ]
    if has_ton or (show_platega and platega_service):
        await safe_edit(
            callback.message, pe("⏳ Загружаем способы оплаты..."), parse_mode="HTML", retries=0,
        )
    if has_ton:
        try:
            ton_amount = await asyncio.wait_for(ton_service.calculate_ton_amount(price), timeout=12)
        except asyncio.TimeoutError:
            ton_amount = None
        if ton_amount:
            lines.append(f"💠 GRAM(TON) — ~{ton_amount} GRAM(TON) (≈ {price} USDT)")
        else:
            lines.append(f"💠 GRAM(TON) — ≈ {price} USDT в GRAM(TON)")
    if show_platega and platega_service:
        try:
            rub_price = await asyncio.wait_for(platega_service.calculate_rub_price(price), timeout=12)
        except asyncio.TimeoutError:
            rub_price = None
        if rub_price:
            lines.append(f"💳 СБП (рубли) — ~{rub_price:.0f} ₽")
    text = pe(f"💳 Способ оплаты ({plan_days} дней):\n\n" + "\n".join(lines))
    await safe_edit(
        callback.message, text, parse_mode="HTML",
        reply_markup=payment_method_keyboard(show_platega=show_platega, show_ton=has_ton),
        retries=0,
    )


@router.callback_query(F.data == "pay_cryptobot")
async def callback_pay_cryptobot(
    callback: CallbackQuery, state: FSMContext, db: Database, cryptobot: CryptoBotService
):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)
    await callback.answer()
    await _create_cryptobot_subscription(callback, db, cryptobot, plan_days=plan_days)


async def _create_cryptobot_subscription(
    callback: CallbackQuery, db: Database, cryptobot: CryptoBotService = None, plan_days: int = 30
):
    if cryptobot is None:
        cryptobot = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)

    user = await db.get_user(callback.from_user.id)
    price = await db.get_price(plan_days)
    crypto_price = round(price * 1.03, 2)  # +3% processing fee

    await safe_edit(callback.message, pe("⏳ Создаём платёж..."), parse_mode="HTML", retries=0)

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
        await safe_edit(
            callback.message,
            pe(f"❌ Ошибка создания платежа:\n{html.escape(error_msg)}"),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
            retries=0,
        )
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice.invoice_id,
        amount=price,
        currency=invoice.currency,
        plan_days=plan_days,
        price_usdt=price,
    )

    text = pe(
        f"💳 Оплата подписки\n\n"
        f"Сумма: {price:.2f} {invoice.currency} (+3%)\n"
        f"Срок: {plan_days} дней\n\n"
        f"Нажмите «Оплатить» для перехода к оплате через CryptoBot.\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await safe_edit(
        callback.message,
        text,
        parse_mode="HTML",
        reply_markup=payment_keyboard(invoice.pay_url, invoice.invoice_id, plan_days, support_username=support),
        retries=0,
    )


@router.callback_query(F.data == "pay_stars")
async def callback_pay_stars(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)
    user = await db.get_user(callback.from_user.id)
    stars_price = get_stars_price(plan_days)
    price_usdt = await db.get_price(plan_days)

    # Native Telegram payment (XTR) — unlike CryptoBot/TON/Platega, there is
    # no external provider or manual "check payment" step: Telegram notifies
    # the bot directly via a successful_payment message once the user pays.
    # The payment record must exist BEFORE the invoice is sent so the
    # pre_checkout_query/successful_payment handlers (bot/handlers/
    # stars_payment.py) can find it by payload the instant Telegram calls
    # back — there is no window to create it afterward like the other
    # methods do after polling an external API.
    invoice_id = f"stars_sub_{user.telegram_id}_{time.time_ns()}"
    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice_id,
        amount=stars_price,
        currency="XTR",
        plan_days=plan_days,
        payment_method="stars",
        price_usdt=price_usdt,
    )

    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Подписка AutoSender — {plan_days} дн.",
        description=f"Подписка на бота рассылок AutoSender на {plan_days} дней.",
        payload=invoice_id,
        currency="XTR",
        prices=[LabeledPrice(label=f"Подписка на {plan_days} дн.", amount=stars_price)],
    )
    await safe_edit(
        callback.message,
        pe(f"⭐️ Счёт на {stars_price} Stars отправлен ниже — оплатите его, "
        "и подписка активируется автоматически."),
        parse_mode="HTML",
        reply_markup=back_to_subscription_keyboard(),
        retries=0,
    )
    await callback.answer()


@router.callback_query(F.data == "pay_ton")
async def callback_pay_ton(
    callback: CallbackQuery, state: FSMContext, db: Database, ton_service: TonPaymentService
):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)

    user = await db.get_user(callback.from_user.id)
    comment = f"sub_{user.telegram_id}_{time.time_ns()}"

    await safe_edit(callback.message, pe("⏳ Получаем курс GRAM(TON)..."), parse_mode="HTML", retries=0)
    # Clear the button's loading spinner now — the actual rate lookup below
    # is a real network call (bounded, but can still take several seconds),
    # and the placeholder message above already tells the user something
    # is happening.
    await callback.answer()

    price = await db.get_price(plan_days)
    try:
        amount = await asyncio.wait_for(ton_service.calculate_ton_amount(price), timeout=12)
    except asyncio.TimeoutError:
        amount = None
    if not amount:
        await safe_edit(
            callback.message,
            pe("❌ Не удалось получить курс GRAM(TON). Попробуйте позже."),
            parse_mode="HTML",
            reply_markup=payment_method_keyboard(show_platega=bool(config.PLATEGA_MERCHANT_ID and config.PLATEGA_SECRET)),
            retries=0,
        )
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=comment,
        amount=amount,
        currency="TON",
        plan_days=plan_days,
        payment_method="ton",
        price_usdt=price,
    )

    pay_url = ton_service.generate_payment_link(amount, comment)

    text = (
        f"💠 Оплата подписки через GRAM(TON)\n\n"
        f"Сумма: <b>{amount} GRAM(TON)</b> (≈ {price} USDT)\n"
        f"Срок: {plan_days} дней\n\n"
        f"Кошелёк: <code>{config.TON_WALLET_ADDRESS}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        f"Нажмите кнопку ниже для оплаты через Tonkeeper.\n"
        f"<b>Важно:</b> комментарий должен совпадать точно!\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await safe_edit(callback.message, text, reply_markup=ton_payment_keyboard(pay_url, comment, plan_days, support_username=support), retries=0)


@router.callback_query(F.data.startswith("check_ton_payment:"))
async def callback_check_ton_payment(
    callback: CallbackQuery, db: Database, ton_service: TonPaymentService
):
    comment = callback.data.split(":", 1)[1]

    payment = await db.get_payment_by_invoice(comment)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    if not user or payment.user_id != user.id:
        await callback.answer("⛔ Нет доступа к этому платежу", show_alert=True)
        return
    if payment.payment_method != "ton":
        await callback.answer("⛔ Этот платёж не предназначен для подписки", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    is_paid = await ton_service.check_payment(payment.amount, comment)

    if is_paid:
        price_usdt = payment.price_usdt if payment.price_usdt is not None else payment.amount
        new_end = await db.complete_subscription_payment(comment, price_usdt)
        if not new_end:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return

        await safe_edit(
            callback.message,
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

    user = await db.get_user(callback.from_user.id)
    if not user or payment.user_id != user.id:
        await callback.answer("⛔ Нет доступа к этому платежу", show_alert=True)
        return
    if payment.payment_method != "cryptobot":
        await callback.answer("⛔ Этот платёж не предназначен для подписки", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    is_paid = await cryptobot.check_invoice_paid(invoice_id)

    if is_paid:
        new_end = await db.complete_subscription_payment(invoice_id, payment.amount)
        if not new_end:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return

        await safe_edit(
            callback.message,
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
    order_id = f"platega_{user.telegram_id}_{time.time_ns()}"

    await safe_edit(callback.message, pe("⏳ Создаём платёж через СБП..."), parse_mode="HTML", retries=0)
    # Both calls below hit the Platega gateway over the network — answer the
    # callback now so the button spinner doesn't sit through them.
    await callback.answer()

    invoice = None
    try:
        amount_rub = await asyncio.wait_for(platega_service.calculate_rub_price(price_usdt), timeout=12)
        invoice = await asyncio.wait_for(
            platega_service.create_invoice(
                amount_rub=amount_rub,
                order_id=order_id,
                description=f"Подписка на бота рассылок ({plan_days} дней)",
            ),
            timeout=15,
        )
    except asyncio.TimeoutError:
        invoice = None

    if not invoice or not invoice.get("payment_url"):
        await safe_edit(
            callback.message,
            pe("❌ Ошибка создания платежа через Platega. Попробуйте позже."),
            parse_mode="HTML",
            reply_markup=payment_method_keyboard(show_platega=True),
            retries=0,
        )
        return

    transaction_id = invoice["payment_id"]  # UUID from Platega

    await db.create_payment(
        user_id=user.id,
        invoice_id=transaction_id,
        amount=amount_rub,
        currency="RUB",
        plan_days=plan_days,
        payment_method="platega",
        price_usdt=price_usdt,
    )

    text = pe(
        f"💳 Оплата через СБП\n\n"
        f"Сумма: <b>{amount_rub:.0f} ₽</b>\n"
        f"Срок: {plan_days} дней\n\n"
        f"Нажмите «Оплатить через СБП» для перехода к оплате.\n"
        f"После оплаты нажмите «Проверить оплату»."
    )
    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await safe_edit(
        callback.message, text, parse_mode="HTML",
        reply_markup=platega_payment_keyboard(invoice["payment_url"], transaction_id, plan_days, support_username=support),
        retries=0,
    )


@router.callback_query(F.data.startswith("check_platega:"))
async def callback_check_platega_payment(
    callback: CallbackQuery, db: Database, platega_service: PlategaService = None
):
    order_id = callback.data.split(":", 1)[1]

    payment = await db.get_payment_by_invoice(order_id)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    if not user or payment.user_id != user.id:
        await callback.answer("⛔ Нет доступа к этому платежу", show_alert=True)
        return
    if payment.payment_method != "platega":
        await callback.answer("⛔ Этот платёж не предназначен для подписки", show_alert=True)
        return

    if payment.status == "paid":
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    if not platega_service:
        await callback.answer("Platega не настроена", show_alert=True)
        return

    is_paid = await platega_service.check_payment(order_id)

    if is_paid:
        price_usdt = payment.price_usdt if payment.price_usdt is not None else payment.amount
        new_end = await db.complete_subscription_payment(order_id, price_usdt)
        if not new_end:
            await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
            return

        await safe_edit(
            callback.message,
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


@router.callback_query(F.data == "activate_free_tier")
async def callback_activate_free_tier(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return

    has_paid = user.subscription_end and user.subscription_end > now_moscow()
    already_active = user.subscription_type == "free_ad" and not has_paid

    if has_paid:
        status_line = "✅ У вас активная подписка — реклама не добавляется."
    elif already_active:
        status_line = "✅ Бесплатный тариф уже активен."
    else:
        status_line = ""

    text = pe(
        "🆓 <b>Бесплатный тариф</b>\n\n"
        "Полный доступ ко всем функциям бота — бесплатно.\n\n"
        "<b>Что включено:</b>\n"
        "• Рассылки по чатам и группам\n"
        "• Автоответчик в ЛС и группах\n"
        "• До 1 аккаунта\n\n"
        "<b>Ограничения:</b>\n"
        "• К каждому сообщению добавляется подпись:\n"
        "<i>━━━━━━━━━━\n🤖 Отправлено через @feAutoSenderBot</i>\n"
        "• Пересылка сообщений (forward) недоступна\n\n"
        "Купите подписку — и реклама исчезнет автоматически."
        + (f"\n\n{status_line}" if status_line else "")
    )
    await safe_edit(callback.message, text, parse_mode="HTML",
                    reply_markup=free_tier_info_keyboard(already_active or bool(has_paid)), retries=0)
    await callback.answer()


@router.callback_query(F.data == "activate_free_tier_confirm")
async def callback_activate_free_tier_confirm(callback: CallbackQuery, db: Database, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return

    has_paid = user.subscription_end and user.subscription_end > now_moscow()
    if has_paid:
        await callback.answer("✅ У вас активная подписка.", show_alert=True)
        return

    if user.subscription_type == "free_ad":
        await callback.answer("ℹ️ Бесплатный тариф уже активен.", show_alert=True)
        return

    await db.activate_free_tier(user.id)
    await callback.answer("✅ Бесплатный тариф активирован!", show_alert=True)
    await callback_subscription(callback, db, state)


@router.callback_query(F.data == "enter_promocode")
async def callback_enter_promocode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SubscriptionStates.waiting_promocode)
    await safe_edit(callback.message, "🎟 Введите промокод:", reply_markup=cancel_keyboard(), retries=0)
    await callback.answer()


@router.message(SubscriptionStates.waiting_promocode)
async def process_promocode(message: Message, state: FSMContext, db: Database):
    if not message.text:
        await message.answer(pe("❌ Введите промокод текстом."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
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

    if not await db.use_promocode(code, user.id, promo.id):
        await message.answer(
            pe("❌ Промокод уже использован или лимит исчерпан."),
            parse_mode="HTML",
            reply_markup=back_to_subscription_keyboard(),
        )
        await state.clear()
        return

    try:
        user = await db.get_user(message.from_user.id)
    except Exception:
        # use_promocode() has already committed both the use and subscription
        # extension.  A read/UI failure must not make that durable success look
        # like an unsuccessful activation.
        logger.error("Promocode %s was used but the updated user could not be read", code, exc_info=True)
        try:
            await state.clear()
        except Exception:
            logger.warning("Could not clear promocode state after successful use", exc_info=True)
        try:
            await message.answer(
                pe(f"✅ Промокод активирован! Добавлено дней: {promo.duration_days}. "
                   "Данные подписки обновятся при следующем открытии раздела."),
                parse_mode="HTML",
                reply_markup=subscription_keyboard(True),
            )
        except Exception:
            logger.warning("Could not send promocode success fallback", exc_info=True)
        return

    if not user:
        logger.error("Promocode %s was used but the updated user no longer exists", code)
        try:
            await state.clear()
        except Exception:
            logger.warning("Could not clear promocode state after successful use", exc_info=True)
        try:
            await message.answer(
                pe(f"✅ Промокод активирован! Добавлено дней: {promo.duration_days}."),
                parse_mode="HTML",
                reply_markup=back_to_subscription_keyboard(),
            )
        except Exception:
            logger.warning("Could not send promocode success fallback", exc_info=True)
        return

    new_end = user.subscription_end

    if user.welcome_pin_msg_id:
        try:
            await message.bot.unpin_chat_message(
                chat_id=message.from_user.id,
                message_id=user.welcome_pin_msg_id,
            )
        except Exception:
            pass
        try:
            await db.update_user_pin_msg_id(user.id, None)
        except Exception:
            logger.warning("Could not clear welcome pin marker after promocode use", exc_info=True)

    try:
        await state.clear()
    except Exception:
        logger.warning("Could not clear promocode state after successful use", exc_info=True)

    try:
        await message.answer(
            pe(f"✅ Промокод активирован!\n\n"
            f"Добавлено дней: {promo.duration_days}\n"
            f"Подписка активна до: {new_end.strftime('%d.%m.%Y %H:%M')}"),
            parse_mode="HTML",
            reply_markup=subscription_keyboard(True),
        )
    except Exception:
        logger.warning("Could not send promocode success message", exc_info=True)


@router.callback_query(F.data == "pay_card")
async def callback_pay_card(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    plan_days = data.get("plan_days", 30)

    manager = await db.get_setting("card_manager_username") or "autosenderkarta"
    price_usdt = await db.get_price(plan_days)
    uah_rate = await get_usd_uah_rate()
    price_uah = round(price_usdt * uah_rate)

    await safe_edit(
        callback.message,
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
        retries=0,
    )
    await callback.answer()
