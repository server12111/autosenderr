import logging

from aiogram import Router, F
from aiogram.types import Message, PreCheckoutQuery
from aiogram.fsm.context import FSMContext

from ..database.db import Database
from ..keyboards.inline import subscription_keyboard
from ..utils.premium_emoji import pe
from .accounts import continue_paid_account_setup_from_message

router = Router()
logger = logging.getLogger(__name__)


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery, db: Database):
    """Must be answered within 10s or Telegram fails the payment client-side.

    The payload is the invoice_id we generated and already stored via
    create_payment() before sending the invoice (see callback_pay_stars /
    callback_pay_account_stars) — reject anything that doesn't match a real,
    not-yet-completed payment for the paying user, so a stale or replayed
    checkout can't be confirmed against a payment that's already been used.
    """
    payment = await db.get_payment_by_invoice(pre_checkout_query.invoice_payload)
    if not payment:
        await pre_checkout_query.answer(ok=False, error_message="Платёж не найден. Попробуйте создать счёт заново.")
        return
    user = await db.get_user(pre_checkout_query.from_user.id)
    if not user or payment.user_id != user.id:
        await pre_checkout_query.answer(ok=False, error_message="Этот счёт не принадлежит вам.")
        return
    if payment.status not in ("pending", None, ""):
        await pre_checkout_query.answer(ok=False, error_message="Этот счёт уже обработан.")
        return
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext, db: Database):
    """Telegram's native push notification that a Stars payment completed —
    there is no polling "check payment" step for this method, unlike
    CryptoBot/TON/Platega."""
    payload = message.successful_payment.invoice_payload
    payment = await db.get_payment_by_invoice(payload)
    if not payment:
        logger.error(f"successful_payment for unknown invoice payload {payload!r}")
        return
    user = await db.get_user(message.from_user.id)
    if not user or payment.user_id != user.id:
        logger.error(f"successful_payment {payload!r} user mismatch (payment.user_id={payment.user_id})")
        return

    if payment.payment_method == "stars":
        price_usdt = payment.price_usdt if payment.price_usdt is not None else payment.amount
        new_end = await db.complete_subscription_payment(payload, price_usdt)
        if not new_end:
            # Already processed (e.g. a duplicate delivery of the same
            # update) — complete_subscription_payment is idempotent, so
            # this is safe to just skip rather than treat as an error.
            return
        await message.answer(
            pe(f"✅ Оплата получена!\n\nВаша подписка активна до {new_end.strftime('%d.%m.%Y %H:%M')}"),
            parse_mode="HTML",
            reply_markup=subscription_keyboard(True),
        )
        return

    if payment.payment_method == "account_stars":
        updated = await db.update_payment_status(payload, "account_setup")
        if not updated:
            fresh = await db.get_payment_by_invoice(payload)
            if fresh and fresh.status == "account_setup":
                await continue_paid_account_setup_from_message(message, state, db, user, payload)
            # else: already "account_used"/"paid" — a duplicate delivery,
            # nothing left to do.
            return
        await continue_paid_account_setup_from_message(message, state, db, user, payload)
        return

    logger.warning(f"successful_payment for invoice {payload!r} has unexpected payment_method {payment.payment_method!r}")
