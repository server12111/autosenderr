from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import referral_keyboard, withdraw_wallet_keyboard, main_menu_keyboard
from ..utils.premium_emoji import pe

router = Router()


class ReferralStates(StatesGroup):
    waiting_wallet = State()


@router.callback_query(F.data == "referral")
async def callback_referral(callback: CallbackQuery, db: Database):
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

    await callback.message.edit_text(pe(text), parse_mode="HTML", reply_markup=referral_keyboard(can_withdraw))
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
    await callback.message.edit_text(
        pe(f"💸 Вывод реферального баланса\n\n"
        f"Сумма к выводу: <b>{user.ref_balance:.2f} USDT</b>\n\n"
        "Введите адрес USDT кошелька (TRC20):"),
        parse_mode="HTML",
        reply_markup=withdraw_wallet_keyboard(),
    )
    await callback.answer()


@router.message(ReferralStates.waiting_wallet)
async def process_wallet(message: Message, state: FSMContext, db: Database):
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

    await db.create_withdrawal_request(user.id, amount, wallet)
    await db.deduct_ref_balance(user.id, amount)
    await state.clear()

    await message.answer(
        pe(f"✅ Заявка на вывод создана!\n\n"
        f"Сумма: <b>{amount:.2f} USDT</b>\n"
        f"Кошелёк: <code>{wallet}</code>\n\n"
        "Администратор обработает заявку в ближайшее время."),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
