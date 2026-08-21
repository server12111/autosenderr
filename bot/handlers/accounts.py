import asyncio
import html
import ipaddress
import logging
import socket
import time
from datetime import datetime
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon.errors import PasswordHashInvalidError, PhoneCodeInvalidError

from ..database.db import Database, POOL_API_CREDENTIAL_ACCOUNT_CAP
from ..keyboards.inline import (
    accounts_keyboard,
    account_menu_keyboard,
    delete_account_confirm_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    add_account_proxy_keyboard,
    retry_or_skip_proxy_keyboard,
    add_account_api_keyboard,
    account_payment_keyboard,
    account_payment_method_keyboard,
    ton_account_payment_keyboard,
    code_input_keyboard,
    back_to_menu_keyboard,
)
from ..userbot.manager import UserbotManager, _parse_proxy, _DEVICE_POOL
from ..config import config, get_stars_price
from ..services import CryptoBotService, TonPaymentService, MailingService
from ..utils.errors import friendly_error
from ..utils.time_utils import now_moscow
from ..utils.tg import safe_answer, safe_edit
from ..utils.proxy import normalize_proxy_string


def _is_blocked_proxy_address(address: str) -> bool:
    """Return True for IP ranges that must never be probed as a user proxy."""
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve_public_proxy_host(host: str) -> str | None:
    """Resolve *host* and fail closed unless every result is publicly routable.

    Validation is performed on resolved addresses rather than the hostname so
    names such as ``internal.example`` cannot bypass the proxy SSRF guard.
    Rejecting a mixed public/private answer also prevents a resolver from
    choosing an internal address after a superficially safe validation.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
        addresses = {info[4][0] for info in infos}
        if not addresses or any(_is_blocked_proxy_address(address) for address in addresses):
            return None
        return next(iter(addresses))
    except (OSError, ValueError):
        return None


async def _test_proxy_connection(host: str, port: int) -> bool:
    address = await _resolve_public_proxy_host(host)
    if not address:
        return False
    try:
        # Use the checked numeric address, not the original hostname, so a
        # DNS rebind between validation and connect cannot reach an internal
        # service.
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=5)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False
from ..utils.premium_emoji import pe


def _proxy_status_text(account) -> str:
    # Pool-assigned proxies must stay invisible to the account owner — shown
    # exactly as if no proxy were configured at all.
    if not account.proxy or account.proxy_pool_id:
        return "🌐 Прокси: не настроен"
    return f"🌐 {html.escape(account.proxy)}"


logger = logging.getLogger(__name__)
router = Router()

_last_pool_exhausted_notify = 0.0
_POOL_EXHAUSTED_NOTIFY_COOLDOWN = 1800  # seconds — avoid spamming admins


async def _disconnect_pending_client(state: FSMContext):
    """If an add-account flow was mid-way (waiting_code/waiting_password),
    its live Telethon client sits in FSM data. Call before clearing/
    restarting the flow so that client doesn't leak an open connection."""
    data = await state.get_data()
    client = data.get("client")
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _notify_pool_exhausted(bot):
    global _last_pool_exhausted_notify
    now = time.monotonic()
    if now - _last_pool_exhausted_notify < _POOL_EXHAUSTED_NOTIFY_COOLDOWN:
        return
    _last_pool_exhausted_notify = now
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                pe(
                    "⚠️ <b>Пул прокси заполнен</b>\n\n"
                    "Все прокси в пуле уже обслуживают максимум аккаунтов. "
                    "Новые аккаунты подключаются без прокси.\n\n"
                    "Добавьте новые прокси в админ-панели."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


class RenameAccountStates(StatesGroup):
    waiting_name = State()


class SetProxyStates(StatesGroup):
    waiting_proxy = State()


class AddAccountStates(StatesGroup):
    waiting_proxy    = State()   # крок 1 (опційно)
    waiting_api_id   = State()   # крок 2a (опційно)
    waiting_api_hash = State()   # крок 2b (опційно)
    waiting_phone    = State()   # крок 3
    waiting_code     = State()   # крок 4
    waiting_password = State()   # 2FA


def _accounts_list_text(accounts: list, page: int) -> str:
    """Per-page account listing — capped to LIST_PAGE_SIZE lines so a large
    account list can't exceed Telegram's ~4096-char message text limit,
    matching the keyboard's own pagination."""
    from ..keyboards.inline import LIST_PAGE_SIZE
    total_pages = max(1, (len(accounts) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_accounts = accounts[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]

    text = "👤 Ваши аккаунты:\n\n"
    if page_accounts:
        for acc in page_accounts:
            status = "🟢" if acc.is_active else "🔴"
            ar = "✅" if acc.autoresponder_enabled else "❌"
            gr = "✅" if acc.group_autoresponder_enabled else "❌"
            text += f"{status} {html.escape(acc.display_name)}\n  └ Автоприветствие: {ar}  Групповой автоответ: {gr}\n"
    else:
        text += "У вас пока нет добавленных аккаунтов.\n"
    if total_pages > 1:
        text += f"\nСтраница {page + 1}/{total_pages}."
    text += "\nВыберите аккаунт или добавьте новый:"
    return text


@router.callback_query(F.data.startswith("accounts_page:"))
async def callback_accounts_page(callback: CallbackQuery, db: Database):
    page = int(callback.data.split(":")[1])
    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)
    await safe_edit(
        callback.message, pe(_accounts_list_text(accounts, page)), parse_mode="HTML",
        reply_markup=accounts_keyboard(accounts, page=page),
        retries=0,
    )
    await callback.answer()


@router.callback_query(F.data == "accounts")
async def callback_accounts(callback: CallbackQuery, db: Database, state: FSMContext):
    # Always-reachable nav button — without clearing state, a stray text
    # message sent after navigating here from mid-wizard would still be
    # caught by whatever text handler that old state points to and get
    # silently applied to it (same bug class already fixed for the
    # referral wallet flow, see callback_referral).
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)

    await callback.message.edit_text(
        pe(_accounts_list_text(accounts, 0)), parse_mode="HTML",
        reply_markup=accounts_keyboard(accounts, page=0),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:"))
async def callback_account_menu(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account_for_user(account_id, callback.from_user.id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    ar_status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    gr_status = "✅ Включён" if account.group_autoresponder_enabled else "❌ Выключен"
    proxy_status = _proxy_status_text(account)
    sponsor_status = "✅ Включена" if account.auto_subscribe_sponsors else "❌ Выключена"

    text = pe(
        f"📱 Аккаунт: {html.escape(account.display_name)}\n"
        f"📞 Номер: {html.escape(account.phone)}\n\n"
        f"🤖 Автоприветствие: {ar_status}\n"
        f"💬 Групповой автоответчик: {gr_status}\n"
        f"🤖 Автоподписка на спонсоров: {sponsor_status}\n"
        f"{proxy_status}\n"
        f"📅 Добавлен: {account.created_at.strftime('%d.%m.%Y') if account.created_at else '—'}\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors)
    )
    await callback.answer()


@router.callback_query(F.data == "account_payment_methods")
async def callback_account_payment_methods(callback: CallbackQuery, db: Database, ton_service: TonPaymentService = None):
    has_ton = bool(config.TON_WALLET_ADDRESS and ton_service)
    if has_ton:
        await safe_edit(callback.message, pe("⏳ Загружаем способы оплаты..."), parse_mode="HTML", retries=0)
    await callback.answer()

    lines = [f"💎 CryptoBot — {config.EXTRA_ACCOUNT_PRICE} {config.SUBSCRIPTION_CURRENCY}"]
    if has_ton:
        try:
            ton_amount = await asyncio.wait_for(ton_service.calculate_ton_amount(config.EXTRA_ACCOUNT_PRICE), timeout=12)
        except asyncio.TimeoutError:
            ton_amount = None
        lines.append(
            f"💠 GRAM(TON) — ~{ton_amount} GRAM(TON) (≈ {config.EXTRA_ACCOUNT_PRICE} USDT)"
            if ton_amount
            else f"💠 GRAM(TON) — ≈ {config.EXTRA_ACCOUNT_PRICE} USDT в GRAM(TON)"
        )
    lines.append(f"⭐️ Telegram Stars — {config.STARS_EXTRA_ACCOUNT_PRICE}")
    text = pe(
        f"➕ Добавление аккаунта\n\n"
        f"⚠️ Вы достигли лимита бесплатных аккаунтов.\n\n"
        f"Выберите способ оплаты:\n\n" + "\n".join(lines)
    )
    await safe_edit(callback.message, text, parse_mode="HTML", reply_markup=account_payment_method_keyboard(), retries=0)


@router.callback_query(F.data == "add_account")
async def callback_add_account(callback: CallbackQuery, state: FSMContext, db: Database, ton_service: TonPaymentService = None):
    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)

    has_paid = user.subscription_end and user.subscription_end > now_moscow()
    account_limit = 1 if (user.subscription_type == "free_ad" and not has_paid) else config.FREE_ACCOUNTS_LIMIT

    if accounts_count >= account_limit:
        has_ton = bool(config.TON_WALLET_ADDRESS and ton_service)
        if has_ton:
            await safe_edit(callback.message, pe("⏳ Загружаем способы оплаты..."), parse_mode="HTML", retries=0)
        await callback.answer()

        lines = [f"💎 CryptoBot — {config.EXTRA_ACCOUNT_PRICE} {config.SUBSCRIPTION_CURRENCY}"]
        if has_ton:
            try:
                ton_amount = await asyncio.wait_for(ton_service.calculate_ton_amount(config.EXTRA_ACCOUNT_PRICE), timeout=12)
            except asyncio.TimeoutError:
                ton_amount = None
            lines.append(
                f"💠 GRAM(TON) — ~{ton_amount} GRAM(TON) (≈ {config.EXTRA_ACCOUNT_PRICE} USDT)"
                if ton_amount
                else f"💠 GRAM(TON) — ≈ {config.EXTRA_ACCOUNT_PRICE} USDT в GRAM(TON)"
            )
        lines.append(f"⭐️ Telegram Stars — {config.STARS_EXTRA_ACCOUNT_PRICE}")
        text = pe(
            f"➕ Добавление аккаунта\n\n"
            f"⚠️ Вы достигли лимита в {account_limit} аккаунтов.\n\n"
            f"Выберите способ оплаты:\n\n" + "\n".join(lines)
        )
        await safe_edit(callback.message, text, parse_mode="HTML", reply_markup=account_payment_method_keyboard(), retries=0)
        return

    remaining = account_limit - accounts_count
    text = pe(
        "➕ Добавление аккаунта\n\n"
        f"📊 У вас {accounts_count}/{account_limit} аккаунтов\n"
        f"Осталось: {remaining}\n\n"
        "<b>Шаг 1 из 3</b>\n\n"
        "Хотите использовать прокси SOCKS5?\n\n"
        "Если да — введите в формате:\n"
        "<code>socks5://host:port</code>\n"
        "или <code>socks5://user:pass@host:port</code>"
    )

    await _disconnect_pending_client(state)
    await state.clear()
    await state.update_data(account_setup_allowed=True)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=add_account_proxy_keyboard())
    await callback.answer()


async def _create_cryptobot_account_payment(callback: CallbackQuery, db: Database, accounts_count: int, account_limit: int = config.FREE_ACCOUNTS_LIMIT):
    """Create CryptoBot invoice for extra account."""
    extra_cost = config.EXTRA_ACCOUNT_PRICE
    crypto_service = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)
    await safe_edit(callback.message, pe("⏳ Создаём счёт..."), parse_mode="HTML", retries=0)
    invoice = await crypto_service.create_invoice(
        amount=extra_cost,
        currency=config.SUBSCRIPTION_CURRENCY,
        description=f"Дополнительный аккаунт (#{accounts_count + 1})",
    )

    if not invoice:
        error_msg = crypto_service.last_error.message if crypto_service.last_error else "Неизвестная ошибка"
        await safe_edit(
            callback.message,
            pe(f"❌ Не удалось создать счёт: {html.escape(error_msg)}"),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
            retries=0,
        )
        return

    user = await db.get_user(callback.from_user.id)
    if not user:
        await safe_edit(
            callback.message,
            pe("❌ Пользователь не найден. Попробуйте начать заново."),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
            retries=0,
        )
        return
    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice.invoice_id,
        amount=invoice.amount,
        currency=invoice.currency,
        payment_method="account_cryptobot",
    )

    text = pe(
        f"➕ Добавление аккаунта\n\n"
        f"⚠️ Вы достигли лимита в {account_limit} аккаунтов.\n\n"
        f"💰 Стоимость дополнительного аккаунта: <b>{extra_cost} USDT</b>\n\n"
        "Оплатите счёт и нажмите «Проверить оплату»."
    )
    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await safe_edit(
        callback.message, text, parse_mode="HTML",
        reply_markup=account_payment_keyboard(invoice.pay_url, invoice.invoice_id, support_username=support),
        retries=0,
    )


@router.callback_query(F.data == "pay_account_cryptobot")
async def callback_pay_account_cryptobot(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)
    await callback.answer()
    await _create_cryptobot_account_payment(callback, db, accounts_count)


@router.callback_query(F.data == "pay_account_stars")
async def callback_pay_account_stars(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)

    # Same reasoning as callback_pay_stars in subscription.py — the record
    # must exist before the invoice is sent, since Telegram calls back via
    # successful_payment with no external "check payment" polling step.
    invoice_id = f"stars_acc_{user.telegram_id}_{time.time_ns()}"
    await db.create_payment(
        user_id=user.id,
        invoice_id=invoice_id,
        amount=config.STARS_EXTRA_ACCOUNT_PRICE,
        currency="XTR",
        payment_method="account_stars",
    )

    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Дополнительный аккаунт AutoSender",
        description="Оплата дополнительного Telegram-аккаунта для рассылок сверх бесплатного лимита.",
        payload=invoice_id,
        currency="XTR",
        prices=[LabeledPrice(label="Доп. аккаунт", amount=config.STARS_EXTRA_ACCOUNT_PRICE)],
    )
    await callback.message.edit_text(
        pe(f"⭐️ Счёт на {config.STARS_EXTRA_ACCOUNT_PRICE} Stars отправлен ниже — "
        "оплатите его, и добавление аккаунта продолжится автоматически."),
        parse_mode="HTML",
        reply_markup=account_payment_method_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "pay_account_ton")
async def callback_pay_account_ton(
    callback: CallbackQuery, db: Database, ton_service: TonPaymentService
):
    user = await db.get_user(callback.from_user.id)
    comment = f"acc_{user.telegram_id}_{time.time_ns()}"

    await safe_edit(callback.message, pe("⏳ Получаем курс GRAM(TON)..."), parse_mode="HTML", retries=0)
    # Clear the button spinner before the rate lookup below, which can take
    # several seconds — same fix as callback_pay_ton in subscription.py.
    await callback.answer()

    try:
        amount = await asyncio.wait_for(ton_service.calculate_ton_amount(config.EXTRA_ACCOUNT_PRICE), timeout=12)
    except asyncio.TimeoutError:
        amount = None
    if not amount:
        await safe_edit(
            callback.message,
            pe("❌ Не удалось получить курс GRAM(TON). Попробуйте позже."),
            parse_mode="HTML",
            reply_markup=account_payment_method_keyboard(),
            retries=0,
        )
        return

    await db.create_payment(
        user_id=user.id,
        invoice_id=comment,
        amount=amount,
        currency="TON",
        payment_method="account_ton",
    )

    pay_url = ton_service.generate_payment_link(amount, comment)

    text = pe(
        f"💠 Оплата дополнительного аккаунта через GRAM(TON)\n\n"
        f"Сумма: <b>{amount} GRAM(TON)</b> (≈ {config.EXTRA_ACCOUNT_PRICE} USDT)\n\n"
        f"Кошелёк: <code>{config.TON_WALLET_ADDRESS}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        f"Нажмите кнопку ниже для оплаты через Tonkeeper.\n"
        f"<b>Важно:</b> комментарий должен совпадать точно!\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    await safe_edit(
        callback.message, text, parse_mode="HTML",
        reply_markup=ton_account_payment_keyboard(pay_url, comment, support_username=support),
        retries=0,
    )


@router.callback_query(F.data.startswith("check_ton_account:"))
async def callback_check_ton_account(
    callback: CallbackQuery, state: FSMContext, db: Database, ton_service: TonPaymentService
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
    if payment.payment_method != "account_ton":
        await callback.answer("⛔ Этот платёж не предназначен для добавления аккаунта", show_alert=True)
        return

    if payment.status == "account_setup":
        await _continue_paid_account_setup(callback, state, db, user, comment)
        return
    if payment.status in ("paid", "account_used"):
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return

    is_paid = await ton_service.check_payment(payment.amount, comment)

    if not is_paid:
        await callback.answer("❌ Оплата ещё не получена. Попробуйте позже.", show_alert=True)
        return

    updated = await db.update_payment_status(comment, "account_setup")
    if not updated:
        payment = await db.get_payment_by_invoice(comment)
        if payment and payment.status == "account_setup":
            await _continue_paid_account_setup(callback, state, db, user, comment)
            return
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return
    await _continue_paid_account_setup(callback, state, db, user, comment)


@router.callback_query(F.data.startswith("check_account_payment:"))
async def callback_check_account_payment(callback: CallbackQuery, state: FSMContext, db: Database):
    invoice_id = callback.data.split(":")[1]
    payment = await db.get_payment_by_invoice(invoice_id)
    user = await db.get_user(callback.from_user.id)
    if not payment or not user or payment.user_id != user.id:
        await callback.answer("⛔ Платёж не найден", show_alert=True)
        return
    if payment.payment_method != "account_cryptobot":
        await callback.answer("⛔ Этот платёж не предназначен для добавления аккаунта", show_alert=True)
        return
    if payment.status == "account_setup":
        await _continue_paid_account_setup(callback, state, db, user, invoice_id)
        return
    if payment.status in ("paid", "account_used"):
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return
    crypto_service = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)
    paid = await crypto_service.check_invoice_paid(invoice_id)

    if not paid:
        await callback.answer("❌ Оплата ещё не получена. Попробуйте позже.", show_alert=True)
        return

    updated = await db.update_payment_status(invoice_id, "account_setup")
    if not updated:
        payment = await db.get_payment_by_invoice(invoice_id)
        if payment and payment.status == "account_setup":
            await _continue_paid_account_setup(callback, state, db, user, invoice_id)
            return
        await callback.answer("✅ Этот платёж уже обработан", show_alert=True)
        return
    await _continue_paid_account_setup(callback, state, db, user, invoice_id)


async def _continue_paid_account_setup(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    user,
    invoice_id: str,
):
    """Restore the wizard from the durable paid entitlement.

    A successful payment is never represented solely by MemoryStorage: if a
    DB/FSM/UI call below fails, tapping the same payment button resumes this
    function while the payment remains in ``account_setup`` state.
    """
    try:
        accounts_count = await db.count_user_accounts(user.id)
        text = pe(
            "✅ Оплата получена!\n\n"
            "➕ Добавление аккаунта\n\n"
            f"📊 У вас {accounts_count} аккаунтов\n\n"
            "<b>Шаг 1 из 3</b>\n\n"
            "Хотите использовать прокси SOCKS5?\n\n"
            "Если да — введите в формате:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>"
        )
        await _disconnect_pending_client(state)
        await state.clear()
        await state.update_data(
            account_setup_allowed=True,
            account_payment_invoice=invoice_id,
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=add_account_proxy_keyboard(),
        )
        await callback.answer()
    except Exception as e:
        logger.warning(
            "Paid extra-account setup %s could not be restored yet: %s",
            invoice_id,
            e,
        )
        try:
            await callback.answer(
                "Оплата сохранена. Нажмите «Проверить оплату» ещё раз, чтобы продолжить добавление аккаунта.",
                show_alert=True,
            )
        except Exception:
            pass


async def continue_paid_account_setup_from_message(
    message: Message,
    state: FSMContext,
    db: Database,
    user,
    invoice_id: str,
):
    """Same restoration as _continue_paid_account_setup, for the Stars flow.

    A Telegram Stars payment is confirmed via an incoming successful_payment
    Message, not a callback button tap — there is no message to edit, so this
    sends a fresh one instead. Used by bot/handlers/stars_payment.py.
    """
    try:
        accounts_count = await db.count_user_accounts(user.id)
        text = pe(
            "✅ Оплата получена!\n\n"
            "➕ Добавление аккаунта\n\n"
            f"📊 У вас {accounts_count} аккаунтов\n\n"
            "<b>Шаг 1 из 3</b>\n\n"
            "Хотите использовать прокси SOCKS5?\n\n"
            "Если да — введите в формате:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>"
        )
        await _disconnect_pending_client(state)
        await state.clear()
        await state.update_data(
            account_setup_allowed=True,
            account_payment_invoice=invoice_id,
        )
        await message.answer(text, parse_mode="HTML", reply_markup=add_account_proxy_keyboard())
    except Exception as e:
        logger.warning(
            "Paid extra-account setup %s (Stars) could not be restored yet: %s",
            invoice_id,
            e,
        )
        try:
            await message.answer(
                pe("Оплата сохранена. Откройте «➕ Добавить аккаунт» ещё раз, чтобы продолжить."),
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.callback_query(F.data == "add_account_set_proxy")
async def callback_add_account_set_proxy(callback: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("account_setup_allowed"):
        await callback.answer("Сессия добавления аккаунта истекла", show_alert=True)
        return
    await callback.message.edit_text(
        "🌐 Введите прокси в формате:\n"
        "<code>socks5://host:port</code>\n"
        "или с авторизацией:\n"
        "<code>socks5://user:pass@host:port</code>",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_proxy)
    await callback.answer()


@router.callback_query(F.data == "add_account_skip_proxy")
async def callback_add_account_skip_proxy(callback: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("account_setup_allowed"):
        await callback.answer("Сессия добавления аккаунта истекла", show_alert=True)
        return
    await state.update_data(proxy=None)
    await _ask_api_step(callback.message, can_edit=True)
    await callback.answer()


@router.message(AddAccountStates.waiting_proxy)
async def process_proxy(message: Message, state: FSMContext):
    text = normalize_proxy_string(message.text.strip() if message.text else "")

    if not text.startswith("socks5://"):
        await message.answer(
            "❌ Неверный формат. Введите прокси:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>",
            reply_markup=retry_or_skip_proxy_keyboard(),
        )
        return

    from urllib.parse import urlparse
    parsed = urlparse(text)
    if not parsed.hostname or not parsed.port:
        await message.answer(
            "❌ Не удалось распознать хост или порт.\n"
            "Проверьте формат: <code>socks5://host:port</code>",
            reply_markup=cancel_keyboard(),
        )
        return

    if not await _test_proxy_connection(parsed.hostname, parsed.port):
        await message.answer(
            pe(f"❌ <b>Прокси не подходит!</b>\n\n"
            f"Не удалось подключиться к <code>{html.escape(parsed.hostname)}:{parsed.port}</code>.\n\n"
            f"Проверьте:\n"
            f"• Правильность адреса и порта\n"
            f"• Логин и пароль (если есть)\n"
            f"• Что прокси рабочий и не заблокирован\n\n"
            f"Введите другой прокси или нажмите «Пропустить»:"),
            parse_mode="HTML",
            reply_markup=retry_or_skip_proxy_keyboard(),
        )
        return

    await state.update_data(proxy=text)
    await state.set_state(None)
    await _ask_api_step(message, can_edit=False)


async def _ask_api_step(target, can_edit: bool = False):
    text = pe(
        "➕ Добавление аккаунта\n\n"
        "<b>Шаг 2 из 3</b>\n\n"
        "Хотите использовать собственный API ID и Hash?\n\n"
        "Получить: https://my.telegram.org\n\n"
        "Если нет — используются стандартные настройки."
    )
    if can_edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=add_account_api_keyboard())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=add_account_api_keyboard())


@router.callback_query(F.data == "add_account_set_api")
async def callback_add_account_set_api(callback: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("account_setup_allowed"):
        await callback.answer("Сессия добавления аккаунта истекла", show_alert=True)
        return
    await callback.message.edit_text(
        pe("🔑 Введите API ID (число):\n\n"
        "Получить: https://my.telegram.org"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_api_id)
    await callback.answer()


@router.callback_query(F.data == "add_account_skip_api")
async def callback_add_account_skip_api(callback: CallbackQuery, state: FSMContext, db: Database):
    if not (await state.get_data()).get("account_setup_allowed"):
        await callback.answer("Сессия добавления аккаунта истекла", show_alert=True)
        return
    # Many phone numbers sharing one api_id is its own spam signal Telegram
    # tracks globally (not just in chats this bot touches) — same reasoning
    # as the proxy pool, so pull from the api-credential pool before ever
    # falling back to the single shared config default.
    pool_cred = await db.get_least_loaded_api_credential(cap=POOL_API_CREDENTIAL_ACCOUNT_CAP)
    if pool_cred:
        await state.update_data(
            api_id=pool_cred.api_id,
            api_hash=pool_cred.api_hash,
            api_credential_pool_id=pool_cred.id,
        )
    else:
        await state.update_data(
            api_id=config.DEFAULT_API_ID,
            api_hash=config.DEFAULT_API_HASH,
        )
    await callback.message.edit_text(
        pe("➕ Добавление аккаунта\n\n"
        "<b>Шаг 3 из 3</b>\n\n"
        "Введите номер телефона в международном формате:\n"
        "Например: <code>+380991234567</code>"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_phone)
    await callback.answer()


@router.message(AddAccountStates.waiting_api_id)
async def process_api_id(message: Message, state: FSMContext):
    try:
        api_id = int((message.text or "").strip())
    except ValueError:
        await message.answer(pe("❌ API ID должен быть числом. Попробуйте снова:"), parse_mode="HTML")
        return

    await state.update_data(api_id=api_id)
    await message.answer("Введите API Hash:", reply_markup=cancel_keyboard())
    await state.set_state(AddAccountStates.waiting_api_hash)


@router.message(AddAccountStates.waiting_api_hash)
async def process_api_hash(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    if not message.text:
        await message.answer(pe("❌ Введите API Hash текстом."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    api_hash = message.text.strip()

    if len(api_hash) < 20:
        await message.answer(pe("❌ API Hash слишком короткий. Попробуйте снова:"), parse_mode="HTML")
        return

    # Manual entry always wins — clear any api_credential_pool_id a prior
    # "skip" tap in this same session might have set, so the persisted
    # account never ends up with pool bookkeeping pointing at credentials
    # that don't match what was actually typed here.
    await state.update_data(api_hash=api_hash, api_credential_pool_id=None)
    data = await state.get_data()

    if data.get("phone"):
        # Phone is already known (retry after API credentials error) — connect immediately
        await _connect_and_send_code(message, state, data, db, userbot_manager)
    else:
        await message.answer(
            pe("➕ Добавление аккаунта\n\n"
            "<b>Шаг 3 из 3</b>\n\n"
            "Введите номер телефона в международном формате:\n"
            "Например: <code>+380991234567</code>"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        await state.set_state(AddAccountStates.waiting_phone)


async def _connect_and_send_code(message: Message, state: FSMContext, data: dict, db: Database, userbot_manager: UserbotManager):
    """Connect to Telegram and request login code. On API credentials error, asks for new ones."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    phone = data["phone"]

    # If the user didn't type their own proxy, decide the pool/shared proxy
    # to use NOW — before the very first connection — instead of assigning
    # it only after login succeeds. Assigning it afterward meant the login
    # itself (code request + sign-in) always went out unproxied, which is
    # exactly the "connected from the wrong region" symptom this fixes.
    auto_proxy: Optional[str] = None
    auto_proxy_pool_id: Optional[int] = None
    connect_proxy = data.get("proxy")
    if not connect_proxy:
        user = await db.get_user(message.from_user.id)
        auto_proxy, auto_proxy_pool_id, pool_exhausted = await db.pick_proxy_for_new_account(user.id)
        connect_proxy = auto_proxy
        await state.update_data(auto_proxy=auto_proxy, auto_proxy_pool_id=auto_proxy_pool_id)
        if pool_exhausted:
            asyncio.create_task(_notify_pool_exhausted(message.bot))

    has_proxy = bool(connect_proxy)

    if has_proxy:
        # Never mention "прокси" here — pool-assigned proxies must stay
        # invisible to the account owner, and even for a user's own proxy
        # there's no need to call it out on this screen.
        status_msg = await message.answer(
            pe("⏳ Подключаемся к Telegram...\n<i>Это может занять до 60 секунд</i>"),
            parse_mode="HTML",
        )
    else:
        status_msg = await message.answer(pe("⏳ Отправляем код на телефон..."), parse_mode="HTML")

    client = None
    try:
        device = _DEVICE_POOL[abs(hash(phone)) % len(_DEVICE_POOL)]
        client = TelegramClient(
            StringSession(), data["api_id"], data["api_hash"],
            proxy=_parse_proxy(connect_proxy),
            device_model=device["device_model"],
            system_version=device["system_version"],
            app_version=device["app_version"],
            lang_code="uk",
            system_lang_code="uk-UA",
            connection_retries=3 if has_proxy else 1,
            retry_delay=2,
            timeout=30 if has_proxy else 15,
        )

        connect_timeout = 60 if has_proxy else 20
        await asyncio.wait_for(client.connect(), timeout=connect_timeout)
        sent = await asyncio.wait_for(client.send_code_request(phone), timeout=30)

        await state.update_data(client=client, entered_code="", phone_code_hash=sent.phone_code_hash)
        try:
            await status_msg.delete()
        except Exception:
            pass
        code_message = await message.answer(
            pe("📱 Код отправлен!\n\n"
            "📲 Проверьте приложение Telegram на других ваших устройствах или Telegram Web — "
            "код придёт туда.\n\n"
            "🔢 Введите код с помощью кнопок:\n\n"
            "Код: ▫️▫️▫️▫️▫️"),
            parse_mode="HTML",
            reply_markup=code_input_keyboard(),
        )
        await state.update_data(code_message_id=code_message.message_id)
        await state.set_state(AddAccountStates.waiting_code)

    except asyncio.TimeoutError:
        if client:
            await client.disconnect()
        try:
            await status_msg.delete()
        except Exception:
            pass
        if auto_proxy_pool_id:
            # A brand-new account has no DB row yet to route through the
            # normal dead-proxy-detection path — without this, the proxy
            # never gets marked dead, and the very next signup attempt
            # would likely land on this same "active" but actually-dead
            # proxy again (pick_proxy_for_new_account has no way to know).
            asyncio.create_task(userbot_manager._handle_pool_proxy_failure(auto_proxy_pool_id, "onboarding connect timeout"))
        # Pool-assigned proxies must stay invisible even in error messages —
        # only mention "прокси" if the user set one themselves.
        hint = "или прокси " if data.get("proxy") else ""
        await message.answer(
            pe("❌ Превышено время ожидания.\n\n"
            f"Telegram не отвечает. Проверьте интернет-соединение {hint}и попробуйте снова."),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()

    except Exception as e:
        if client:
            await client.disconnect()
        try:
            await status_msg.delete()
        except Exception:
            pass
        if auto_proxy_pool_id and isinstance(e, (OSError, ConnectionError)):
            # Same reasoning as the TimeoutError branch above.
            asyncio.create_task(userbot_manager._handle_pool_proxy_failure(auto_proxy_pool_id, str(e)))
        err = str(e)
        if "Connection to Telegram failed" in err or "ConnectionError" in type(e).__name__:
            proxy_hint = "• Telegram заблокирован — попробуйте добавить прокси\n\n" if data.get("proxy") else ""
            await message.answer(
                pe("❌ Не удалось подключиться к Telegram.\n\n"
                "Возможные причины:\n"
                "• Нет интернета на сервере\n"
                f"{proxy_hint}"
                "Попробуйте снова позже."),
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            await state.clear()
        elif "api_id" in err.lower() or "api_hash" in err.lower() or "api_id/api_hash" in err.lower():
            # Keep phone/proxy in state, ask for new API credentials
            await state.update_data(api_id=None, api_hash=None)
            await message.answer(
                pe("❌ <b>Неверные API ID или API Hash.</b>\n\n"
                "Стандартные учётные данные не подходят для этого номера.\n\n"
                "Введите свои API ID и Hash (номер телефона сохранён, вводить снова не нужно):\n"
                "Получить: <a href=\"https://my.telegram.org\">my.telegram.org</a>"),
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            await state.set_state(AddAccountStates.waiting_api_id)
        elif "phone" in err.lower() or "invalid" in err.lower():
            # Same reasoning as the api_id/api_hash branch above — keep
            # proxy/API credentials in state and just re-ask for the phone,
            # instead of wiping the whole wizard over a typo.
            await state.update_data(phone=None)
            await message.answer(
                pe("❌ Неверный номер телефона.\n\nПроверьте формат: +380991234567\n\nВведите номер снова:"),
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            await state.set_state(AddAccountStates.waiting_phone)
        else:
            await message.answer(
                pe(friendly_error(e, default="❌ Не удалось отправить код. Попробуйте снова позже.")),
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            await state.clear()


@router.message(AddAccountStates.waiting_phone)
async def process_phone(
    message: Message, state: FSMContext, userbot_manager: UserbotManager, db: Database
):
    if not message.text:
        await message.answer(pe("❌ Введите номер телефона."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    phone = message.text.strip()

    if not phone.startswith("+"):
        await message.answer(pe("❌ Номер должен начинаться с +. Попробуйте снова:"), parse_mode="HTML")
        return

    data = await state.get_data()
    if data.get("connecting"):
        # Already connecting on a previous phone number — ignore instead of
        # spawning a second Telethon client that would leak on completion.
        return

    await state.update_data(phone=phone, connecting=True)
    data = await state.get_data()
    try:
        await _connect_and_send_code(message, state, data, db, userbot_manager)
    finally:
        try:
            await state.update_data(connecting=False)
        except Exception:
            pass


@router.message(AddAccountStates.waiting_code)
async def process_code(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    code = (message.text or "").strip()
    if not code.isdigit() or len(code) < 5:
        return
    await state.update_data(entered_code=code)
    await _confirm_code(message, state, db, userbot_manager)


async def _persist_new_account(
    db: Database,
    userbot_manager: UserbotManager,
    telegram_user_id: int,
    phone: str,
    api_id: int,
    api_hash: str,
    session_string: str,
    proxy_str: Optional[str],
    auto_proxy: Optional[str],
    auto_proxy_pool_id: Optional[int],
    account_payment_invoice: Optional[str] = None,
    api_credential_pool_id: Optional[int] = None,
):
    """Save the account row after a successful Telethon sign_in.

    By this point the Telegram session is already live — discarding it on a
    transient DB hiccup (e.g. SQLite busy under load from 260+ concurrently
    writing accounts) would strand a session the bot has no record of and no
    way to recover. Retry a few times before giving up.
    """
    last_exc = None
    saved_proxy = proxy_str if proxy_str else auto_proxy
    saved_proxy_pool_id = None if proxy_str else auto_proxy_pool_id
    for attempt in range(3):
        try:
            user = await db.get_user(telegram_user_id)
            account_id = await db.create_account(
                user_id=user.id,
                phone=phone,
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
                account_payment_invoice=account_payment_invoice,
                proxy=saved_proxy,
                proxy_pool_id=saved_proxy_pool_id,
                api_credential_pool_id=api_credential_pool_id,
            )
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
            else:
                raise last_exc

    # The DB row exists now — start the real (non-onboarding) Telethon client
    # so autoresponder/mailing event handlers are registered immediately.
    # Without this, a freshly added account sat completely idle (no
    # NewMessage handler registered at all) until the next full bot restart,
    # an admin manually restarting clients, or the user happening to touch
    # the proxy setting — best-effort only, a failure here doesn't undo the
    # account creation that already succeeded. Both the lookup and the
    # start are inside ONE try/except: the account row is already saved by
    # this point, so nothing from here on should be able to bubble up and
    # make an already-successful creation look like it failed (that
    # exact bug — get_account() itself hitting a transient DB error with
    # no protection — was reported as "session appeared but bot said
    # error" right after this block was first added).
    try:
        account = await db.get_account(account_id)
        if account:
            await asyncio.wait_for(userbot_manager.start_client(account), timeout=30)
    except Exception as e:
        logger.warning(f"Failed to start client for newly added account {account_id}: {e}")

    return user, account_id


@router.message(AddAccountStates.waiting_password)
async def process_password(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    if not message.text:
        await message.answer(pe("❌ Введите пароль текстом."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    password = message.text.strip()
    data = await state.get_data()
    if not data.get("account_setup_allowed"):
        await state.clear()
        return
    client = data.get("client")

    if not client:
        await message.answer(
            pe("❌ Сессия истекла. Начните заново."),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    if data.get("confirming"):
        # A previous submission is still being processed against the same
        # Telethon client — ignore the duplicate instead of racing a second
        # sign_in on it (mirrors the same guard in _confirm_code).
        return
    await state.update_data(confirming=True)

    try:
        # Wrapped like the connect()/send_code_request() calls earlier in
        # this same wizard — if the proxy dies between the code being sent
        # and the user actually typing the password, this could otherwise
        # hang forever with no feedback.
        await asyncio.wait_for(client.sign_in(password=password), timeout=30)

        session_string = client.session.save()
        await client.disconnect()

        user, account_id = await _persist_new_account(
            db,
            userbot_manager,
            message.from_user.id,
            data["phone"],
            data["api_id"],
            data["api_hash"],
            session_string,
            data.get("proxy"),
            data.get("auto_proxy"),
            data.get("auto_proxy_pool_id"),
            data.get("account_payment_invoice"),
            data.get("api_credential_pool_id"),
        )

        # The account is genuinely created at this point — nothing past
        # here should be able to turn this into a shown error (the exact
        # bug already fixed once inside _persist_new_account itself, which
        # recurred one call later in this same function: a transient
        # failure fetching the list for the confirmation screen used to
        # fall into the except block below meant for sign_in failures).
        try:
            accounts = await db.get_user_accounts(user.id)
        except Exception as e:
            logger.warning(f"Failed to fetch account list after creating account {account_id}: {e}")
            accounts = []
        # The Telegram session and account row are durable now.  Do not let a
        # transient Bot API failure while rendering this confirmation enter the
        # outer ``except`` below: that branch says the login failed even though
        # the account has already been added.
        try:
            await safe_answer(
                message,
                pe(f"✅ Аккаунт {html.escape(data['phone'])} успешно добавлен!"),
                parse_mode="HTML",
                reply_markup=accounts_keyboard(accounts),
            )
        except Exception:
            logger.exception("Account %s was created but its confirmation could not be sent", account_id)
        try:
            await state.clear()
        except Exception:
            logger.exception("Account %s was created but add-account FSM cleanup failed", account_id)

    except PasswordHashInvalidError as e:
        # Keep the client connected and stay in waiting_password — the
        # message tells the user to try again, so there must actually be
        # something left to retry into instead of dumping them back to the
        # accounts list and burning the login session.
        await state.update_data(confirming=False)
        await message.answer(pe(friendly_error(e)), parse_mode="HTML", reply_markup=cancel_keyboard())

    except Exception as e:
        logger.warning(f"Unexpected error confirming 2FA password for user {message.from_user.id}: {e}", exc_info=True)
        await client.disconnect()
        user = await db.get_user(message.from_user.id)
        accounts = await db.get_user_accounts(user.id)
        await message.answer(
            pe(friendly_error(e)),
            parse_mode="HTML",
            reply_markup=accounts_keyboard(accounts),
        )
        await state.clear()


# === Code input keyboard handlers ===

def _format_code_display(code: str, length: int = 5) -> str:
    """Format code with filled and empty circles."""
    filled = len(code)
    display = "".join(["⚫" for _ in range(filled)])
    display += "".join(["▫️" for _ in range(length - filled)])
    return display


@router.callback_query(F.data.startswith("code_digit:"))
async def callback_code_digit(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    valid_states = [AddAccountStates.waiting_code.state]
    if current_state not in valid_states:
        await callback.answer("Сессия ввода кода истекла", show_alert=True)
        return

    digit = callback.data.split(":")[1]
    data = await state.get_data()
    current_code = data.get("entered_code", "")

    if len(current_code) >= 5:
        await callback.answer("Код уже введён полностью")
        return

    new_code = current_code + digit
    await state.update_data(entered_code=new_code)

    display = _format_code_display(new_code)
    await safe_edit(
        callback.message,
        pe(f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}"),
        parse_mode="HTML",
        reply_markup=code_input_keyboard(),
        retries=0,
    )
    await callback.answer()


@router.callback_query(F.data == "code_backspace")
async def callback_code_backspace(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    valid_states = [AddAccountStates.waiting_code.state]
    if current_state not in valid_states:
        await callback.answer("Сессия ввода кода истекла", show_alert=True)
        return

    data = await state.get_data()
    current_code = data.get("entered_code", "")

    if not current_code:
        await callback.answer("Код пуст")
        return

    new_code = current_code[:-1]
    await state.update_data(entered_code=new_code)

    display = _format_code_display(new_code)
    await safe_edit(
        callback.message,
        pe(f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}"),
        parse_mode="HTML",
        reply_markup=code_input_keyboard(),
        retries=0,
    )
    await callback.answer()


@router.callback_query(F.data == "code_clear")
async def callback_code_clear(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    valid_states = [AddAccountStates.waiting_code.state]
    if current_state not in valid_states:
        await callback.answer("Сессия ввода кода истекла", show_alert=True)
        return

    await state.update_data(entered_code="")

    display = _format_code_display("")
    await safe_edit(
        callback.message,
        pe(f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}"),
        parse_mode="HTML",
        reply_markup=code_input_keyboard(),
        retries=0,
    )
    await callback.answer("Код очищен")


async def _edit_code_message(event, state: FSMContext, text: str, **kwargs):
    if isinstance(event, CallbackQuery):
        return await event.message.edit_text(text, **kwargs)
    data = await state.get_data()
    message_id = data.get("code_message_id")
    if message_id:
        return await event.bot.edit_message_text(
            chat_id=event.chat.id, message_id=message_id, text=text, **kwargs
        )


@router.callback_query(F.data == "code_confirm")
async def callback_code_confirm(callback: CallbackQuery, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    await _confirm_code(callback, state, db, userbot_manager)


async def _confirm_code(event, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    user_id = event.from_user.id
    current_state = await state.get_state()
    valid_states = [AddAccountStates.waiting_code.state]
    if current_state not in valid_states:
        if isinstance(event, CallbackQuery):
            await event.answer("Сессия ввода кода истекла", show_alert=True)
        return

    data = await state.get_data()
    if not data.get("account_setup_allowed"):
        await state.clear()
        return
    code = data.get("entered_code", "")
    client = data.get("client")

    if not code:
        if isinstance(event, CallbackQuery):
            await event.answer("Введите код!", show_alert=True)
        return

    if len(code) < 5:
        if isinstance(event, CallbackQuery):
            await event.answer("Код должен содержать 5 цифр!", show_alert=True)
        return

    if not client:
        await _edit_code_message(event, state,
            pe("❌ Сессия истекла. Начните заново."),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    if data.get("confirming"):
        # A previous tap is still being processed against the same Telethon client —
        # ignore the duplicate instead of racing a second sign_in on it.
        if isinstance(event, CallbackQuery):
            await event.answer("Код уже проверяется...")
        return
    await state.update_data(confirming=True)

    await _edit_code_message(event, state, pe("⏳ Проверяем код..."), parse_mode="HTML")

    try:
        await asyncio.wait_for(client.sign_in(data["phone"], code), timeout=30)

        session_string = client.session.save()
        await client.disconnect()

        user, account_id = await _persist_new_account(
            db,
            userbot_manager,
            user_id,
            data["phone"],
            data["api_id"],
            data["api_hash"],
            session_string,
            data.get("proxy"),
            data.get("auto_proxy"),
            data.get("auto_proxy_pool_id"),
            data.get("account_payment_invoice"),
            data.get("api_credential_pool_id"),
        )

        # Same reasoning as process_password above — the account already
        # exists at this point, so a failure fetching the list must not
        # fall into the except block below and get reported as an error.
        try:
            accounts = await db.get_user_accounts(user.id)
        except Exception as e:
            logger.warning(f"Failed to fetch account list after creating account {account_id}: {e}")
            accounts = []
        # Sign-in and the DB write have succeeded.  Rendering a callback's
        # message can still fail on a slow server (in particular once the
        # callback is old); never route that through the sign-in error handler
        # and tell the user the opposite of what actually happened.
        success_text = pe(f"✅ Аккаунт {html.escape(data['phone'])} успешно добавлен!")
        success_markup = accounts_keyboard(accounts)
        try:
            await _edit_code_message(
                event,
                state,
                success_text,
                parse_mode="HTML",
                reply_markup=success_markup,
            )
        except Exception:
            logger.exception("Account %s was created but its confirmation edit failed", account_id)
            try:
                await safe_answer(
                    event.message if isinstance(event, CallbackQuery) else event,
                    success_text,
                    parse_mode="HTML",
                    reply_markup=success_markup,
                )
            except Exception:
                logger.exception("Fallback confirmation for created account %s could not be sent", account_id)
        try:
            await state.clear()
        except Exception:
            logger.exception("Account %s was created but add-account FSM cleanup failed", account_id)

    except Exception as e:
        error_str = str(e).lower()
        if "two-step" in error_str or "password" in error_str:
            await state.update_data(confirming=False)
            await state.set_state(AddAccountStates.waiting_password)
            await _edit_code_message(event, state,
                pe("🔐 Требуется пароль двухфакторной аутентификации.\n\nВведите пароль:"),
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        elif isinstance(e, PhoneCodeInvalidError):
            # Same reasoning as the password branch — keep the client and
            # the waiting_code state alive so "try again" is actually
            # possible instead of forcing a full restart (new SMS/code).
            await state.update_data(confirming=False, entered_code="")
            await _edit_code_message(event, state,
                pe(f"{friendly_error(e)}\n\n"
                "🔢 Введите код с помощью кнопок:\n\n"
                f"Код: {_format_code_display('')}"),
                parse_mode="HTML",
                reply_markup=code_input_keyboard(),
            )
        else:
            logger.warning(f"Unexpected error confirming code for user {user_id}: {e}", exc_info=True)
            await client.disconnect()
            user = await db.get_user(user_id)
            accounts = await db.get_user_accounts(user.id)
            await _edit_code_message(event, state,
                pe(friendly_error(e)),
                parse_mode="HTML",
                reply_markup=accounts_keyboard(accounts),
            )
            await state.clear()

    if isinstance(event, CallbackQuery):
        # A long Telethon login may leave the callback older than Telegram's
        # answer window.  The account confirmation above is the authoritative
        # result, so an expired acknowledgement must not raise a generic error.
        try:
            await event.answer()
        except Exception:
            logger.debug("Could not acknowledge account-code callback", exc_info=True)

@router.callback_query(F.data.startswith("delete_account:"))
async def callback_delete_account(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account_for_user(account_id, callback.from_user.id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    text = pe(f"❓ Вы уверены, что хотите удалить аккаунт {html.escape(account.phone)}?\n\n⚠️ Все рассылки этого аккаунта будут остановлены.")

    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=delete_account_confirm_keyboard(account_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_account:"))
async def callback_confirm_delete_account(
    callback: CallbackQuery,
    db: Database,
    userbot_manager: UserbotManager,
    mailing_service: MailingService,
):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account_for_user(account_id, callback.from_user.id)
    user = await db.get_user(callback.from_user.id)
    if not account or account.user_id != user.id:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    mailings = await db.get_user_mailings(user.id)
    for mailing in mailings:
        if mailing.account_id == account_id and mailing.is_active:
            await mailing_service.stop_mailing(mailing.id)

    await userbot_manager.logout_and_stop(account)
    # logout_and_stop already tore down the live Telegram session — retry the
    # DB write a few times instead of leaving a stranded account row (still
    # is_active=1, session dead) behind a transient DB error.
    deleted = False
    for attempt in range(3):
        try:
            await db.delete_account(account_id)
            deleted = True
            break
        except Exception as e:
            if attempt < 2:
                logger.warning(f"delete_account({account_id}) failed, retrying: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
            else:
                logger.error(f"delete_account({account_id}) failed after retries: {e}", exc_info=True)

    if not deleted:
        # The Telegram session has already been logged out above, but the
        # account row is still active when every DB retry failed.  Never
        # claim that the account was deleted in this state: leave the
        # confirmation screen in place so the user can retry the DB step.
        await callback.answer(
            "⚠️ Сессия отключена, но запись аккаунта не удалилась. Повторите удаление.",
            show_alert=True,
        )
        return

    try:
        accounts = await db.get_user_accounts(user.id)
    except Exception:
        logger.error("Account %s was deleted but account-list refresh failed", account_id, exc_info=True)
        try:
            await callback.answer("✅ Аккаунт удалён. Список обновится при следующем открытии.", show_alert=True)
        except Exception:
            pass
        return
    # The account is already gone at this point regardless of what happens
    # next — use safe_edit so a transient network blip rendering this
    # confirmation doesn't surface as a scary "Произошла ошибка" for an
    # action that actually already succeeded.
    await safe_edit(
        callback.message,
        pe("✅ Аккаунт удалён"),
        parse_mode="HTML",
        reply_markup=accounts_keyboard(accounts),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rename_account:"))
async def callback_rename_account(callback: CallbackQuery, state: FSMContext, db: Database):
    account_id = int(callback.data.split(":")[1])
    if not await db.get_account_for_user(account_id, callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await state.update_data(account_id=account_id)
    await state.set_state(RenameAccountStates.waiting_name)
    await callback.message.edit_text(
        pe("✏️ Введите новое название для аккаунта:\n\n"
        "(Например: Основной, Рабочий, Спам и т.д.)"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(RenameAccountStates.waiting_name)
async def process_rename_account(message: Message, state: FSMContext, db: Database):
    if not message.text:
        await message.answer(pe("❌ Введите название текстом."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    name = message.text.strip()
    if not name:
        await message.answer(pe("❌ Название не может быть пустым."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id:
        await message.answer(pe("❌ Сессия устарела. Начните заново."), parse_mode="HTML")
        await state.clear()
        return
    if not await db.get_account_for_user(account_id, message.from_user.id):
        await state.clear()
        await message.answer("⛔ Нет доступа", parse_mode="HTML")
        return
    await db.update_account_name(account_id, name)
    await state.clear()

    account = await db.get_account_for_user(account_id, message.from_user.id)
    await message.answer(
        pe(f"✅ Аккаунт переименован: <b>{html.escape(name)}</b>"),
        parse_mode="HTML",
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
    )


@router.callback_query(F.data == "pay_account_card")
async def callback_pay_account_card(callback: CallbackQuery):
    await callback.message.edit_text(
        pe("💳 Оплата банковской картой\n\n"
        "Для оплаты аккаунта банковской картой (Visa/MasterCard) "
        "напишите нашему менеджеру:\n\n"
        "👤 Менеджер: @autosenderkarta\n\n"
        "📌 Как это работает:\n"
        "1. Напишите менеджеру, что хотите оплатить аккаунт\n"
        "2. Менеджер отправит вам реквизиты для перевода\n"
        "3. После оплаты отправьте скриншот чека менеджеру\n"
        "4. Аккаунт будет добавлен в течение нескольких минут\n\n"
        "⏰ Время работы менеджера: ежедневно с 9:00 до 23:00"),
        parse_mode="HTML",
        reply_markup=account_payment_method_keyboard(),
    )
    await callback.answer()


# === Proxy settings ===

@router.callback_query(F.data.startswith("set_proxy:"))
async def callback_set_proxy(callback: CallbackQuery, state: FSMContext, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account_for_user(account_id, callback.from_user.id)
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await state.update_data(account_id=account_id)
    await state.set_state(SetProxyStates.waiting_proxy)

    current = "не настроен" if (not account.proxy or account.proxy_pool_id) else f"<code>{html.escape(account.proxy)}</code>"
    await callback.message.edit_text(
        pe(f"🌐 <b>Настройка прокси SOCKS5</b>\n\n"
        f"Текущий прокси: {current}\n\n"
        f"Введите прокси в формате:\n"
        f"<code>socks5://host:port</code>\n"
        f"или с авторизацией:\n"
        f"<code>socks5://user:pass@host:port</code>\n\n"
        f"Чтобы <b>удалить</b> прокси — отправьте: <code>удалить</code>"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(SetProxyStates.waiting_proxy)
async def process_set_proxy(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    text = message.text.strip() if message.text else ""
    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id:
        await message.answer(pe("❌ Сессия устарела. Начните заново."), parse_mode="HTML")
        await state.clear()
        return
    if not await db.get_account_for_user(account_id, message.from_user.id):
        await state.clear()
        await message.answer("⛔ Нет доступа", parse_mode="HTML")
        return

    if text.lower() in ("удалить", "remove", "delete"):
        await db.update_account_proxy(account_id, None)
        await db.clear_proxy_problem_notified(account_id)
        await state.clear()
        account = await db.get_account_for_user(account_id, message.from_user.id)
        # Restart client without proxy
        await userbot_manager.stop_client(account_id)
        if account:
            await userbot_manager.start_client(account)
        await message.answer(
            pe("✅ Прокси удалён. Аккаунт переподключён без прокси."),
            parse_mode="HTML",
            reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
        )
        return

    text = normalize_proxy_string(text)
    if not text.startswith("socks5://"):
        await message.answer(
            pe("❌ Неверный формат. Введите прокси в формате:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>\n\n"
            "Чтобы удалить прокси — отправьте: <code>удалить</code>"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    from urllib.parse import urlparse
    parsed = urlparse(text)
    if not parsed.hostname or not parsed.port:
        await message.answer(
            pe("❌ Не удалось распознать хост или порт.\n"
            "Проверьте формат: <code>socks5://host:port</code>"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    if not await _test_proxy_connection(parsed.hostname, parsed.port):
        await message.answer(
            pe(f"❌ <b>Прокси не подходит!</b>\n\n"
            f"Не удалось подключиться к <code>{html.escape(parsed.hostname)}:{parsed.port}</code>.\n\n"
            f"Проверьте:\n"
            f"• Правильность адреса и порта\n"
            f"• Логин и пароль (если есть)\n"
            f"• Что прокси рабочий и не заблокирован\n\n"
            f"Введите другой прокси или отправьте <code>удалить</code>:"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    await db.update_account_proxy(account_id, text)
    # A previously-notified proxy problem was about the OLD proxy — if this
    # new one also turns out to be broken, that's a distinct failure the
    # owner should be told about too, not silently suppressed by the old flag.
    await db.clear_proxy_problem_notified(account_id)
    await state.clear()

    account = await db.get_account_for_user(account_id, message.from_user.id)
    # Restart client with new proxy
    await userbot_manager.stop_client(account_id)
    if account:
        await userbot_manager.start_client(account)

    await message.answer(
        pe(f"✅ Прокси сохранён: <code>{html.escape(text)}</code>\nАккаунт переподключён."),
        parse_mode="HTML",
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
    )


@router.callback_query(F.data.startswith("toggle_sponsor_sub:"))
async def callback_toggle_sponsor_sub(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account_for_user(account_id, callback.from_user.id)
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    new_val = not account.auto_subscribe_sponsors
    await db.update_auto_subscribe_sponsors(account_id, new_val)
    status = "включена" if new_val else "выключена"
    await callback.answer(f"Автоподписка на спонсоров {status}")

    account = await db.get_account_for_user(account_id, callback.from_user.id)
    ar_status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    gr_status = "✅ Включён" if account.group_autoresponder_enabled else "❌ Выключен"
    proxy_status = _proxy_status_text(account)
    sponsor_status = "✅ Включена" if account.auto_subscribe_sponsors else "❌ Выключена"

    text = pe(
        f"📱 Аккаунт: {html.escape(account.display_name)}\n"
        f"📞 Номер: {html.escape(account.phone)}\n\n"
        f"🤖 Автоприветствие: {ar_status}\n"
        f"💬 Групповой автоответчик: {gr_status}\n"
        f"🤖 Автоподписка на спонсоров: {sponsor_status}\n"
        f"{proxy_status}\n"
        f"📅 Добавлен: {account.created_at.strftime('%d.%m.%Y') if account.created_at else '—'}\n\n"
        "Выберите действие:"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors),
    )

