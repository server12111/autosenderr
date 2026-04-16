from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..database.db import Database
from ..keyboards.inline import (
    main_menu_keyboard, back_to_menu_keyboard, channel_check_keyboard,
    accounts_keyboard, admin_keyboard, mailings_keyboard,
)

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
async def cmd_start(message: Message, db: Database):
    args = message.text.split(maxsplit=1)
    ref_code = None
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("ref_"):
            ref_code = param[4:]

    user = await db.get_or_create_user(
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
                "📢 Для использования бота необходимо подписаться на каналы:",
                reply_markup=channel_check_keyboard(not_subscribed),
            )
            return

    text = (
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


@router.callback_query(F.data == "check_channels")
async def callback_check_channels(callback: CallbackQuery, db: Database):
    channels = await db.get_required_channels()
    if channels:
        not_subscribed = await check_channels_subscription(callback.bot, callback.from_user.id, channels)
        if not_subscribed:
            await callback.answer("Вы ещё не подписались на все каналы!", show_alert=True)
            await callback.message.edit_text(
                "📢 Подпишитесь на все каналы и нажмите «Проверить»:",
                reply_markup=channel_check_keyboard(not_subscribed),
            )
            return

    await callback.answer("✅ Готово!")
    await callback.message.edit_text(
        "📋 <b>Главное меню</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, db: Database):
    await db.get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(
        "📋 <b>Главное меню</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery, db: Database):
    support = await db.get_setting("card_manager_username") or "autosenderkarta"
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "<b>📋 Рассылки</b> — создание и управление рассылками по чатам\n"
        "<b>👤 Аккаунты</b> — добавление Telegram-аккаунтов для рассылок\n"
        "<b>💳 Подписка</b> — тарифы и способы оплаты\n"
        "<b>🤝 Рефералы</b> — приглашайте друзей и зарабатывайте\n\n"
        "➕ <b>Как добавить аккаунт:</b>\n"
        "1. Перейдите в «Аккаунты» → «Добавить аккаунт»\n"
        "2. При желании укажите прокси SOCKS5\n"
        "3. При желании укажите свой API ID + Hash\n"
        "4. Введите номер телефона\n"
        "5. Введите код из Telegram\n\n"
        "📤 <b>Как создать рассылку:</b>\n"
        "1. Перейдите в «Рассылки» → «Создать»\n"
        "2. Выберите аккаунт и задайте название\n"
        "3. Добавьте сообщения (текст или фото)\n"
        "4. Укажите целевые чаты/группы\n"
        "5. Настройте интервал и расписание\n"
        "6. Запустите рассылку\n\n"
        "🤖 <b>Автоответчик:</b>\n"
        "• <i>Личный</i> — отвечает на входящие ЛС (каждому один раз)\n"
        "• <i>Групповой</i> — отвечает на реплаи в группах (каждый раз)\n\n"
        f"🆘 <b>Поддержка:</b> @{support}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext, db: Database):
    current_state = await state.get_state()
    await state.clear()
    await callback.answer()

    if current_state and any(x in current_state for x in ("AddAccount", "RenameAccount", "SetProxy", "Autoresponder")):
        user = await db.get_user(callback.from_user.id)
        accounts = await db.get_user_accounts(user.id)
        text = "👤 <b>Аккаунты</b>\n\nВыберите аккаунт или добавьте новый:"
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=accounts_keyboard(accounts))
        except Exception:
            await callback.message.delete()
            await callback.message.answer(text, parse_mode="HTML", reply_markup=accounts_keyboard(accounts))
    elif current_state and "Admin" in current_state:
        text = "🔧 Админ-панель\n\nВыберите действие:"
        try:
            await callback.message.edit_text(text, reply_markup=admin_keyboard())
        except Exception:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=admin_keyboard())
    elif current_state and any(x in current_state for x in ("CreateMailing", "EditMailing")):
        mailings = await db.get_user_mailings((await db.get_user(callback.from_user.id)).id)
        text = "📋 <b>Рассылки</b>\n\nВыберите рассылку или создайте новую:"
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mailings_keyboard(mailings))
        except Exception:
            await callback.message.delete()
            await callback.message.answer(text, parse_mode="HTML", reply_markup=mailings_keyboard(mailings))
    else:
        text = "📋 <b>Главное меню</b>\n\nВыберите раздел:"
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
        except Exception:
            await callback.message.delete()
            await callback.message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
