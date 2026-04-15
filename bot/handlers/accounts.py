import asyncio
import time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    accounts_keyboard,
    account_menu_keyboard,
    delete_account_confirm_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    add_account_proxy_keyboard,
    add_account_api_keyboard,
    account_payment_keyboard,
    account_payment_method_keyboard,
    ton_account_payment_keyboard,
    code_input_keyboard,
    back_to_menu_keyboard,
)
from ..userbot.manager import UserbotManager, _parse_proxy, _DEVICE_POOL
from ..config import config
from ..services import CryptoBotService, TonPaymentService

router = Router()


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


@router.callback_query(F.data == "accounts")
async def callback_accounts(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)

    text = "👤 Ваши аккаунты:\n\n"
    if accounts:
        for acc in accounts:
            status = "🟢" if acc.is_active else "🔴"
            ar = "✅" if acc.autoresponder_enabled else "❌"
            gr = "✅" if acc.group_autoresponder_enabled else "❌"
            text += f"{status} {acc.display_name}\n  └ Личный автоответ: {ar}  Групповой: {gr}\n"
    else:
        text += "У вас пока нет добавленных аккаунтов.\n"

    text += "\nВыберите аккаунт или добавьте новый:"

    await callback.message.edit_text(text, reply_markup=accounts_keyboard(accounts))
    await callback.answer()


@router.callback_query(F.data.startswith("account:"))
async def callback_account_menu(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    ar_status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    gr_status = "✅ Включён" if account.group_autoresponder_enabled else "❌ Выключен"
    proxy_status = f"🌐 {account.proxy}" if account.proxy else "🌐 Прокси: не настроен"

    text = (
        f"📱 Аккаунт: {account.display_name}\n"
        f"📞 Номер: {account.phone}\n\n"
        f"🤖 Личный автоответчик: {ar_status}\n"
        f"💬 Групповой автоответчик: {gr_status}\n"
        f"{proxy_status}\n"
        f"📅 Добавлен: {account.created_at.strftime('%d.%m.%Y')}\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(
        text, reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors)
    )
    await callback.answer()


@router.callback_query(F.data == "add_account")
async def callback_add_account(callback: CallbackQuery, state: FSMContext, db: Database):
    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)

    if accounts_count >= config.FREE_ACCOUNTS_LIMIT:
        if config.TON_WALLET_ADDRESS:
            ton_service_instance = TonPaymentService(config.TON_WALLET_ADDRESS, config.TONCENTER_API_KEY)
            ton_amount = await ton_service_instance.calculate_ton_amount(config.EXTRA_ACCOUNT_PRICE)
            if ton_amount:
                ton_text = f"💠 TON — ~{ton_amount} TON (≈ {config.EXTRA_ACCOUNT_PRICE} USDT)"
            else:
                ton_text = f"💠 TON — ≈ {config.EXTRA_ACCOUNT_PRICE} USDT в TON"
            text = (
                f"➕ Добавление аккаунта\n\n"
                f"⚠️ Вы достигли лимита в {config.FREE_ACCOUNTS_LIMIT} бесплатных аккаунтов.\n\n"
                f"Выберите способ оплаты:\n\n"
                f"💎 CryptoBot — {config.EXTRA_ACCOUNT_PRICE} {config.SUBSCRIPTION_CURRENCY}\n"
                f"{ton_text}"
            )
            await callback.message.edit_text(
                text, reply_markup=account_payment_method_keyboard()
            )
        else:
            await _create_cryptobot_account_payment(callback, db, accounts_count)
        await callback.answer()
        return

    remaining = config.FREE_ACCOUNTS_LIMIT - accounts_count
    text = (
        "➕ Добавление аккаунта\n\n"
        f"📊 У вас {accounts_count}/{config.FREE_ACCOUNTS_LIMIT} бесплатных аккаунтов\n"
        f"Осталось бесплатных: {remaining}\n\n"
        "<b>Шаг 1 из 3</b>\n\n"
        "Хотите использовать прокси SOCKS5?\n\n"
        "Если да — введите в формате:\n"
        "<code>socks5://host:port</code>\n"
        "или <code>socks5://user:pass@host:port</code>"
    )

    await state.clear()
    await callback.message.edit_text(text, reply_markup=add_account_proxy_keyboard())
    await callback.answer()


async def _create_cryptobot_account_payment(callback: CallbackQuery, db: Database, accounts_count: int):
    """Create CryptoBot invoice for extra account."""
    extra_cost = config.EXTRA_ACCOUNT_PRICE
    crypto_service = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)
    invoice = await crypto_service.create_invoice(
        amount=extra_cost,
        currency=config.SUBSCRIPTION_CURRENCY,
        description=f"Дополнительный аккаунт (#{accounts_count + 1})",
    )

    if not invoice:
        error_msg = crypto_service.last_error.message if crypto_service.last_error else "Неизвестная ошибка"
        await callback.message.edit_text(
            f"❌ Не удалось создать счёт: {error_msg}",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = (
        f"➕ Добавление аккаунта\n\n"
        f"⚠️ Вы достигли лимита в {config.FREE_ACCOUNTS_LIMIT} бесплатных аккаунтов.\n\n"
        f"💰 Стоимость дополнительного аккаунта: <b>{extra_cost} USDT</b>\n\n"
        "Оплатите счёт и нажмите «Проверить оплату»."
    )
    await callback.message.edit_text(
        text,
        reply_markup=account_payment_keyboard(invoice.pay_url, invoice.invoice_id),
    )


@router.callback_query(F.data == "pay_account_cryptobot")
async def callback_pay_account_cryptobot(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)
    await _create_cryptobot_account_payment(callback, db, accounts_count)
    await callback.answer()


@router.callback_query(F.data == "pay_account_ton")
async def callback_pay_account_ton(
    callback: CallbackQuery, db: Database, ton_service: TonPaymentService
):
    user = await db.get_user(callback.from_user.id)
    comment = f"acc_{user.telegram_id}_{int(time.time())}"

    await callback.message.edit_text("⏳ Получаем курс TON...")

    amount = await ton_service.calculate_ton_amount(config.EXTRA_ACCOUNT_PRICE)
    if not amount:
        await callback.message.edit_text(
            "❌ Не удалось получить курс TON. Попробуйте позже.",
            reply_markup=account_payment_method_keyboard(),
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
        f"💠 Оплата дополнительного аккаунта через TON\n\n"
        f"Сумма: <b>{amount} TON</b> (≈ {config.EXTRA_ACCOUNT_PRICE} USDT)\n\n"
        f"Кошелёк: <code>{config.TON_WALLET_ADDRESS}</code>\n"
        f"Комментарий: <code>{comment}</code>\n\n"
        f"Нажмите кнопку ниже для оплаты через Tonkeeper.\n"
        f"<b>Важно:</b> комментарий должен совпадать точно!\n\n"
        f"После оплаты нажмите «Проверить оплату»."
    )

    await callback.message.edit_text(
        text, reply_markup=ton_account_payment_keyboard(pay_url, comment)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_ton_account:"))
async def callback_check_ton_account(
    callback: CallbackQuery, state: FSMContext, db: Database, ton_service: TonPaymentService
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

    if not is_paid:
        await callback.answer("❌ Оплата ещё не получена. Попробуйте позже.", show_alert=True)
        return

    await db.update_payment_status(comment, "paid")

    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)

    text = (
        "✅ Оплата получена!\n\n"
        "➕ Добавление аккаунта\n\n"
        f"📊 У вас {accounts_count} аккаунтов\n\n"
        "<b>Шаг 1 из 3</b>\n\n"
        "Хотите использовать прокси SOCKS5?\n\n"
        "Если да — введите в формате:\n"
        "<code>socks5://host:port</code>\n"
        "или <code>socks5://user:pass@host:port</code>"
    )

    await state.clear()
    await callback.message.edit_text(text, reply_markup=add_account_proxy_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("check_account_payment:"))
async def callback_check_account_payment(callback: CallbackQuery, state: FSMContext, db: Database):
    invoice_id = callback.data.split(":")[1]
    crypto_service = CryptoBotService(config.CRYPTOBOT_TOKEN, config.CRYPTOBOT_TESTNET)
    paid = await crypto_service.check_invoice_paid(invoice_id)

    if not paid:
        await callback.answer("❌ Оплата ещё не получена. Попробуйте позже.", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    accounts_count = await db.count_user_accounts(user.id)
    remaining = config.FREE_ACCOUNTS_LIMIT - accounts_count
    if remaining < 0:
        remaining = 0

    text = (
        "✅ Оплата получена!\n\n"
        "➕ Добавление аккаунта\n\n"
        f"📊 У вас {accounts_count} аккаунтов\n\n"
        "<b>Шаг 1 из 3</b>\n\n"
        "Хотите использовать прокси SOCKS5?\n\n"
        "Если да — введите в формате:\n"
        "<code>socks5://host:port</code>\n"
        "или <code>socks5://user:pass@host:port</code>"
    )

    await state.clear()
    await callback.message.edit_text(text, reply_markup=add_account_proxy_keyboard())
    await callback.answer()


@router.callback_query(F.data == "add_account_set_proxy")
async def callback_add_account_set_proxy(callback: CallbackQuery, state: FSMContext):
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
    await state.update_data(proxy=None)
    await _ask_api_step(callback.message, can_edit=True)
    await callback.answer()


@router.message(AddAccountStates.waiting_proxy)
async def process_proxy(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""

    if not text.startswith("socks5://"):
        await message.answer(
            "❌ Неверный формат. Введите прокси:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>",
            reply_markup=cancel_keyboard(),
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

    await state.update_data(proxy=text)
    await state.set_state(None)
    await _ask_api_step(message, can_edit=False)


async def _ask_api_step(target, can_edit: bool = False):
    text = (
        "➕ Добавление аккаунта\n\n"
        "<b>Шаг 2 из 3</b>\n\n"
        "Хотите использовать собственный API ID и Hash?\n\n"
        "Получить: https://my.telegram.org\n\n"
        "Если нет — используются стандартные настройки."
    )
    if can_edit:
        await target.edit_text(text, reply_markup=add_account_api_keyboard())
    else:
        await target.answer(text, reply_markup=add_account_api_keyboard())


@router.callback_query(F.data == "add_account_set_api")
async def callback_add_account_set_api(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🔑 Введите API ID (число):\n\n"
        "Получить: https://my.telegram.org",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_api_id)
    await callback.answer()


@router.callback_query(F.data == "add_account_skip_api")
async def callback_add_account_skip_api(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        api_id=config.DEFAULT_API_ID,
        api_hash=config.DEFAULT_API_HASH,
    )
    await callback.message.edit_text(
        "➕ Добавление аккаунта\n\n"
        "<b>Шаг 3 из 3</b>\n\n"
        "Введите номер телефона в международном формате:\n"
        "Например: <code>+380991234567</code>",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_phone)
    await callback.answer()


@router.message(AddAccountStates.waiting_api_id)
async def process_api_id(message: Message, state: FSMContext):
    try:
        api_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ API ID должен быть числом. Попробуйте снова:")
        return

    await state.update_data(api_id=api_id)
    await message.answer("Введите API Hash:", reply_markup=cancel_keyboard())
    await state.set_state(AddAccountStates.waiting_api_hash)


@router.message(AddAccountStates.waiting_api_hash)
async def process_api_hash(message: Message, state: FSMContext):
    api_hash = message.text.strip()

    if len(api_hash) < 20:
        await message.answer("❌ API Hash слишком короткий. Попробуйте снова:")
        return

    await state.update_data(api_hash=api_hash)
    await message.answer(
        "➕ Добавление аккаунта\n\n"
        "<b>Шаг 3 из 3</b>\n\n"
        "Введите номер телефона в международном формате:\n"
        "Например: <code>+380991234567</code>",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(AddAccountStates.waiting_phone)


@router.message(AddAccountStates.waiting_phone)
async def process_phone(
    message: Message, state: FSMContext, userbot_manager: UserbotManager
):
    phone = message.text.strip()

    if not phone.startswith("+"):
        await message.answer("❌ Номер должен начинаться с +. Попробуйте снова:")
        return

    await state.update_data(phone=phone)
    data = await state.get_data()

    await message.answer("⏳ Отправляем код на телефон...")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    device = _DEVICE_POOL[abs(hash(phone)) % len(_DEVICE_POOL)]
    client = TelegramClient(
        StringSession(), data["api_id"], data["api_hash"],
        proxy=_parse_proxy(data.get("proxy")),
        device_model=device["device_model"],
        system_version=device["system_version"],
        app_version=device["app_version"],
        lang_code="uk",
        system_lang_code="uk-UA",
        connection_retries=1,
        retry_delay=1,
        timeout=15,
    )

    try:
        await asyncio.wait_for(client.connect(), timeout=20)

        sent = await asyncio.wait_for(client.send_code_request(phone), timeout=30)

        await state.update_data(client=client, entered_code="", phone_code_hash=sent.phone_code_hash)

        await message.answer(
            "📱 Код отправлен!\n\n"
            "📲 Проверьте приложение Telegram на других ваших устройствах или Telegram Web — "
            "код придёт туда.\n\n"
            "🔢 Введите код с помощью кнопок:\n\n"
            "Код: ▫️▫️▫️▫️▫️",
            reply_markup=code_input_keyboard(),
        )
        await state.set_state(AddAccountStates.waiting_code)

    except asyncio.TimeoutError:
        await client.disconnect()
        await message.answer(
            "❌ Превышено время ожидания (30 сек).\n\n"
            "Telegram не отвечает. Проверьте интернет-соединение или прокси и попробуйте снова.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
    except Exception as e:
        await client.disconnect()
        err = str(e)
        if "Connection to Telegram failed" in err or "ConnectionError" in type(e).__name__:
            msg = (
                "❌ Не удалось подключиться к Telegram.\n\n"
                "Возможные причины:\n"
                "• Нет интернета на сервере\n"
                "• Telegram заблокирован — попробуйте добавить прокси\n\n"
                "Попробуйте снова позже."
            )
        elif "phone" in err.lower() or "invalid" in err.lower():
            msg = f"❌ Неверный номер телефона: {e}\n\nПроверьте формат: +380991234567"
        else:
            msg = f"❌ Ошибка при отправке кода: {e}\n\nПопробуйте снова."
        await message.answer(msg, reply_markup=main_menu_keyboard())
        await state.clear()


@router.message(AddAccountStates.waiting_code)
async def process_code(message: Message, state: FSMContext, db: Database):
    data = await state.get_data()
    current_code = data.get("entered_code", "")
    display = _format_code_display(current_code)

    await message.answer(
        "⚠️ Используйте кнопки для ввода кода!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}",
        reply_markup=code_input_keyboard(),
    )


@router.message(AddAccountStates.waiting_password)
async def process_password(message: Message, state: FSMContext, db: Database):
    password = message.text.strip()
    data = await state.get_data()
    client = data.get("client")

    if not client:
        await message.answer(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    try:
        await client.sign_in(password=password)

        session_string = client.session.save()
        await client.disconnect()

        user = await db.get_user(message.from_user.id)
        account_id = await db.create_account(
            user_id=user.id,
            phone=data["phone"],
            api_id=data["api_id"],
            api_hash=data["api_hash"],
            session_string=session_string,
        )
        proxy_str = data.get("proxy")
        if proxy_str and account_id:
            await db.update_account_proxy(account_id, proxy_str)

        await message.answer(
            f"✅ Аккаунт {data['phone']} успешно добавлен!",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()

    except Exception as e:
        await client.disconnect()
        await message.answer(
            f"❌ Ошибка авторизации: {e}",
            reply_markup=main_menu_keyboard(),
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
    await callback.message.edit_text(
        f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}",
        reply_markup=code_input_keyboard(),
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
    await callback.message.edit_text(
        f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}",
        reply_markup=code_input_keyboard(),
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
    await callback.message.edit_text(
        f"📱 Код отправлен!\n\n"
        f"🔢 Введите код с помощью кнопок:\n\n"
        f"Код: {display}",
        reply_markup=code_input_keyboard(),
    )
    await callback.answer("Код очищен")


@router.callback_query(F.data == "code_confirm")
async def callback_code_confirm(callback: CallbackQuery, state: FSMContext, db: Database):
    current_state = await state.get_state()
    valid_states = [AddAccountStates.waiting_code.state]
    if current_state not in valid_states:
        await callback.answer("Сессия ввода кода истекла", show_alert=True)
        return

    data = await state.get_data()
    code = data.get("entered_code", "")
    client = data.get("client")

    if not code:
        await callback.answer("Введите код!", show_alert=True)
        return

    if len(code) < 5:
        await callback.answer("Код должен содержать 5 цифр!", show_alert=True)
        return

    if not client:
        await callback.message.edit_text(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        await callback.answer()
        return

    await callback.message.edit_text("⏳ Проверяем код...")

    try:
        await client.sign_in(data["phone"], code)

        session_string = client.session.save()
        await client.disconnect()

        user = await db.get_user(callback.from_user.id)
        account_id = await db.create_account(
            user_id=user.id,
            phone=data["phone"],
            api_id=data["api_id"],
            api_hash=data["api_hash"],
            session_string=session_string,
        )
        proxy_str = data.get("proxy")
        if proxy_str and account_id:
            await db.update_account_proxy(account_id, proxy_str)

        await callback.message.edit_text(
            f"✅ Аккаунт {data['phone']} успешно добавлен!",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()

    except Exception as e:
        error_str = str(e).lower()
        if "two-step" in error_str or "password" in error_str:
            await state.set_state(AddAccountStates.waiting_password)
            await callback.message.edit_text(
                "🔐 Требуется пароль двухфакторной аутентификации.\n\n"
                "Введите пароль:",
                reply_markup=cancel_keyboard(),
            )
        else:
            await client.disconnect()
            await callback.message.edit_text(
                f"❌ Ошибка авторизации: {e}",
                reply_markup=main_menu_keyboard(),
            )
            await state.clear()

    await callback.answer()


@router.callback_query(F.data.startswith("delete_account:"))
async def callback_delete_account(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    text = f"❓ Вы уверены, что хотите удалить аккаунт {account.phone}?\n\n⚠️ Все рассылки этого аккаунта будут остановлены."

    await callback.message.edit_text(
        text, reply_markup=delete_account_confirm_keyboard(account_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_account:"))
async def callback_confirm_delete_account(
    callback: CallbackQuery, db: Database, userbot_manager: UserbotManager
):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if account:
        await userbot_manager.logout_and_stop(account)

    await db.delete_account(account_id)

    await callback.message.edit_text(
        "✅ Аккаунт удалён",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rename_account:"))
async def callback_rename_account(callback: CallbackQuery, state: FSMContext):
    account_id = int(callback.data.split(":")[1])
    await state.update_data(account_id=account_id)
    await state.set_state(RenameAccountStates.waiting_name)
    await callback.message.edit_text(
        "✏️ Введите новое название для аккаунта:\n\n"
        "(Например: Основной, Рабочий, Спам и т.д.)",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(RenameAccountStates.waiting_name)
async def process_rename_account(message: Message, state: FSMContext, db: Database):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Название не может быть пустым.", reply_markup=cancel_keyboard())
        return
    data = await state.get_data()
    account_id = data["account_id"]
    await db.update_account_name(account_id, name)
    await state.clear()

    account = await db.get_account(account_id)
    await message.answer(
        f"✅ Аккаунт переименован: <b>{name}</b>",
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
    )


@router.callback_query(F.data == "pay_account_card")
async def callback_pay_account_card(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 Оплата банковской картой\n\n"
        "Для оплаты аккаунта банковской картой (Visa/MasterCard) "
        "напишите нашему менеджеру:\n\n"
        "👤 Менеджер: @autosenderkarta\n\n"
        "📌 Как это работает:\n"
        "1. Напишите менеджеру, что хотите оплатить аккаунт\n"
        "2. Менеджер отправит вам реквизиты для перевода\n"
        "3. После оплаты отправьте скриншот чека менеджеру\n"
        "4. Аккаунт будет добавлен в течение нескольких минут\n\n"
        "⏰ Время работы менеджера: ежедневно с 9:00 до 23:00",
        reply_markup=account_payment_method_keyboard(),
    )
    await callback.answer()


# === Proxy settings ===

@router.callback_query(F.data.startswith("set_proxy:"))
async def callback_set_proxy(callback: CallbackQuery, state: FSMContext, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    await state.update_data(account_id=account_id)
    await state.set_state(SetProxyStates.waiting_proxy)

    current = f"<code>{account.proxy}</code>" if account.proxy else "не настроен"
    await callback.message.edit_text(
        f"🌐 <b>Настройка прокси SOCKS5</b>\n\n"
        f"Текущий прокси: {current}\n\n"
        f"Введите прокси в формате:\n"
        f"<code>socks5://host:port</code>\n"
        f"или с авторизацией:\n"
        f"<code>socks5://user:pass@host:port</code>\n\n"
        f"Чтобы <b>удалить</b> прокси — отправьте: <code>удалить</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(SetProxyStates.waiting_proxy)
async def process_set_proxy(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    text = message.text.strip() if message.text else ""
    data = await state.get_data()
    account_id = data["account_id"]

    if text.lower() in ("удалить", "remove", "delete"):
        await db.update_account_proxy(account_id, None)
        await state.clear()
        account = await db.get_account(account_id)
        # Restart client without proxy
        await userbot_manager.stop_client(account_id)
        if account:
            await userbot_manager.start_client(account)
        await message.answer(
            "✅ Прокси удалён. Аккаунт переподключён без прокси.",
            reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
        )
        return

    if not text.startswith("socks5://"):
        await message.answer(
            "❌ Неверный формат. Введите прокси в формате:\n"
            "<code>socks5://host:port</code>\n"
            "или <code>socks5://user:pass@host:port</code>\n\n"
            "Чтобы удалить прокси — отправьте: <code>удалить</code>",
            reply_markup=cancel_keyboard(),
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

    await db.update_account_proxy(account_id, text)
    await state.clear()

    account = await db.get_account(account_id)
    # Restart client with new proxy
    await userbot_manager.stop_client(account_id)
    if account:
        await userbot_manager.start_client(account)

    await message.answer(
        f"✅ Прокси сохранён: <code>{text}</code>\nАккаунт переподключён.",
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False),
    )


# === Auto-subscribe sponsors toggle ===

@router.callback_query(F.data.startswith("toggle_auto_subscribe:"))
async def callback_toggle_auto_subscribe(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)
    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    new_val = not account.auto_subscribe_sponsors
    await db.update_auto_subscribe_sponsors(account_id, new_val)
    status = "включена" if new_val else "выключена"
    await callback.answer(f"Авто-подписка на спонсоров {status}")

    account = await db.get_account(account_id)
    await callback.message.edit_reply_markup(
        reply_markup=account_menu_keyboard(account_id, account.auto_subscribe_sponsors if account else False)
    )
