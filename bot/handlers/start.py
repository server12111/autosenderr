from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..database.db import Database
from ..keyboards.inline import (
    main_menu_keyboard, back_to_menu_keyboard, channel_check_keyboard,
    accounts_keyboard, admin_keyboard, mailings_keyboard, help_keyboard,
    account_menu_keyboard, mailing_menu_keyboard,
    mailing_messages_keyboard, mailing_targets_keyboard,
    mailing_creation_messages_keyboard, mailing_creation_targets_keyboard,
    active_hours_keyboard, reply_mode_select_keyboard, dm_mailing_keyboard,
)
from ..config import config
from ..utils.premium_emoji import pe

router = Router()


async def check_channels_subscription(bot, user_id: int, channels) -> list:
    """Returns list of channels user is NOT subscribed to."""
    not_subscribed = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch.channel_id, user_id)
            if member.status in ("left", "kicked", "restricted"):
                not_subscribed.append(ch)
        except Exception:
            not_subscribed.append(ch)
    return not_subscribed


@router.message(Command("start"))
async def cmd_start(message: Message, db: Database, state: FSMContext):
    current_state = await state.get_state()
    if current_state and "AddAccount" in current_state:
        data = await state.get_data()
        client = data.get("client")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await state.clear()

    args = message.text.split(maxsplit=1)
    ref_code = None
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("ref_"):
            ref_code = param[4:]

    user, is_new = await db.get_or_create_user(
        message.from_user.id, message.from_user.username
    )

    # Set referral if first join and ref_code valid
    if ref_code and not user.referred_by:
        referrer = await db.get_user_by_ref_code(ref_code)
        if referrer and referrer.telegram_id != message.from_user.id:
            await db.set_referred_by(user.id, referrer.id)

    # Check required channel subscriptions
    channels = await db.get_required_channels()
    if channels:
        not_subscribed = await check_channels_subscription(message.bot, message.from_user.id, channels)
        if not_subscribed:
            await message.answer(
                pe("📢 Для использования бота необходимо подписаться на каналы:"),
                parse_mode="HTML",
                reply_markup=channel_check_keyboard(not_subscribed),
            )
            return

    text = pe(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "Добро пожаловать в <b>AutoSender</b> — инструмент для автоматических рассылок через Telegram-аккаунты.\n\n"
        "⚡️ <b>Что умеет бот:</b>\n"
        "• Рассылка сообщений по чатам и группам\n"
        "• Автоответчик на личные сообщения\n"
        "• Автоответчик в группах\n"
        "• Гибкое расписание и интервалы\n"
        "• Управление несколькими аккаунтами\n\n"
        "Выберите раздел ниже 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())

    if is_new:
        promo = await db.get_promocode("free")
        if promo and promo.uses_count < promo.max_uses:
            pin_msg = await message.answer(
                pe(
                    "🎟 <b>Промокод в подарок!</b>\n\n"
                    "🔥 <b>1 день</b> бесплатного доступа — специально для тебя:\n\n"
                    "<code>free</code>\n\n"
                    "⭐ Активируй в разделе <b>«Подписка»</b>"
                ),
                parse_mode="HTML",
            )
            try:
                await message.bot.pin_chat_message(
                    chat_id=message.chat.id,
                    message_id=pin_msg.message_id,
                    disable_notification=True,
                )
                await db.update_user_pin_msg_id(user.id, pin_msg.message_id)
            except Exception:
                pass


@router.callback_query(F.data == "check_channels")
async def callback_check_channels(callback: CallbackQuery, db: Database):
    channels = await db.get_required_channels()
    if channels:
        not_subscribed = await check_channels_subscription(callback.bot, callback.from_user.id, channels)
        if not_subscribed:
            await callback.answer("Вы ещё не подписались на все каналы!", show_alert=True)
            await callback.message.edit_text(
                pe("📢 Подпишитесь на все каналы и нажмите «Проверить»:"),
                parse_mode="HTML",
                reply_markup=channel_check_keyboard(not_subscribed),
            )
            return

    await callback.answer("✅ Готово!")
    await callback.message.edit_text(
        pe("📋 <b>Главное меню</b>\n\nВыберите раздел:"),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, db: Database):
    await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(
        pe("📋 <b>Главное меню</b>\n\nВыберите раздел:"),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery, db: Database):
    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    text = pe(
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>📋 Рассылки</b> — рассылай сообщения по чатам и группам\n"
        "• Текст, фото, пересылка сообщений\n"
        "• Расписание по времени и интервалам\n"
        "• Несколько аккаунтов на одну рассылку\n\n"
        "<b>👤 Аккаунты</b> — добавляй Telegram-аккаунты\n"
        "• Поддержка прокси SOCKS5\n"
        "• Автоответчик в ЛС и группах\n\n"
        "<b>💳 Подписка</b> — CryptoBot, TON, карта, промокоды\n"
        "<b>🤝 Рефералы</b> — приглашай друзей и получай % с оплат\n\n"
        "➕ <b>Как добавить аккаунт:</b>\n"
        "1. «Аккаунты» → «Добавить аккаунт»\n"
        "2. При желании укажи прокси SOCKS5\n"
        "3. Введи номер телефона и код из Telegram\n\n"
        "📤 <b>Как запустить рассылку:</b>\n"
        "1. «Рассылки» → «Создать»\n"
        "2. Выбери аккаунт, добавь сообщения и чаты\n"
        "3. Настрой интервал, расписание → Запуск\n\n"
        f"🆘 <b>Поддержка:</b> @{support}"
    )
    privacy_url = getattr(config, 'PRIVACY_URL', None) or None
    terms_url = getattr(config, 'TERMS_URL', None) or None
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=help_keyboard(support_username=support, privacy_url=privacy_url, terms_url=terms_url)
    )
    await callback.answer()


@router.callback_query(F.data == "dm_mailing_info")
async def callback_dm_mailing_info(callback: CallbackQuery):
    await callback.message.edit_text(
        pe(
            "📲 <b>Рассылка в личные сообщения</b>\n\n"
            "Хотите отправлять сообщения напрямую в ЛС пользователей?\n\n"
            "У нас есть отдельный бот специально для этого — он умеет делать рассылки "
            "прямо в личные сообщения.\n\n"
            "👇 Перейдите и попробуйте: @feAutoSenderDMbot"
        ),
        parse_mode="HTML",
        reply_markup=dm_mailing_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext, db: Database):
    current_state = await state.get_state()
    data = await state.get_data()  # read BEFORE clear so mailing_id/account_id are available
    await state.clear()
    await callback.answer()

    async def _edit(text, markup):
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await callback.message.delete()
            await callback.message.answer(text, parse_mode="HTML", reply_markup=markup)

    if not current_state:
        await _edit(pe("📋 <b>Главное меню</b>\n\nВыберите раздел:"), main_menu_keyboard())

    # ── Accounts ──────────────────────────────────────────────────────────────
    elif "AddAccount" in current_state:
        client = data.get("client")
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        user = await db.get_user(callback.from_user.id)
        accounts = await db.get_user_accounts(user.id)
        await _edit(pe("👤 <b>Аккаунты</b>\n\nВыберите аккаунт или добавьте новый:"), accounts_keyboard(accounts))

    elif any(x in current_state for x in ("RenameAccount", "SetProxy", "Autoresponder")):
        account_id = data.get("account_id")
        account = await db.get_account(account_id) if account_id else None
        if account:
            await _edit(
                pe(f"📱 Аккаунт: {account.display_name}\n\nВыберите действие:"),
                account_menu_keyboard(account_id, account.auto_subscribe_sponsors),
            )
        else:
            user = await db.get_user(callback.from_user.id)
            accounts = await db.get_user_accounts(user.id)
            await _edit(pe("👤 <b>Аккаунты</b>\n\nВыберите аккаунт или добавьте новый:"), accounts_keyboard(accounts))

    # ── Admin ──────────────────────────────────────────────────────────────────
    elif "Admin" in current_state:
        await _edit(pe("🔧 Админ-панель\n\nВыберите действие:"), admin_keyboard())

    # ── EditMailing — повертаємо на 1 крок назад залежно від стану ─────────────
    elif "EditMailing" in current_state:
        mailing_id = data.get("mailing_id")
        mailing = await db.get_mailing(mailing_id) if mailing_id else None
        if mailing:
            if any(x in current_state for x in ("waiting_message_text", "waiting_forward_message")):
                messages = await db.get_mailing_messages(mailing_id)
                await _edit(pe(f"📝 Сообщения рассылки «{mailing.name}»:"), mailing_messages_keyboard(mailing_id, messages))
            elif any(x in current_state for x in ("waiting_target", "waiting_folder", "waiting_txt_file",
                                                   "waiting_thread_id_for_target", "waiting_target_interval",
                                                   "waiting_thread_id")):
                targets = await db.get_mailing_targets(mailing_id)
                await _edit(pe(f"🎯 Целевые чаты рассылки «{mailing.name}»:"), mailing_targets_keyboard(mailing_id, targets))
            elif "waiting_reply_range" in current_state:
                await _edit(pe("↩️ Режим ответной рассылки:"), reply_mode_select_keyboard(mailing_id))
            else:  # waiting_hours або інший
                await _edit(pe(f"📊 Рассылка: {mailing.name}\n\nВыберите действие:"), mailing_menu_keyboard(mailing))
        else:
            mailings = await db.get_user_mailings((await db.get_user(callback.from_user.id)).id)
            await _edit(pe("📋 <b>Рассылки</b>\n\nВыберите рассылку или создайте новую:"), mailings_keyboard(mailings))

    # ── CreateMailing — повертаємо на поточний крок wizard'у ───────────────────
    elif "CreateMailing" in current_state:
        mailing_id = data.get("mailing_id")
        if mailing_id:
            mailing = await db.get_mailing(mailing_id)
            if mailing:
                if any(x in current_state for x in ("waiting_message_text", "waiting_forward_message", "adding_messages")):
                    messages = await db.get_mailing_messages(mailing_id)
                    await _edit(pe(f"📝 Добавьте сообщения для «{mailing.name}»:"), mailing_creation_messages_keyboard(mailing_id, messages))
                elif any(x in current_state for x in ("waiting_target", "waiting_folder", "waiting_txt_file", "adding_targets")):
                    targets = await db.get_mailing_targets(mailing_id)
                    await _edit(pe(f"🎯 Добавьте чаты для «{mailing.name}»:"), mailing_creation_targets_keyboard(mailing_id, targets))
                elif "waiting_hours" in current_state:
                    await _edit(pe(f"⏰ Настройка времени для «{mailing.name}»:"), active_hours_keyboard(mailing_id))
                else:
                    mailings = await db.get_user_mailings((await db.get_user(callback.from_user.id)).id)
                    await _edit(pe("📋 <b>Рассылки</b>\n\nВыберите рассылку или создайте новую:"), mailings_keyboard(mailings))
            else:
                mailings = await db.get_user_mailings((await db.get_user(callback.from_user.id)).id)
                await _edit(pe("📋 <b>Рассылки</b>\n\nВыберите рассылку или создайте новую:"), mailings_keyboard(mailings))
        else:
            mailings = await db.get_user_mailings((await db.get_user(callback.from_user.id)).id)
            await _edit(pe("📋 <b>Рассылки</b>\n\nВыберите рассылку или создайте новую:"), mailings_keyboard(mailings))

    else:
        await _edit(pe("📋 <b>Главное меню</b>\n\nВыберите раздел:"), main_menu_keyboard())
