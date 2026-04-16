import re
import html as _html_lib

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database.db import Account, Mailing, MailingMessage, MailingTarget, Promocode, RequiredChannel


def _strip_html(text: str) -> str:
    clean = re.sub(r'<[^>]+>', '', text)
    return _html_lib.unescape(clean)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Мои рассылки", callback_data="mailings"),
        InlineKeyboardButton(text="👤 Аккаунты", callback_data="accounts"),
    )
    builder.row(
        InlineKeyboardButton(text="💳 Подписка", callback_data="subscription"),
        InlineKeyboardButton(text="🤝 Рефералы", callback_data="referral"),
    )
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
        status = "🟢" if acc.is_active else "🔴"
        builder.row(InlineKeyboardButton(text=f"{status} {acc.display_name}", callback_data=f"account:{acc.id}"))
    builder.row(
        InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account"),
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"),
    )
    return builder.as_markup()


def account_menu_keyboard(account_id: int, auto_subscribe: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✉️ Рассылки аккаунта", callback_data=f"account_mailings:{account_id}"))
    builder.row(
        InlineKeyboardButton(text="🤖 Автоответ (личные)", callback_data=f"autoresponder:{account_id}"),
        InlineKeyboardButton(text="💬 Автоответ (группы)", callback_data=f"group_autoresponder:{account_id}"),
    )
    sub_text = "🔔 Авто-подписка: ВКЛ" if auto_subscribe else "🔕 Авто-подписка: ВЫКЛ"
    builder.row(
        InlineKeyboardButton(text=sub_text, callback_data=f"toggle_auto_subscribe:{account_id}"),
        InlineKeyboardButton(text="🌐 Прокси", callback_data=f"set_proxy:{account_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"rename_account:{account_id}"),
        InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_account:{account_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def delete_account_confirm_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_account:{account_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"account:{account_id}"),
    )
    return builder.as_markup()


def account_payment_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=pay_url))
    builder.row(
        InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_account_payment:{invoice_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"),
    )
    return builder.as_markup()


def add_account_proxy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, добавить прокси", callback_data="add_account_set_proxy"),
        InlineKeyboardButton(text="➡️ Продолжить", callback_data="add_account_skip_proxy"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def add_account_api_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, ввести API", callback_data="add_account_set_api"),
        InlineKeyboardButton(text="➡️ Продолжить", callback_data="add_account_skip_api"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def account_payment_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💎 CryptoBot (USDT)", callback_data="pay_account_cryptobot"),
        InlineKeyboardButton(text="💠 TON", callback_data="pay_account_ton"),
    )
    builder.row(InlineKeyboardButton(text="💳 На карту", callback_data="pay_account_card"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"))
    return builder.as_markup()


def ton_account_payment_keyboard(pay_url: str, comment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💠 Оплатить через Tonkeeper", url=pay_url))
    builder.row(
        InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_ton_account:{comment}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="accounts"),
    )
    return builder.as_markup()


# === Autoresponder ===
def autoresponder_keyboard(account_id: int, enabled: bool, notify_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_autoresponder:{account_id}"))
    notify_text = "🔔 Уведомления: ВКЛ" if notify_enabled else "🔕 Уведомления: ВЫКЛ"
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"edit_autoresponder_text:{account_id}"),
        InlineKeyboardButton(text=notify_text, callback_data=f"toggle_notify:{account_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑️ Очистить историю", callback_data=f"clear_autoresponder_history:{account_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"account:{account_id}"),
    )
    return builder.as_markup()


def group_autoresponder_keyboard(account_id: int, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_group_autoresponder:{account_id}"))
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"edit_group_autoresponder_text:{account_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"account:{account_id}"),
    )
    return builder.as_markup()


# === Mailings ===
def mailings_keyboard(mailings: list[Mailing]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in mailings:
        status = "🟢" if m.is_active else "🔴"
        builder.row(InlineKeyboardButton(text=f"{status} {m.name}", callback_data=f"mailing:{m.id}"))
    builder.row(
        InlineKeyboardButton(text="➕ Создать рассылку", callback_data="create_mailing"),
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"),
    )
    return builder.as_markup()


def mailing_menu_keyboard(mailing: Mailing) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Остановить" if mailing.is_active else "🟢 Запустить"
    builder.row(InlineKeyboardButton(text=toggle_text, callback_data=f"toggle_mailing:{mailing.id}"))
    builder.row(
        InlineKeyboardButton(text="📝 Сообщения", callback_data=f"mailing_messages:{mailing.id}"),
        InlineKeyboardButton(text="🎯 Целевые чаты", callback_data=f"mailing_targets:{mailing.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Время активности", callback_data=f"mailing_hours:{mailing.id}"),
        InlineKeyboardButton(text="🔄 Аккаунт", callback_data=f"change_mailing_account:{mailing.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Удалить рассылку", callback_data=f"delete_mailing:{mailing.id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="mailings"),
    )
    return builder.as_markup()


def delete_mailing_confirm_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_mailing:{mailing_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing:{mailing_id}"),
    )
    return builder.as_markup()


# === Mailing messages ===
def _msg_button_preview(msg: MailingMessage) -> str:
    if msg.is_forward:
        return f"[Переслано] {msg.forward_peer} #{msg.forward_msg_id}"
    photo_count = len(msg.photo_paths)
    prefix = f"[{photo_count} Фото] " if photo_count > 1 else "[Фото] " if photo_count == 1 else ""
    text = _strip_html(msg.text or "")
    max_len = 25 if photo_count else 30
    preview = text[:max_len] + "..." if len(text) > max_len else text
    return f"{prefix}{preview}" if (prefix or preview) else "[Фото]"


def mailing_messages_keyboard(mailing_id: int, messages: list[MailingMessage]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for msg in messages:
        preview = _msg_button_preview(msg)
        builder.row(InlineKeyboardButton(text=f"🗑️ {preview}", callback_data=f"delete_msg:{msg.id}"))
    builder.row(
        InlineKeyboardButton(text="➕ Текст/фото", callback_data=f"add_mailing_message:{mailing_id}"),
        InlineKeyboardButton(text="📨 Переслать", callback_data=f"add_mailing_forward:{mailing_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing:{mailing_id}"))
    return builder.as_markup()


def parse_mode_keyboard(message_id: int, mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="HTML", callback_data=f"set_parse_mode:html:{message_id}:{mailing_id}"),
        InlineKeyboardButton(text="Markdown", callback_data=f"set_parse_mode:md:{message_id}:{mailing_id}"),
        InlineKeyboardButton(text="Plain", callback_data=f"set_parse_mode:plain:{message_id}:{mailing_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing_messages:{mailing_id}"))
    return builder.as_markup()


def photo_collection_keyboard(mailing_id: int, photo_count: int, is_create: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    prefix = "create_" if is_create else "edit_"
    builder.row(
        InlineKeyboardButton(text=f"💾 Сохранить ({photo_count} фото)", callback_data=f"{prefix}save_photos:{mailing_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="cancel"),
    )
    return builder.as_markup()


# === Mailing targets ===
def _format_target_interval(target: MailingTarget) -> str:
    secs = target.interval_seconds
    if secs is None:
        return "⏱️ Умолч."
    if secs >= 3600:
        return f"⏱️ {secs // 3600}ч"
    elif secs >= 60:
        return f"⏱️ {secs // 60}м"
    return f"⏱️ {secs}с"


def mailing_targets_keyboard(mailing_id: int, targets: list[MailingTarget]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in targets:
        iv_text = _format_target_interval(target)
        builder.row(
            InlineKeyboardButton(text=f"🗑️ {target.chat_identifier}", callback_data=f"delete_target:{target.id}"),
            InlineKeyboardButton(text=iv_text, callback_data=f"edit_target_interval:{target.id}:{mailing_id}"),
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
        builder.row(InlineKeyboardButton(text=f"📱 {acc.display_name}", callback_data=f"select_account:{acc.id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="mailings"))
    return builder.as_markup()


def select_account_for_mailing_keyboard(accounts: list[Account], mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.row(InlineKeyboardButton(
            text=f"📱 {acc.display_name}",
            callback_data=f"set_mailing_account:{acc.id}:{mailing_id}"
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"mailing:{mailing_id}"))
    return builder.as_markup()


def mailing_creation_messages_keyboard(mailing_id: int, messages: list[MailingMessage]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for msg in messages:
        preview = _msg_button_preview(msg)
        builder.row(InlineKeyboardButton(text=f"🗑️ {preview}", callback_data=f"create_delete_msg:{msg.id}"))
    builder.row(
        InlineKeyboardButton(text="➕ Текст/фото", callback_data=f"create_add_message:{mailing_id}"),
        InlineKeyboardButton(text="📨 Переслать", callback_data=f"create_add_forward:{mailing_id}"),
    )
    if messages:
        builder.row(InlineKeyboardButton(text="✅ Готово", callback_data=f"create_messages_done:{mailing_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def mailing_creation_targets_keyboard(mailing_id: int, targets: list[MailingTarget]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in targets:
        builder.row(InlineKeyboardButton(text=f"🗑️ {target.chat_identifier}", callback_data=f"create_delete_target:{target.id}"))
    builder.row(
        InlineKeyboardButton(text="➕ Добавить чат", callback_data=f"create_add_target:{mailing_id}"),
        InlineKeyboardButton(text="📁 Добавить папку", callback_data=f"create_add_folder:{mailing_id}"),
    )
    if targets:
        builder.row(
            InlineKeyboardButton(text="✅ Готово", callback_data=f"create_targets_done:{mailing_id}"),
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"cancel_creation:{mailing_id}"),
        )
    else:
        builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def active_hours_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏭️ Пропустить (24/7)", callback_data=f"skip_hours:{mailing_id}"),
        InlineKeyboardButton(text="⏰ Настроить", callback_data=f"setup_hours:{mailing_id}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cancel_creation:{mailing_id}"))
    return builder.as_markup()


def launch_mailing_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data=f"launch_mailing:{mailing_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"cancel_creation:{mailing_id}"),
    )
    return builder.as_markup()


# === Subscription ===
def subscription_keyboard(has_subscription: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    sub_text = "🔄 Продлить подписку" if has_subscription else "💳 Купить подписку"
    builder.row(
        InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promocode"),
        InlineKeyboardButton(text=sub_text, callback_data="buy_subscription"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def subscription_plan_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 7 дней", callback_data="sub_plan:7"),
        InlineKeyboardButton(text="📅 30 дней", callback_data="sub_plan:30"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def payment_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Оплатить", url=pay_url))
    builder.row(
        InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment:{invoice_id}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"),
    )
    return builder.as_markup()


def payment_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💎 CryptoBot (USDT)", callback_data="pay_cryptobot"),
        InlineKeyboardButton(text="💠 TON", callback_data="pay_ton"),
    )
    builder.row(InlineKeyboardButton(text="💳 На карту", callback_data="pay_card"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


def ton_payment_keyboard(pay_url: str, comment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💠 Оплатить через Tonkeeper", url=pay_url))
    builder.row(
        InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_ton_payment:{comment}"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"),
    )
    return builder.as_markup()


def back_to_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="subscription"))
    return builder.as_markup()


# === Referral ===
def referral_keyboard(can_withdraw: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_withdraw:
        builder.row(
            InlineKeyboardButton(text="💸 Вывести баланс", callback_data="withdraw_ref_balance"),
            InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"),
        )
    else:
        builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def withdraw_wallet_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="referral"))
    return builder.as_markup()


# === Required channels ===
def channel_check_keyboard(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        url = f"https://t.me/{ch.channel_username}" if ch.channel_username else None
        if url:
            builder.row(InlineKeyboardButton(text=f"📢 {ch.channel_title}", url=url))
    builder.row(InlineKeyboardButton(text="✅ Я подписался — проверить", callback_data="check_channels"))
    return builder.as_markup()


# === Admin ===
def admin_stats_period_keyboard(active: str = "day") -> InlineKeyboardMarkup:
    periods = [("День", "day"), ("Неделя", "week"), ("Месяц", "month"), ("Год", "year")]
    builder = InlineKeyboardBuilder()
    builder.row(*[
        InlineKeyboardButton(
            text=f"▶ {label}" if active == k else label,
            callback_data=f"admin_stats:{k}",
        )
        for label, k in periods
    ])
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promocodes"),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка всем", callback_data="admin_broadcast"),
        InlineKeyboardButton(text="📡 Обяз. каналы", callback_data="admin_channels"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings"),
        InlineKeyboardButton(text="💸 Запросы вывода", callback_data="admin_withdrawals"),
    )
    builder.row(
        InlineKeyboardButton(text="📤 Экспорт БД", callback_data="admin_export_db"),
        InlineKeyboardButton(text="📥 Импорт БД", callback_data="admin_import_db"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Очистить мёртвые аккаунты", callback_data="admin_cleanup_accounts"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Цена 7 дней", callback_data="admin_set_price_7d"),
        InlineKeyboardButton(text="💰 Цена 30 дней", callback_data="admin_set_price_30d"),
    )
    builder.row(
        InlineKeyboardButton(text="🤝 % рефералов", callback_data="admin_set_ref_percent"),
        InlineKeyboardButton(text="💸 Мин. вывод", callback_data="admin_set_min_withdraw"),
    )
    builder.row(InlineKeyboardButton(text="💳 Менеджер (оплата картой)", callback_data="admin_set_card_manager"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return builder.as_markup()


def admin_channels_keyboard(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.row(InlineKeyboardButton(
            text=f"🗑️ {ch.channel_title}",
            callback_data=f"admin_del_channel:{ch.channel_id}",
        ))
    builder.row(
        InlineKeyboardButton(text="➕ Добавить канал", callback_data="admin_add_channel"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"),
    )
    return builder.as_markup()


def admin_withdrawals_keyboard(requests) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for req in requests:
        builder.row(
            InlineKeyboardButton(
                text=f"✅ #{req.id} ({req.amount} USDT)",
                callback_data=f"admin_approve_withdraw:{req.id}",
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=f"admin_decline_withdraw:{req.id}",
            ),
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back"))
    return builder.as_markup()


def admin_promocodes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo"),
        InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos"),
    )
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
            InlineKeyboardButton(text="🗑️", callback_data=f"admin_delete_promo:{promo.id}"),
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promocodes"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="cancel"))
    return builder.as_markup()


# === Code input keyboard ===
def code_input_keyboard(current_code: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1️⃣", callback_data="code_digit:1"),
        InlineKeyboardButton(text="2️⃣", callback_data="code_digit:2"),
        InlineKeyboardButton(text="3️⃣", callback_data="code_digit:3"),
    )
    builder.row(
        InlineKeyboardButton(text="4️⃣", callback_data="code_digit:4"),
        InlineKeyboardButton(text="5️⃣", callback_data="code_digit:5"),
        InlineKeyboardButton(text="6️⃣", callback_data="code_digit:6"),
    )
    builder.row(
        InlineKeyboardButton(text="7️⃣", callback_data="code_digit:7"),
        InlineKeyboardButton(text="8️⃣", callback_data="code_digit:8"),
        InlineKeyboardButton(text="9️⃣", callback_data="code_digit:9"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑️", callback_data="code_clear"),
        InlineKeyboardButton(text="0️⃣", callback_data="code_digit:0"),
        InlineKeyboardButton(text="⬅️", callback_data="code_backspace"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="cancel"),
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="code_confirm"),
    )
    return builder.as_markup()
