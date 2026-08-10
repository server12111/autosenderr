import html
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import referral_keyboard, withdraw_wallet_keyboard, main_menu_keyboard
from ..utils.premium_emoji import pe
from ..utils.tg import safe_answer, safe_edit

router = Router()
logger = logging.getLogger(__name__)


class ReferralStates(StatesGroup):
    waiting_wallet = State()


@router.callback_query(F.data == "referral")
async def callback_referral(callback: CallbackQuery, db: Database, state: FSMContext):
    # The "wallet address" wizard's own "Назад" button routes back here (callback_data="referral")
    # instead of "cancel" — without clearing state, a stray later text message anywhere in the bot
    # would still be caught by process_wallet() and trigger a real withdrawal to that text as a wallet.
    await state.clear()
    user = await db.get_user(callback.from_user.id)

    ref_count = await db.get_referral_count(user.id)
    buyers_count = await db.get_referral_buyers_count(user.id)
    ref_percent = await db.get_ref_percent()
    min_withdraw = await db.get_ref_min_withdraw()

    bot_info = await callback.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.ref_code}"

    can_withdraw = user.ref_balance >= min_withdraw

    text = (
        "🤝 Реферальная программа\n\n"
        f"Ваша реферальная ссылка:\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: {ref_count}\n"
        f"💰 Из них купили подписку: {buyers_count}\n\n"
        f"💵 Ваш баланс: {user.ref_balance:.2f} USDT\n"
        f"💸 Процент с покупок: {ref_percent}%\n"
        f"📤 Минимум для вывода: {min_withdraw} USDT\n\n"
        "Вы получаете процент от каждой покупки подписки вашими рефералами!"
    )

    await safe_edit(callback.message, pe(text), parse_mode="HTML", reply_markup=referral_keyboard(can_withdraw), retries=0)
    await callback.answer()


@router.callback_query(F.data == "withdraw_ref_balance")
async def callback_withdraw(callback: CallbackQuery, state: FSMContext, db: Database):
    user = await db.get_user(callback.from_user.id)
    min_withdraw = await db.get_ref_min_withdraw()

    if user.ref_balance < min_withdraw:
        await callback.answer(
            f"Минимум для вывода: {min_withdraw} USDT. У вас: {user.ref_balance:.2f} USDT",
            show_alert=True,
        )
        return

    await state.set_state(ReferralStates.waiting_wallet)
    await safe_edit(
        callback.message,
        pe(f"💸 Вывод реферального баланса\n\n"
        f"Сумма к выводу: <b>{user.ref_balance:.2f} USDT</b>\n\n"
        "Введите адрес USDT кошелька (TRC20):"),
        parse_mode="HTML",
        reply_markup=withdraw_wallet_keyboard(),
        retries=0,
    )
    await callback.answer()


@router.message(ReferralStates.waiting_wallet)
async def process_wallet(message: Message, state: FSMContext, db: Database):
    if not message.text:
        await message.answer(pe("❌ Введите адрес кошелька текстом."), parse_mode="HTML")
        return
    wallet = message.text.strip()

    if len(wallet) < 10:
        await message.answer(
            pe("❌ Неверный адрес кошелька. Попробуйте ещё раз:"),
            parse_mode="HTML",
            reply_markup=withdraw_wallet_keyboard(),
        )
        return

    user = await db.get_user(message.from_user.id)
    amount = user.ref_balance

    if not await db.create_withdrawal_from_balance(user.id, amount, wallet):
        await state.clear()
        await message.answer("❌ Баланс уже был изменён. Повторите запрос.", parse_mode="HTML")
        return
    # The withdrawal row and balance deduction are one committed DB operation.
    # Follow-up failures must never let a global error handler claim otherwise.
    try:
        await state.clear()
    except Exception:
        logger.warning("Could not clear withdrawal state after request creation", exc_info=True)

    try:
        await safe_answer(
            message,
            pe(f"✅ Заявка на вывод создана!\n\n"
            f"Сумма: <b>{amount:.2f} USDT</b>\n"
            f"Кошелёк: <code>{html.escape(wallet)}</code>\n\n"
            "Администратор обработает заявку в ближайшее время."),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logger.warning("Withdrawal was created but its confirmation could not be sent", exc_info=True)
