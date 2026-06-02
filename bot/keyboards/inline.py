import re
import html as _html_lib
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database.db import Account, Mailing, MailingMessage, MailingTarget, Promocode, RequiredChannel
from ..utils.premium_emoji import EMOJI_MAP


def _strip_html(text: str) -> str:
    clean = re.sub(r'<[^>]+>', '', text)
    return _html_lib.unescape(clean)


# Sorted longest-first so "⚡️" matches before "⚡"
_SORTED_EMOJI = sorted(EMOJI_MAP.keys(), key=len, reverse=True)


def _btn(text: str, **kwargs) -> InlineKeyboardButton:
    """Create InlineKeyboardButton with premium animated emoji icon if text starts with a mapped emoji."""
    for emoji in _SORTED_EMOJI:
        if text.startswith(emoji):
            clean = text[len(emoji):].lstrip()
            return InlineKeyboardButton(
                text=clean if clean else " ",
                icon_custom_emoji_id=EMOJI_MAP[emoji],
                **kwargs,
            )
    return InlineKeyboardButton(text=text, **kwargs)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("📋 Мои рассылки", callback_data="mailings", style="primary"),
        _btn("👤 Аккаунты", callback_data="accounts", style="primary"),
    )
    builder.row(
        _btn("💳 Подписка", callback_data="subscription", style="primary"),
        _btn("🤝 Рефералы", callback_data="referral", style="primary"),
    )
    builder.row(_btn("ℹ️ Помощь", callback_data="help", style="primary"))
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def help_keyboard(support_username: Optional[str] = None,
                  privacy_url: Optional[str] = None,
                  terms_url: Optional[str] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if privacy_url:
        builder.row(InlineKeyboardButton(
            text="Политика конфиденциальности", url=privacy_url,
            icon_custom_emoji_id="5429405838345265327"
        ))
    if terms_url:
        builder.row(InlineKeyboardButton(
            text="Пользовательское соглашение", url=terms_url,
            icon_custom_emoji_id="5188639433544447819"
        ))
    if support_username:
        builder.row(InlineKeyboardButton(
            text="Поддержка", url=f"https://t.me/{support_username.lstrip('@')}",
            icon_custom_emoji_id="5388805667114988189"
        ))
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def skip_thread_keyboard(mailing_id: int, target_identifier: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⏭️ Пропустить (General)",
        callback_data=f"skip_thread:{mailing_id}:{target_identifier}"
    ))
    return builder.as_markup()


# === Accounts ===
def accounts_keyboard(accounts: list[Account]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        status = "🟢" if acc.is_active else "🔴"
        builder.row(_btn(f"{status} {acc.display_name}", callback_data=f"account:{acc.id}", style="primary"))
    builder.row(
        _btn("➕ Добавить аккаунт", callback_data="add_account", style="success"),
        _btn("◀️ Главное меню", callback_data="main_menu", style="primary"),
    )
    return builder.as_markup()


def account_menu_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("✉️ Рассылки аккаунта", callback_data=f"account_mailings:{account_id}", style="primary"))
    builder.row(
        _btn("🤖 Автоответ (личные)", callback_data=f"autoresponder:{account_id}", style="primary"),
        _btn("💬 Автоответ (группы)", callback_data=f"group_autoresponder:{account_id}", style="primary"),
    )
    builder.row(
        _btn("🌐 Прокси", callback_data=f"set_proxy:{account_id}", style="primary"),
        _btn("✏️ Переименовать", callback_data=f"rename_account:{account_id}", style="primary"),
    )
    builder.row(_btn("❌ Удалить", callback_data=f"delete_account:{account_id}", style="danger"))
    builder.row(_btn("◀️ Назад", callback_data="accounts", style="primary"))
    return builder.as_markup()


def delete_account_confirm_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, удалить", callback_data=f"confirm_delete_account:{account_id}", style="danger"),
        _btn("◀️ Назад", callback_data=f"account:{account_id}", style="primary"),
    )
    return builder.as_markup()


def account_payment_keyboard(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить", url=pay_url, style="success"))
    builder.row(
        _btn("🔄 Проверить оплату", callback_data=f"check_account_payment:{invoice_id}", style="primary"),
        _btn("◀️ Назад", callback_data="account_payment_methods", style="primary"),
    )
    return builder.as_markup()


def add_account_proxy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Купить аккаунт и прокси", url="https://t.me/FeTgAccountbot", icon_custom_emoji_id="5312361253610475399"))
    builder.row(
        _btn("✅ Да, добавить прокси", callback_data="add_account_set_proxy", style="success"),
        _btn("➡️ Продолжить", callback_data="add_account_skip_proxy", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="accounts", style="primary"))
    return builder.as_markup()


def add_account_api_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, ввести API", callback_data="add_account_set_api", style="success"),
        _btn("➡️ Продолжить", callback_data="add_account_skip_api", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="add_account", style="primary"))
    return builder.as_markup()


def account_payment_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💎 CryptoBot (USDT)", callback_data="pay_account_cryptobot", style="primary"),
        _btn("💠 TON", callback_data="pay_account_ton", style="primary"),
    )
    builder.row(_btn("💳 На карту", callback_data="pay_account_card", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="accounts", style="primary"))
    return builder.as_markup()


def ton_account_payment_keyboard(pay_url: str, comment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💠 Оплатить через Tonkeeper", url=pay_url, style="success"))
    builder.row(
        _btn("🔄 Проверить оплату", callback_data=f"check_ton_account:{comment}", style="primary"),
        _btn("◀️ Назад", callback_data="account_payment_methods", style="primary"),
    )
    return builder.as_markup()


# === Autoresponder ===
def autoresponder_keyboard(account_id: int, enabled: bool, notify_enabled: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    toggle_style = "danger" if enabled else "success"
    builder.row(_btn(toggle_text, callback_data=f"toggle_autoresponder:{account_id}", style=toggle_style))
    notify_text = "🔔 Уведомления: ВКЛ" if notify_enabled else "🔔 Уведомления: ВЫКЛ"
    notify_style = "danger" if notify_enabled else "success"
    builder.row(
        _btn("✏️ Изменить текст", callback_data=f"edit_autoresponder_text:{account_id}", style="primary"),
        _btn(notify_text, callback_data=f"toggle_notify:{account_id}", style=notify_style),
    )
    builder.row(
        _btn("🗑️ Очистить историю", callback_data=f"clear_autoresponder_history:{account_id}", style="danger"),
        _btn("◀️ Назад", callback_data=f"account:{account_id}", style="primary"),
    )
    return builder.as_markup()


def group_autoresponder_keyboard(account_id: int, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if enabled else "🟢 Включить"
    toggle_style = "danger" if enabled else "success"
    builder.row(_btn(toggle_text, callback_data=f"toggle_group_autoresponder:{account_id}", style=toggle_style))
    builder.row(
        _btn("✏️ Изменить текст", callback_data=f"edit_group_autoresponder_text:{account_id}", style="primary"),
        _btn("◀️ Назад", callback_data=f"account:{account_id}", style="primary"),
    )
    return builder.as_markup()


# === Mailings ===
def mailings_keyboard(mailings: list[Mailing]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in mailings:
        status = "🟢" if m.is_active else "🔴"
        builder.row(_btn(f"{status} {m.name}", callback_data=f"mailing:{m.id}", style="primary"))
    builder.row(
        _btn("➕ Создать рассылку", callback_data="create_mailing", style="success"),
        _btn("◀️ Главное меню", callback_data="main_menu", style="primary"),
    )
    return builder.as_markup()


def mailing_menu_keyboard(mailing: Mailing) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Остановить" if mailing.is_active else "🟢 Запустить"
    toggle_style = "danger" if mailing.is_active else "success"
    builder.row(_btn(toggle_text, callback_data=f"toggle_mailing:{mailing.id}", style=toggle_style))
    builder.row(
        _btn("📝 Сообщения", callback_data=f"mailing_messages:{mailing.id}", style="primary"),
        _btn("🎯 Целевые чаты", callback_data=f"mailing_targets:{mailing.id}", style="primary"),
    )
    builder.row(
        _btn("⏰ Время активности", callback_data=f"mailing_hours:{mailing.id}", style="primary"),
        _btn("🔃 Аккаунт", callback_data=f"change_mailing_account:{mailing.id}", style="primary"),
    )
    builder.row(
        _btn("👥 Несколько аккаунтов", callback_data=f"mailing_multi_accounts:{mailing.id}", style="primary"),
    )
    reply_label = "↩️ Ответная рассылка: ВКЛ" if mailing.reply_mode else "↩️ Ответная рассылка: ВЫКЛ"
    builder.row(_btn(reply_label, callback_data=f"mailing_reply_mode:{mailing.id}", style="primary"))
    builder.row(
        _btn("❌ Удалить рассылку", callback_data=f"delete_mailing:{mailing.id}", style="danger"),
        _btn("◀️ Назад", callback_data="mailings", style="primary"),
    )
    return builder.as_markup()


def reply_mode_select_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("🔃 На последнее", callback_data=f"reply_mode_last:{mailing_id}", style="primary"))
    builder.row(_btn("🔢 На N-е с конца", callback_data=f"reply_mode_fixed:{mailing_id}", style="primary"))
    builder.row(_btn("🎲 Случайно", callback_data=f"reply_mode_random:{mailing_id}", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def reply_mode_fixed_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*[
        InlineKeyboardButton(text=str(n), callback_data=f"reply_mode_fixed_pos:{mailing_id}:{n}")
        for n in range(2, 8)
    ])
    builder.row(*[
        InlineKeyboardButton(text=str(n), callback_data=f"reply_mode_fixed_pos:{mailing_id}:{n}")
        for n in range(8, 11)
    ])
    builder.row(_btn("◀️ Назад", callback_data=f"mailing_reply_mode:{mailing_id}", style="primary"))
    return builder.as_markup()


def delete_mailing_confirm_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, удалить", callback_data=f"confirm_delete_mailing:{mailing_id}", style="danger"),
        _btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"),
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
        builder.row(_btn(f"🗑️ {preview}", callback_data=f"delete_msg:{msg.id}", style="danger"))
    builder.row(
        _btn("➕ Текст/фото", callback_data=f"add_mailing_message:{mailing_id}", style="primary"),
        _btn("📨 Переслать", callback_data=f"add_mailing_forward:{mailing_id}", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def parse_mode_keyboard(message_id: int, mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="HTML", callback_data=f"set_parse_mode:html:{message_id}:{mailing_id}", style="primary"),
        InlineKeyboardButton(text="Markdown", callback_data=f"set_parse_mode:md:{message_id}:{mailing_id}", style="primary"),
        InlineKeyboardButton(text="Plain", callback_data=f"set_parse_mode:plain:{message_id}:{mailing_id}", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data=f"mailing_messages:{mailing_id}", style="primary"))
    return builder.as_markup()


def photo_collection_keyboard(mailing_id: int, photo_count: int, is_create: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    prefix = "create_" if is_create else "edit_"
    builder.row(
        _btn(f"💾 Сохранить ({photo_count} фото)", callback_data=f"{prefix}save_photos:{mailing_id}", style="success"),
        _btn("◀️ Назад", callback_data="cancel", style="primary"),
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
        if target.is_forum or target.thread_id:
            thread_text = f"🧵#{target.thread_id}" if target.thread_id else "🧵"
            builder.row(
                _btn(f"🗑️ {target.chat_identifier}", callback_data=f"delete_target:{target.id}", style="danger"),
                _btn(iv_text, callback_data=f"edit_target_interval:{target.id}:{mailing_id}", style="primary"),
                InlineKeyboardButton(text=thread_text, callback_data=f"set_target_thread:{target.id}:{mailing_id}"),
            )
        else:
            builder.row(
                _btn(f"🗑️ {target.chat_identifier}", callback_data=f"delete_target:{target.id}", style="danger"),
                _btn(iv_text, callback_data=f"edit_target_interval:{target.id}:{mailing_id}", style="primary"),
            )
    builder.row(
        _btn("➕ Добавить чат", callback_data=f"add_mailing_target:{mailing_id}", style="primary"),
        _btn("📁 Добавить папку", callback_data=f"add_folder_target:{mailing_id}", style="primary"),
    )
    builder.row(_btn("📄 Загрузить .txt", callback_data=f"add_txt_target:{mailing_id}", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


# === Mailing creation ===
def select_account_keyboard(accounts: list[Account]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.row(_btn(f"📱 {acc.display_name}", callback_data=f"select_account:{acc.id}", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="mailings", style="primary"))
    return builder.as_markup()


def select_account_for_mailing_keyboard(accounts: list[Account], mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.row(_btn(
            f"📱 {acc.display_name}",
            callback_data=f"set_mailing_account:{acc.id}:{mailing_id}",
            style="primary",
        ))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def multi_account_select_keyboard(accounts: list[Account], selected_ids: list[int], mailing_id: int, rotation_mode: str = "per_target") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        prefix = "✅ " if acc.id in selected_ids else "📱 "
        builder.row(_btn(
            f"{prefix}{acc.display_name}",
            callback_data=f"toggle_mailing_account:{acc.id}:{mailing_id}",
            style="primary",
        ))
    if rotation_mode == "per_cycle":
        mode_label = "🔄 Режим: все чаты одним акк."
    else:
        mode_label = "🔄 Режим: по одному чату"
    builder.row(_btn(mode_label, callback_data=f"toggle_rotation_mode:{mailing_id}", style="primary"))
    builder.row(_btn("✅ Готово", callback_data=f"mailing:{mailing_id}", style="success"))
    return builder.as_markup()


def mailing_creation_messages_keyboard(mailing_id: int, messages: list[MailingMessage]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for msg in messages:
        preview = _msg_button_preview(msg)
        builder.row(_btn(f"🗑️ {preview}", callback_data=f"create_delete_msg:{msg.id}", style="danger"))
    builder.row(
        _btn("➕ Текст/фото", callback_data=f"create_add_message:{mailing_id}", style="primary"),
        _btn("📨 Переслать", callback_data=f"create_add_forward:{mailing_id}", style="primary"),
    )
    if messages:
        builder.row(_btn("✅ Готово", callback_data=f"create_messages_done:{mailing_id}", style="success"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def mailing_creation_targets_keyboard(mailing_id: int, targets: list[MailingTarget]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in targets:
        builder.row(_btn(f"🗑️ {target.chat_identifier}", callback_data=f"create_delete_target:{target.id}", style="danger"))
    builder.row(
        _btn("➕ Добавить чат", callback_data=f"create_add_target:{mailing_id}", style="primary"),
        _btn("📁 Добавить папку", callback_data=f"create_add_folder:{mailing_id}", style="primary"),
    )
    builder.row(_btn("📄 Загрузить .txt", callback_data=f"create_add_txt:{mailing_id}", style="primary"))
    if targets:
        builder.row(
            _btn("✅ Готово", callback_data=f"create_targets_done:{mailing_id}", style="success"),
            _btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"),
        )
    else:
        builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def active_hours_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("⏭️ Пропустить (24/7)", callback_data=f"skip_hours:{mailing_id}", style="primary"),
        _btn("⏰ Настроить", callback_data=f"setup_hours:{mailing_id}", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def launch_mailing_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("🚀 Запустить рассылку", callback_data=f"launch_mailing:{mailing_id}", style="success"),
        _btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"),
    )
    return builder.as_markup()


# === Subscription ===
def subscription_keyboard(has_subscription: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    sub_text = "🔄 Продлить подписку" if has_subscription else "💳 Купить подписку"
    builder.row(
        _btn("🎟 Ввести промокод", callback_data="enter_promocode", style="primary"),
        _btn(sub_text, callback_data="buy_subscription", style="success"),
    )
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def subscription_plan_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("📅 7 дней", callback_data="sub_plan:7", style="primary"),
        _btn("📅 30 дней", callback_data="sub_plan:30", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="subscription", style="primary"))
    return builder.as_markup()


def payment_keyboard(pay_url: str, invoice_id: str, plan_days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить", url=pay_url, style="success"))
    builder.row(
        _btn("🔄 Проверить оплату", callback_data=f"check_payment:{invoice_id}", style="primary"),
        _btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"),
    )
    return builder.as_markup()


def payment_method_keyboard(show_platega: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💎 CryptoBot (USDT)", callback_data="pay_cryptobot", style="primary"),
        _btn("💠 TON", callback_data="pay_ton", style="primary"),
    )
    if show_platega:
        builder.row(_btn("🇷🇺 Оплата рублями (СБП)", callback_data="pay_platega", style="primary"))
    builder.row(_btn("🇺🇦 На карту(грн)", callback_data="pay_card", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="buy_subscription", style="primary"))
    return builder.as_markup()


def platega_payment_keyboard(pay_url: str, order_id: str, plan_days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить через СБП", url=pay_url, style="success"))
    builder.row(
        _btn("🔄 Проверить оплату", callback_data=f"check_platega:{order_id}", style="primary"),
        _btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"),
    )
    return builder.as_markup()


def ton_payment_keyboard(pay_url: str, comment: str, plan_days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💠 Оплатить через Tonkeeper", url=pay_url, style="success"))
    builder.row(
        _btn("🔄 Проверить оплату", callback_data=f"check_ton_payment:{comment}", style="primary"),
        _btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"),
    )
    return builder.as_markup()


def back_to_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="buy_subscription", style="primary"))
    return builder.as_markup()


# === Referral ===
def referral_keyboard(can_withdraw: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_withdraw:
        builder.row(
            _btn("💸 Вывести баланс", callback_data="withdraw_ref_balance", style="success"),
            _btn("◀️ Главное меню", callback_data="main_menu", style="primary"),
        )
    else:
        builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def withdraw_wallet_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="referral", style="primary"))
    return builder.as_markup()


# === Required channels ===
def channel_check_keyboard(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        url = f"https://t.me/{ch.channel_username}" if ch.channel_username else None
        if url:
            builder.row(_btn(f"📢 {ch.channel_title}", url=url, style="primary"))
    builder.row(_btn("✅ Я подписался — проверить", callback_data="check_channels", style="success"))
    return builder.as_markup()


# === Admin ===
def admin_stats_period_keyboard(active: str = "day") -> InlineKeyboardMarkup:
    periods = [("День", "day"), ("Неделя", "week"), ("Месяц", "month"), ("Год", "year")]
    builder = InlineKeyboardBuilder()
    builder.row(*[
        InlineKeyboardButton(
            text=f"▶ {label}" if active == k else label,
            callback_data=f"admin_stats:{k}",
            style="success" if active == k else "primary",
        )
        for label, k in periods
    ])
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("📊 Статистика", callback_data="admin_stats", style="primary"),
        _btn("🎟 Промокоды", callback_data="admin_promocodes", style="primary"),
    )
    builder.row(
        _btn("💳 Статистика подписок", callback_data="admin_sub_stats", style="primary"),
    )
    builder.row(
        _btn("📢 Рассылка всем", callback_data="admin_broadcast", style="primary"),
        InlineKeyboardButton(text="📡 Обяз. каналы", callback_data="admin_channels", style="primary"),
    )
    builder.row(
        _btn("⚙️ Настройки", callback_data="admin_settings", style="primary"),
        _btn("💸 Запросы вывода", callback_data="admin_withdrawals", style="primary"),
    )
    builder.row(
        _btn("💳 Подписки", callback_data="admin_subscriptions", style="primary"),
    )
    builder.row(
        _btn("📤 Экспорт БД", callback_data="admin_export_db", style="primary"),
    )
    builder.row(
        _btn("📥 Импорт БД", callback_data="admin_import_db", style="primary"),
        _btn("🗑 Очистить мёртвые аккаунты", callback_data="admin_cleanup_accounts", style="danger"),
    )
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def admin_sub_stats_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💎 CryptoBot", callback_data="admin_sub_method:cryptobot", style="primary"),
        _btn("💠 TON", callback_data="admin_sub_method:ton", style="primary"),
    )
    builder.row(_btn("🇷🇺 Platega (СБП)", callback_data="admin_sub_method:platega", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_sub_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="admin_sub_stats", style="primary"))
    return builder.as_markup()


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💰 Цена 7 дней", callback_data="admin_set_price_7d", style="primary"),
        _btn("💰 Цена 30 дней", callback_data="admin_set_price_30d", style="primary"),
    )
    builder.row(
        _btn("🤝 % рефералов", callback_data="admin_set_ref_percent", style="primary"),
        _btn("💸 Мин. вывод", callback_data="admin_set_min_withdraw", style="primary"),
    )
    builder.row(_btn("💳 Менеджер (оплата картой)", callback_data="admin_set_card_manager", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_channels_keyboard(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.row(_btn(
            f"🗑️ {ch.channel_title}",
            callback_data=f"admin_del_channel:{ch.channel_id}",
            style="danger",
        ))
    builder.row(
        _btn("➕ Добавить канал", callback_data="admin_add_channel", style="success"),
        _btn("◀️ Назад", callback_data="admin_back", style="primary"),
    )
    return builder.as_markup()


def admin_withdrawals_keyboard(requests) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for req in requests:
        builder.row(
            _btn(
                f"✅ #{req.id} ({req.amount} USDT)",
                callback_data=f"admin_approve_withdraw:{req.id}",
                style="success",
            ),
            _btn(
                "❌",
                callback_data=f"admin_decline_withdraw:{req.id}",
                style="danger",
            ),
        )
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def promo_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, платная подписка", callback_data="promo_is_sub:1", style="success"),
        _btn("❌ Нет, обычный промокод", callback_data="promo_is_sub:0", style="primary"),
    )
    return builder.as_markup()


def admin_promocodes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("➕ Создать промокод", callback_data="admin_create_promo", style="success"),
        _btn("📋 Список промокодов", callback_data="admin_list_promos", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_promo_list_keyboard(promocodes: list[Promocode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo in promocodes:
        status = "✅" if promo.uses_count >= promo.max_uses else "🟢"
        builder.row(
            _btn(
                f"{status} {promo.code} ({promo.duration_days}д) [{promo.uses_count}/{promo.max_uses}]",
                callback_data=f"admin_promo_info:{promo.id}",
                style="primary",
            ),
            _btn("🗑️", callback_data=f"admin_delete_promo:{promo.id}", style="danger"),
        )
    builder.row(_btn("◀️ Назад", callback_data="admin_promocodes", style="primary"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="cancel", style="primary"))
    return builder.as_markup()


# === Code input keyboard ===
def code_input_keyboard(current_code: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1️⃣", callback_data="code_digit:1", style="primary"),
        InlineKeyboardButton(text="2️⃣", callback_data="code_digit:2", style="primary"),
        InlineKeyboardButton(text="3️⃣", callback_data="code_digit:3", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="4️⃣", callback_data="code_digit:4", style="primary"),
        InlineKeyboardButton(text="5️⃣", callback_data="code_digit:5", style="primary"),
        InlineKeyboardButton(text="6️⃣", callback_data="code_digit:6", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="7️⃣", callback_data="code_digit:7", style="primary"),
        InlineKeyboardButton(text="8️⃣", callback_data="code_digit:8", style="primary"),
        InlineKeyboardButton(text="9️⃣", callback_data="code_digit:9", style="primary"),
    )
    builder.row(
        _btn("🗑️", callback_data="code_clear", style="danger"),
        InlineKeyboardButton(text="0️⃣", callback_data="code_digit:0", style="primary"),
        _btn("⬅️", callback_data="code_backspace", style="primary"),
    )
    builder.row(
        _btn("◀️ Назад", callback_data="cancel", style="primary"),
        _btn("✅ Подтвердить", callback_data="code_confirm", style="success"),
    )
    return builder.as_markup()
