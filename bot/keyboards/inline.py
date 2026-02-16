from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database.db import Account, Mailing, MailingMessage, MailingTarget, Promocode


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Мои рассылки", callback_data="mailings"))
    builder.row(InlineKeyboardButton(text="👤 Аккаунты", callback_data="accounts"))
    builder.row(InlineKeyboardButton(text="💳 Подписка", callback_data="subscription"))
    builder.row(InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help"))
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


# === Accounts ===
def accounts_keyboard(accounts: list[Account]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.row(
            InlineKeyboardButton(text=f"📱 {acc.phone}", callback_data=f"account:{acc.id}")
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account"))
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def account_menu_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✉️ Рассылки аккаунта", callback_data=f"account_mailings:{account_id}")
    )
    builder.row(
        InlineKeyboardButton(text="🤖 Автоответчик", callback_data=f"autoresponder:{account_id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Удалить аккаунт", callback_data=f"delete_account:{account_id}")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def delete_account_confirm_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_account:{account_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"account:{account_id}"),
    )
    return builder.as_markup()


def account_payment_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=pay_url))
    builder.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_account_payment:{invoice_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def add_account_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📱 По номеру телефона", callback_data="add_account_phone")
    )
    builder.row(
        InlineKeyboardButton(text="🔑 Ввести API ID и Hash", callback_data="add_account_api")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


# === Autoresponder ===
def autoresponder_keyboard(account_id: int, enabled: bool, notify_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_autoresponder:{account_id}")
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"edit_autoresponder_text:{account_id}")
    )
    notify_text = "🔔 Уведомления: ВКЛ" if notify_enabled else "🔕 Уведомления: ВЫКЛ"
    builder.row(
        InlineKeyboardButton(text=notify_text, callback_data=f"toggle_notify:{account_id}")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"account:{account_id}"))
    return builder.as_markup()


# === Mailings ===
def mailings_keyboard(mailings: list[Mailing]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in mailings:
        status = "🟢" if m.is_active else "🔴"
        builder.row(
            InlineKeyboardButton(text=f"{status} {m.name}", callback_data=f"mailing:{m.id}")
        )
    builder.row(InlineKeyboardButton(text="➕ Создать рассылку", callback_data="create_mailing"))
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def mailing_menu_keyboard(mailing: Mailing) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Остановить" if mailing.is_active else "🟢 Запустить"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_mailing:{mailing.id}")
    )
    builder.row(
        InlineKeyboardButton(text="📝 Сообщения", callback_data=f"mailing_messages:{mailing.id}")
    )
    builder.row(
        InlineKeyboardButton(text="🎯 Целевые чаты", callback_data=f"mailing_targets:{mailing.id}")
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Время активности", callback_data=f"mailing_hours:{mailing.id}")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Удалить рассылку", callback_data=f"delete_mailing:{mailing.id}")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="mailings"))
    return builder.as_markup()


def delete_mailing_confirm_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_mailing:{mailing_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"mailing:{mailing_id}"),
    )
    return builder.as_markup()


# === Mailing messages ===
def _msg_button_preview(msg: MailingMessage) -> str:
    photo_count = len(msg.photo_paths)
    if photo_count > 1:
        prefix = f"[{photo_count} Фото] "
    elif photo_count == 1:
        prefix = "[Фото] "
    else:
        prefix = ""
    text = msg.text or ""
    max_len = 25 if photo_count else 30
    preview = text[:max_len] + "..." if len(text) > max_len else text
    return f"{prefix}{preview}" if (prefix or preview) else "[Фото]"


def mailing_messages_keyboard(mailing_id: int, messages: list[MailingMessage]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for msg in messages:
        preview = _msg_button_preview(msg)
        builder.row(
            InlineKeyboardButton(text=f"🗑️ {preview}", callback_data=f"delete_msg:{msg.id}")
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить сообщение", callback_data=f"add_mailing_message:{mailing_id}")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing:{mailing_id}"))
    return builder.as_markup()


def photo_collection_keyboard(mailing_id: int, photo_count: int, is_create: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    prefix = "create_" if is_create else "edit_"
    builder.row(
        InlineKeyboardButton(
            text=f"💾 Сохранить ({photo_count} фото)",
            callback_data=f"{prefix}save_photos:{mailing_id}",
        )
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


# === Mailing targets ===
def mailing_targets_keyboard(mailing_id: int, targets: list[MailingTarget]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in targets:
        builder.row(
            InlineKeyboardButton(text=f"🗑️ {target.chat_identifier}", callback_data=f"delete_target:{target.id}")
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить чат", callback_data=f"add_mailing_target:{mailing_id}"),
        InlineKeyboardButton(text="📁 Добавить папку", callback_data=f"add_folder_target:{mailing_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing:{mailing_id}"))
    return builder.as_markup()


# === Mailing creation ===
def select_account_keyboard(accounts: list[Account]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.row(
            InlineKeyboardButton(text=f"📱 {acc.phone}", callback_data=f"select_account:{acc.id}")
        )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="mailings"))
    return builder.as_markup()


def mailing_creation_messages_keyboard(mailing_id: int, messages: list[MailingMessage]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for msg in messages:
        preview = _msg_button_preview(msg)
        builder.row(
            InlineKeyboardButton(text=f"🗑️ {preview}", callback_data=f"create_delete_msg:{msg.id}")
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить сообщение", callback_data=f"create_add_message:{mailing_id}")
    )
    if messages:
        builder.row(InlineKeyboardButton(text="✅ Готово", callback_data=f"create_messages_done:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def mailing_creation_targets_keyboard(mailing_id: int, targets: list[MailingTarget]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in targets:
        builder.row(
            InlineKeyboardButton(text=f"🗑️ {target.chat_identifier}", callback_data=f"create_delete_target:{target.id}")
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить чат", callback_data=f"create_add_target:{mailing_id}"),
        InlineKeyboardButton(text="📁 Добавить папку", callback_data=f"create_add_folder:{mailing_id}"),
    )
    if targets:
        builder.row(InlineKeyboardButton(text="✅ Готово", callback_data=f"create_targets_done:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def active_hours_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏭️ Пропустить (24/7)", callback_data=f"skip_hours:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="⏰ Настроить", callback_data=f"setup_hours:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def launch_mailing_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data=f"launch_mailing:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


# === Subscription ===
def subscription_keyboard(has_subscription: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promocode"))
    if not has_subscription:
        builder.row(InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription"))
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def payment_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=pay_url))
    builder.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment:{invoice_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def payment_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 CryptoBot (USDT)", callback_data="pay_cryptobot"))
    builder.row(InlineKeyboardButton(text="💠 TON (Tonkeeper)", callback_data="pay_ton"))
    builder.row(InlineKeyboardButton(text="💳 На карту", callback_data="pay_card"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def ton_payment_keyboard(pay_url: str, comment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💠 Оплатить через Tonkeeper", url=pay_url))
    builder.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_ton_payment:{comment}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def account_payment_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 CryptoBot (USDT)", callback_data="pay_account_cryptobot"))
    builder.row(InlineKeyboardButton(text="💠 TON (Tonkeeper)", callback_data="pay_account_ton"))
    builder.row(InlineKeyboardButton(text="💳 На карту", callback_data="pay_account_card"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def ton_account_payment_keyboard(pay_url: str, comment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💠 Оплатить через Tonkeeper", url=pay_url))
    builder.row(InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_ton_account:{comment}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


# === Admin ===
def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promocodes"))
    builder.row(InlineKeyboardButton(text="📢 Рассылка всем", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def back_to_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def admin_promocodes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo"))
    builder.row(InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return builder.as_markup()


def admin_promo_list_keyboard(promocodes: list[Promocode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo in promocodes:
        status = "✅" if promo.uses_count >= promo.max_uses else "🟢"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {promo.code} ({promo.duration_days}д) [{promo.uses_count}/{promo.max_uses}]",
                callback_data=f"admin_promo_info:{promo.id}",
            ),
            InlineKeyboardButton(
                text="🗑️",
                callback_data=f"admin_delete_promo:{promo.id}",
            ),
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


# === Code input keyboard ===
def code_input_keyboard(current_code: str = "") -> InlineKeyboardMarkup:
    """Numpad-style keyboard for entering verification code."""
    builder = InlineKeyboardBuilder()
    # Row 1: 1 2 3
    builder.row(
        InlineKeyboardButton(text="1️⃣", callback_data="code_digit:1"),
        InlineKeyboardButton(text="2️⃣", callback_data="code_digit:2"),
        InlineKeyboardButton(text="3️⃣", callback_data="code_digit:3"),
    )
    # Row 2: 4 5 6
    builder.row(
        InlineKeyboardButton(text="4️⃣", callback_data="code_digit:4"),
        InlineKeyboardButton(text="5️⃣", callback_data="code_digit:5"),
        InlineKeyboardButton(text="6️⃣", callback_data="code_digit:6"),
    )
    # Row 3: 7 8 9
    builder.row(
        InlineKeyboardButton(text="7️⃣", callback_data="code_digit:7"),
        InlineKeyboardButton(text="8️⃣", callback_data="code_digit:8"),
        InlineKeyboardButton(text="9️⃣", callback_data="code_digit:9"),
    )
    # Row 4: Clear 0 Backspace
    builder.row(
        InlineKeyboardButton(text="🗑️", callback_data="code_clear"),
        InlineKeyboardButton(text="0️⃣", callback_data="code_digit:0"),
        InlineKeyboardButton(text="⬅️", callback_data="code_backspace"),
    )
    # Row 5: Cancel / Confirm
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="code_confirm"),
    )
    return builder.as_markup()
