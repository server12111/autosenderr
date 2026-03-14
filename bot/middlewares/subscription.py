from datetime import datetime
from typing import Callable, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.context import FSMContext

from ..database.db import Database
from ..config import config
from ..handlers.subscription import SubscriptionStates
from ..handlers.referral import ReferralStates


class SubscriptionMiddleware(BaseMiddleware):
    EXEMPT_CALLBACKS = {
        "main_menu",
        "subscription",
        "buy_subscription",
        "help",
        "pay_cryptobot",
        "pay_ton",
        "pay_card",
        "pay_account_cryptobot",
        "pay_account_ton",
        "pay_account_card",
        "enter_promocode",
        "referral",
        "withdraw_ref_balance",
        "check_channels",
    }

    EXEMPT_CALLBACK_PREFIXES = (
        "check_payment:",
        "check_ton_payment:",
        "check_ton_account:",
        "check_account_payment:",
        "sub_plan:",
    )

    EXEMPT_FSM_STATES = {
        SubscriptionStates.waiting_promocode,
        ReferralStates.waiting_wallet,
    }

    def __init__(self, db: Database):
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Check FSM state — allow exempt states (e.g. promo code input)
        state: FSMContext = data.get("state")
        if state:
            current_state = await state.get_state()
            for exempt_state in self.EXEMPT_FSM_STATES:
                if current_state == exempt_state.state:
                    return await handler(event, data)

        user_id = None
        if isinstance(event, Message):
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            callback_data = event.data or ""
            if callback_data in self.EXEMPT_CALLBACKS:
                return await handler(event, data)
            if callback_data.startswith(self.EXEMPT_CALLBACK_PREFIXES):
                return await handler(event, data)
            user_id = event.from_user.id

        if user_id:
            user = await self.db.get_user(user_id)

            if user and user.is_admin:
                return await handler(event, data)

            if not user or not user.subscription_end:
                await self._show_subscription_required(event)
                return

            if user.subscription_end < datetime.now():
                await self._show_subscription_expired(event)
                return

        return await handler(event, data)

    async def _show_subscription_required(self, event: TelegramObject):
        text = (
            "⚠️ Для использования этой функции требуется подписка.\n\n"
            "Нажмите «Подписка» в главном меню, чтобы приобрести доступ."
        )
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)

    async def _show_subscription_expired(self, event: TelegramObject):
        text = (
            "⚠️ Ваша подписка истекла.\n\n"
            "Нажмите «Подписка» в главном меню, чтобы продлить доступ."
        )
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
