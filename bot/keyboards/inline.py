import re
import html as _html_lib
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database.db import Account, Mailing, MailingMessage, MailingTarget, ParserSettings, Promocode, RequiredChannel
from ..utils.premium_emoji import EMOJI_MAP


def _strip_html(text: str) -> str:
    clean = re.sub(r'<[^>]+>', '', text)
    return _html_lib.unescape(clean)


# Sorted longest-first so "⚡️" matches before "⚡"
_SORTED_EMOJI = sorted(EMOJI_MAP.keys(), key=len, reverse=True)

LIST_PAGE_SIZE = 20  # same reasoning as TARGETS_PAGE_SIZE below — keeps
# accounts/mailings/messages list keyboards well under Telegram's
# ~100-button cap regardless of how many a user has.


def _paginate_nav_row(builder: InlineKeyboardBuilder, page: int, total_pages: int, callback_prefix: str):
    if total_pages <= 1:
        return
    nav = []
    if page > 0:
        nav.append(_btn("◀️", callback_data=f"{callback_prefix}:{page - 1}", style="primary"))
    nav.append(_btn(f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
    if page < total_pages - 1:
        nav.append(_btn("▶️", callback_data=f"{callback_prefix}:{page + 1}", style="primary"))
    builder.row(*nav)


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
    builder.row(_btn("🛠 Инструменты", callback_data="tools", style="primary"))
    builder.row(_btn("ℹ️ Помощь", callback_data="help", style="primary"))
    return builder.as_markup()


# === Tools / Parser ===

def tools_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("🔎 Парсер сообщений", callback_data="parser_intro", style="primary"))
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def parser_intro_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("▶️ Настроить парсер", callback_data="parser_configure", style="success"))
    builder.row(_btn("📋 Мои парсеры", callback_data="parser_manage", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="tools", style="primary"))
    return builder.as_markup()


def parser_manage_keyboard(parsers: list[tuple[ParserSettings, Account]], can_add: bool, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(parsers) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for settings, account in parsers[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "🟢" if settings.is_active else "⚫️"
        builder.row(_btn(f"{status} {account.display_name}", callback_data=f"parser_view:{settings.account_id}", style="primary"))
    _paginate_nav_row(builder, page, total_pages, "parser_manage_page")
    if can_add:
        builder.row(_btn("➕ Новый парсер", callback_data="parser_configure", style="success"))
    builder.row(_btn("◀️ Назад", callback_data="parser_intro", style="primary"))
    return builder.as_markup()


def parser_view_keyboard(settings: ParserSettings) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "⏸ Остановить" if settings.is_active else "▶️ Запустить"
    builder.row(_btn(toggle_text, callback_data=f"parser_toggle:{settings.account_id}", style="primary"))
    builder.row(_btn("🗑️ Удалить", callback_data=f"parser_delete:{settings.account_id}", style="danger"))
    builder.row(_btn("◀️ Назад", callback_data="parser_manage", style="primary"))
    return builder.as_markup()


def parser_delete_confirm_keyboard(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, удалить", callback_data=f"parser_delete_confirm:{account_id}", style="danger"),
        _btn("◀️ Отмена", callback_data=f"parser_view:{account_id}", style="primary"),
    )
    return builder.as_markup()


def select_account_for_parser_keyboard(accounts: list[Account], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(accounts) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for acc in accounts[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        builder.row(_btn(f"📱 {acc.display_name}", callback_data=f"parser_select_account:{acc.id}", style="primary"))
    _paginate_nav_row(builder, page, total_pages, "parser_select_account_page")
    builder.row(_btn("◀️ Назад", callback_data="parser_intro", style="primary"))
    return builder.as_markup()


def parser_chats_keyboard(chats: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, chat in enumerate(chats):
        builder.row(_btn(f"🗑️ {chat}", callback_data=f"parser_chat_del:{i}", style="danger"))
    if chats:
        builder.row(_btn("✅ Готово", callback_data="parser_chats_done", style="success"))
    builder.row(_btn("❌ Отмена", callback_data="cancel", style="danger"))
    return builder.as_markup()


def parser_stopwords_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("⏭ Пропустить", callback_data="parser_stopwords_skip", style="primary"))
    builder.row(_btn("❌ Отмена", callback_data="cancel", style="danger"))
    return builder.as_markup()


def parser_autoreply_choice_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да", callback_data="parser_autoreply_yes", style="success"),
        _btn("🚫 Нет", callback_data="parser_autoreply_no", style="primary"),
    )
    builder.row(_btn("❌ Отмена", callback_data="cancel", style="danger"))
    return builder.as_markup()


def parser_notify_keyboard(sender_id: int, username: Optional[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    url = f"https://t.me/{username}" if username else f"tg://user?id={sender_id}"
    builder.row(_btn("✉️ Написать", url=url, style="success"))
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def help_keyboard(support_username: Optional[str] = None,
                  privacy_url: Optional[str] = None,
                  terms_url: Optional[str] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if privacy_url and terms_url:
        builder.row(
            InlineKeyboardButton(text="Политика конф.", url=privacy_url, icon_custom_emoji_id="5429405838345265327"),
            InlineKeyboardButton(text="Польз. соглашение", url=terms_url, icon_custom_emoji_id="5188639433544447819"),
        )
    elif privacy_url:
        builder.row(InlineKeyboardButton(text="Политика конфиденциальности", url=privacy_url, icon_custom_emoji_id="5429405838345265327"))
    elif terms_url:
        builder.row(InlineKeyboardButton(text="Пользовательское соглашение", url=terms_url, icon_custom_emoji_id="5188639433544447819"))
    if support_username:
        builder.row(_btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"))
    builder.row(_btn("📲 Рассылка в ЛС", callback_data="dm_mailing_info", style="success"))
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def dm_mailing_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("🚀 Перейти к боту", url="https://t.me/feAutoSenderDMbot", style="success"))
    builder.row(_btn("◀️ Назад", callback_data="help", style="primary"))
    return builder.as_markup()


def skip_thread_keyboard(mailing_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⏭️ Пропустить (General)",
        callback_data=f"skip_thread:{mailing_id}"
    ))
    builder.row(_btn("◀️ Назад", callback_data="cancel", style="primary"))
    return builder.as_markup()


# === Accounts ===
def accounts_keyboard(accounts: list[Account], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(accounts) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for acc in accounts[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "🟢" if acc.is_active else "🔴"
        builder.row(_btn(f"{status} {acc.display_name}", callback_data=f"account:{acc.id}", style="primary"))
    _paginate_nav_row(builder, page, total_pages, "accounts_page")
    builder.row(
        _btn("➕ Добавить аккаунт", callback_data="add_account", style="success"),
        _btn("◀️ Главное меню", callback_data="main_menu", style="primary"),
    )
    return builder.as_markup()


def account_menu_keyboard(account_id: int, auto_subscribe_sponsors: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("✉️ Рассылки аккаунта", callback_data=f"account_mailings:{account_id}", style="primary"))
    builder.row(
        _btn("🤖 Автоприветствие (личные)", callback_data=f"autoresponder:{account_id}", style="primary"),
        _btn("💬 Автоответ (группы)", callback_data=f"group_autoresponder:{account_id}", style="primary"),
    )
    sponsor_text = "🔴 Автоподписка: ВЫКЛ" if not auto_subscribe_sponsors else "🟢 Автоподписка: ВКЛ"
    builder.row(_btn(sponsor_text, callback_data=f"toggle_sponsor_sub:{account_id}", style="primary"))
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


def account_payment_keyboard(pay_url: str, invoice_id: str, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить", url=pay_url, style="success"))
    if support_username:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_account_payment:{invoice_id}", style="primary"),
            _btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"),
        )
        builder.row(_btn("◀️ Назад", callback_data="account_payment_methods", style="primary"))
    else:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_account_payment:{invoice_id}", style="primary"),
            _btn("◀️ Назад", callback_data="account_payment_methods", style="primary"),
        )
    return builder.as_markup()


def add_account_proxy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Купить аккаунт и прокси", url="https://t.me/FeTgAccountbot?start=ref7145919720", icon_custom_emoji_id="5312361253610475399"))
    builder.row(
        _btn("✅ Да, добавить прокси", callback_data="add_account_set_proxy", style="success"),
        _btn("➡️ Продолжить", callback_data="add_account_skip_proxy", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="accounts", style="primary"))
    return builder.as_markup()


def retry_or_skip_proxy_keyboard() -> InlineKeyboardMarkup:
    """Keep an escape route after a proxy validation failure.

    The add-account flow is already authorized at this point, so reusing its
    normal skip callback is safe and lets the user continue without losing
    the partially completed FSM flow.
    """
    builder = InlineKeyboardBuilder()
    builder.row(_btn("➡️ Продолжить без прокси", callback_data="add_account_skip_proxy", style="primary"))
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
        _btn("💠 GRAM(TON)", callback_data="pay_account_ton", style="primary"),
    )
    builder.row(_btn("⭐️ Telegram Stars", callback_data="pay_account_stars", style="primary"))
    builder.row(_btn("💳 На карту", callback_data="pay_account_card", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="accounts", style="primary"))
    return builder.as_markup()


def ton_account_payment_keyboard(pay_url: str, comment: str, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💠 Оплатить через Tonkeeper", url=pay_url, style="success"))
    if support_username:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_ton_account:{comment}", style="primary"),
            _btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"),
        )
        builder.row(_btn("◀️ Назад", callback_data="account_payment_methods", style="primary"))
    else:
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
def mailings_keyboard(mailings: list[Mailing], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(mailings) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for m in mailings[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "🟢" if m.is_active else "🔴"
        builder.row(_btn(f"{status} {m.name}", callback_data=f"mailing:{m.id}", style="primary"))
    _paginate_nav_row(builder, page, total_pages, "mailings_page")
    builder.row(
        _btn("➕ Создать рассылку", callback_data="create_mailing", style="success"),
        _btn("◀️ Главное меню", callback_data="main_menu", style="primary"),
    )
    return builder.as_markup()


def mailing_menu_keyboard(mailing: Mailing, show_remove_ads: bool = False) -> InlineKeyboardMarkup:
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
    builder.row(InlineKeyboardButton(
        text="Скрытый тег",
        icon_custom_emoji_id="5226774675798923342",
        callback_data=f"hidden_tag:{mailing.id}",
        style="primary",
    ))
    reply_label = "↩️ Ответная рассылка: ВКЛ" if mailing.reply_mode else "↩️ Ответная рассылка: ВЫКЛ"
    builder.row(_btn(reply_label, callback_data=f"mailing_reply_mode:{mailing.id}", style="primary"))
    if show_remove_ads:
        builder.row(_btn("🚫 Убрать рекламу из рассылки", callback_data="subscription", style="danger"))
    builder.row(
        _btn("❌ Удалить рассылку", callback_data=f"delete_mailing:{mailing.id}", style="danger"),
        _btn("◀️ Назад", callback_data="mailings", style="primary"),
    )
    return builder.as_markup()


def hidden_tag_keyboard(mailing_id: int, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if enabled:
        builder.row(_btn("🔴 Выключить", callback_data=f"toggle_hidden_tag:{mailing_id}", style="danger"))
    else:
        builder.row(_btn("✅ Включить", callback_data=f"toggle_hidden_tag:{mailing_id}", style="success"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
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


def delete_message_confirm_keyboard(mailing_id: int, message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, удалить", callback_data=f"delete_msg:{message_id}", style="danger"),
        _btn("◀️ Отмена", callback_data=f"mailing_messages:{mailing_id}", style="primary"),
    )
    return builder.as_markup()


def delete_target_confirm_keyboard(mailing_id: int, target_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, удалить", callback_data=f"delete_target:{target_id}", style="danger"),
        _btn("◀️ Отмена", callback_data=f"mailing_targets:{mailing_id}", style="primary"),
    )
    return builder.as_markup()


# === Mailing messages ===
def _msg_button_preview(msg: MailingMessage) -> str:
    if msg.is_forward:
        return f"[Переслано] {msg.forward_peer} #{msg.forward_msg_id}"
    if msg.is_dice:
        return f"[Эмодзи] {msg.text}"
    if msg.video_path:
        text = _strip_html(msg.text or "")
        preview = text[:25] + "..." if len(text) > 25 else text
        return f"[Видео] {preview}" if preview else "[Видео]"
    photo_count = len(msg.photo_paths)
    prefix = f"[{photo_count} Фото] " if photo_count > 1 else "[Фото] " if photo_count == 1 else ""
    text = _strip_html(msg.text or "")
    max_len = 25 if photo_count else 30
    preview = text[:max_len] + "..." if len(text) > max_len else text
    return f"{prefix}{preview}" if (prefix or preview) else "[Фото]"


def mailing_messages_keyboard(mailing_id: int, messages: list[MailingMessage], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(messages) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for msg in messages[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        preview = _msg_button_preview(msg)
        builder.row(_btn(f"🗑️ {preview}", callback_data=f"confirm_delete_msg:{msg.id}", style="danger"))
    _paginate_nav_row(builder, page, total_pages, f"mailing_messages_page:{mailing_id}")
    builder.row(
        _btn("➕ Текст/фото/видео/эмодзи", callback_data=f"add_mailing_message:{mailing_id}", style="primary"),
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
        return "⏱️ —"
    if secs >= 3600:
        return f"⏱️ {secs // 3600}ч"
    elif secs >= 60:
        return f"⏱️ {secs // 60}м"
    return f"⏱️ {secs}с"


TARGETS_PAGE_SIZE = 20  # keeps total inline buttons well under Telegram's ~100-button cap


def mailing_targets_keyboard(mailing_id: int, targets: list[MailingTarget], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Изменить КД", callback_data=f"change_all_target_interval:{mailing_id}", style="success"))
    total_pages = max(1, (len(targets) + TARGETS_PAGE_SIZE - 1) // TARGETS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_targets = targets[page * TARGETS_PAGE_SIZE:(page + 1) * TARGETS_PAGE_SIZE]
    for target in page_targets:
        iv_text = _format_target_interval(target)
        if target.is_forum or target.thread_id:
            thread_text = f"🧵#{target.thread_id}" if target.thread_id else "🧵"
            builder.row(
                _btn(f"🗑️ {target.chat_identifier}", callback_data=f"confirm_delete_target:{target.id}", style="danger"),
                _btn(iv_text, callback_data=f"edit_target_interval:{target.id}:{mailing_id}", style="primary"),
                InlineKeyboardButton(text=thread_text, callback_data=f"set_target_thread:{target.id}:{mailing_id}"),
            )
        else:
            builder.row(
                _btn(f"🗑️ {target.chat_identifier}", callback_data=f"confirm_delete_target:{target.id}", style="danger"),
                _btn(iv_text, callback_data=f"edit_target_interval:{target.id}:{mailing_id}", style="primary"),
            )
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀️", callback_data=f"mailing_targets_page:{mailing_id}:{page - 1}", style="primary"))
        nav.append(_btn(f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
        if page < total_pages - 1:
            nav.append(_btn("▶️", callback_data=f"mailing_targets_page:{mailing_id}:{page + 1}", style="primary"))
        builder.row(*nav)
    builder.row(
        _btn("➕ Добавить чат", callback_data=f"add_mailing_target:{mailing_id}", style="primary"),
        _btn("📁 Добавить папку", callback_data=f"add_folder_target:{mailing_id}", style="primary"),
    )
    builder.row(_btn("📄 Загрузить .txt", callback_data=f"add_txt_target:{mailing_id}", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


# === Mailing creation ===
def select_account_keyboard(accounts: list[Account], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(accounts) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for acc in accounts[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        builder.row(_btn(f"📱 {acc.display_name}", callback_data=f"select_account:{acc.id}", style="primary"))
    _paginate_nav_row(builder, page, total_pages, "select_account_page")
    builder.row(_btn("◀️ Назад", callback_data="mailings", style="primary"))
    return builder.as_markup()


def select_account_for_mailing_keyboard(accounts: list[Account], mailing_id: int, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(accounts) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for acc in accounts[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        builder.row(_btn(
            f"📱 {acc.display_name}",
            callback_data=f"set_mailing_account:{acc.id}:{mailing_id}",
            style="primary",
        ))
    _paginate_nav_row(builder, page, total_pages, f"set_mailing_account_page:{mailing_id}")
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def mailing_creation_messages_keyboard(mailing_id: int, messages: list[MailingMessage], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(messages) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for msg in messages[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        preview = _msg_button_preview(msg)
        builder.row(_btn(f"🗑️ {preview}", callback_data=f"create_delete_msg:{msg.id}", style="danger"))
    _paginate_nav_row(builder, page, total_pages, f"create_messages_page:{mailing_id}")
    builder.row(
        _btn("➕ Текст/фото/видео/эмодзи", callback_data=f"create_add_message:{mailing_id}", style="primary"),
        _btn("📨 Переслать", callback_data=f"create_add_forward:{mailing_id}", style="primary"),
    )
    if messages:
        builder.row(_btn("✅ Готово", callback_data=f"create_messages_done:{mailing_id}", style="success"))
    builder.row(_btn("◀️ Назад", callback_data=f"mailing:{mailing_id}", style="primary"))
    return builder.as_markup()


def mailing_creation_targets_keyboard(mailing_id: int, targets: list[MailingTarget], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(targets) + TARGETS_PAGE_SIZE - 1) // TARGETS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_targets = targets[page * TARGETS_PAGE_SIZE:(page + 1) * TARGETS_PAGE_SIZE]
    for target in page_targets:
        builder.row(_btn(f"🗑️ {target.chat_identifier}", callback_data=f"create_delete_target:{target.id}", style="danger"))
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀️", callback_data=f"create_targets_page:{mailing_id}:{page - 1}", style="primary"))
        nav.append(_btn(f"{page + 1}/{total_pages}", callback_data="noop", style="primary"))
        if page < total_pages - 1:
            nav.append(_btn("▶️", callback_data=f"create_targets_page:{mailing_id}:{page + 1}", style="primary"))
        builder.row(*nav)
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
    if not has_subscription:
        builder.row(_btn("🆓 Использовать бесплатно (с рекламой)", callback_data="activate_free_tier", style="primary"))
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def subscription_expired_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Продлить подписку", callback_data="subscription", style="success"))
    builder.row(_btn("🆓 Включить бесплатный тариф", callback_data="activate_free_tier", style="success"))
    return builder.as_markup()


def free_tier_info_keyboard(already_active: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not already_active:
        builder.row(_btn("✅ Активировать бесплатный тариф", callback_data="activate_free_tier_confirm", style="success"))
    builder.row(_btn("◀️ Назад", callback_data="subscription", style="primary"))
    return builder.as_markup()


def subscription_plan_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("📅 1 день", callback_data="sub_plan:1", style="primary"),
        _btn("📅 7 дней", callback_data="sub_plan:7", style="primary"),
        _btn("📅 30 дней", callback_data="sub_plan:30", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="subscription", style="primary"))
    return builder.as_markup()


def payment_keyboard(pay_url: str, invoice_id: str, plan_days: int, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить", url=pay_url, style="success"))
    if support_username:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_payment:{invoice_id}", style="primary"),
            _btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"),
        )
        builder.row(_btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"))
    else:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_payment:{invoice_id}", style="primary"),
            _btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"),
        )
    return builder.as_markup()


def payment_method_keyboard(show_platega: bool = False, show_ton: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_ton:
        builder.row(
            _btn("💎 CryptoBot (USDT)", callback_data="pay_cryptobot", style="primary"),
            _btn("💠 GRAM(TON)", callback_data="pay_ton", style="primary"),
        )
    else:
        builder.row(_btn("💎 CryptoBot (USDT)", callback_data="pay_cryptobot", style="primary"))
    builder.row(_btn("⭐️ Telegram Stars", callback_data="pay_stars", style="primary"))
    if show_platega:
        builder.row(_btn("🇷🇺 Оплата рублями (СБП)", callback_data="pay_platega", style="primary"))
    builder.row(_btn("🇺🇦 На карту(грн)", callback_data="pay_card", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="buy_subscription", style="primary"))
    return builder.as_markup()


def platega_payment_keyboard(pay_url: str, order_id: str, plan_days: int, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💳 Оплатить через СБП", url=pay_url, style="success"))
    if support_username:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_platega:{order_id}", style="primary"),
            _btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"),
        )
        builder.row(_btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"))
    else:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_platega:{order_id}", style="primary"),
            _btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"),
        )
    return builder.as_markup()


def ton_payment_keyboard(pay_url: str, comment: str, plan_days: int, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💠 Оплатить через Tonkeeper", url=pay_url, style="success"))
    if support_username:
        builder.row(
            _btn("🔄 Проверить оплату", callback_data=f"check_ton_payment:{comment}", style="primary"),
            _btn("🆘 Поддержка", url=f"https://t.me/{support_username.lstrip('@')}", style="danger"),
        )
        builder.row(_btn("◀️ Назад", callback_data=f"sub_plan:{plan_days}", style="primary"))
    else:
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
def channel_check_keyboard(channels: list[RequiredChannel], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(channels) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for ch in channels[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        url = f"https://t.me/{ch.channel_username}" if ch.channel_username else None
        if url:
            builder.row(_btn(f"📢 {ch.channel_title}", url=url, style="primary"))
    _paginate_nav_row(builder, page, total_pages, "required_channels_page")
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
        _btn("📢 Рассылка всем", callback_data="admin_broadcast", style="primary"),
        InlineKeyboardButton(text="📡 Обяз. каналы", callback_data="admin_channels", style="primary"),
    )
    builder.row(
        _btn("⚙️ Настройки", callback_data="admin_settings", style="primary"),
        _btn("💸 Запросы вывода", callback_data="admin_withdrawals", style="primary"),
    )
    builder.row(
        _btn("🆓 Бесплатный тариф", callback_data="admin_free_tier", style="primary"),
        _btn("🌐 Пул прокси", callback_data="admin_proxy_pool", style="primary"),
    )
    builder.row(
        _btn("🔑 Пул API ID", callback_data="admin_api_pool", style="primary"),
    )
    builder.row(
        _btn("🔍 Диагностика", callback_data="admin_diagnostics", style="primary"),
        _btn("🗑 Мёртвые аккаунты", callback_data="admin_cleanup_accounts", style="primary"),
    )
    builder.row(
        _btn("💳 Статистика Platega", callback_data="admin_platega", style="primary"),
        _btn("📋 Подписки", callback_data="admin_subscriptions:0", style="primary"),
    )
    builder.row(
        _btn("📤 Экспорт БД", callback_data="admin_export_db", style="primary"),
        _btn("📥 Импорт БД", callback_data="admin_import_db", style="primary"),
    )
    builder.row(_btn("◀️ Главное меню", callback_data="main_menu", style="primary"))
    return builder.as_markup()


def admin_free_tier_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("💬 Чаты", callback_data="admin_free_chats:0", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_free_chats_keyboard(page: int, has_next: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    navigation = []
    if page > 0:
        navigation.append(
            _btn("⬅️ Назад", callback_data=f"admin_free_chats:{page - 1}", style="primary")
        )
    if has_next:
        navigation.append(
            _btn("➡️ Далее", callback_data=f"admin_free_chats:{page + 1}", style="primary")
        )
    if navigation:
        builder.row(*navigation)
    builder.row(_btn("◀️ К статистике", callback_data="admin_free_tier", style="primary"))
    return builder.as_markup()


def admin_sub_stats_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💎 CryptoBot", callback_data="admin_sub_method:cryptobot", style="primary"),
        _btn("💠 GRAM(TON)", callback_data="admin_sub_method:ton", style="primary"),
    )
    builder.row(
        _btn("🇷🇺 Platega (СБП)", callback_data="admin_sub_method:platega", style="primary"),
        _btn("⭐️ Telegram Stars", callback_data="admin_sub_method:stars", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_sub_method_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="admin_sub_stats", style="primary"))
    return builder.as_markup()


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("💰 Цена 1 день", callback_data="admin_set_price_1d", style="primary"),
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


def admin_channels_keyboard(channels: list[RequiredChannel], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(channels) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for ch in channels[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        builder.row(_btn(
            f"🗑️ {ch.channel_title}",
            callback_data=f"admin_del_channel:{ch.channel_id}",
            style="danger",
        ))
    _paginate_nav_row(builder, page, total_pages, "admin_channels_page")
    builder.row(
        _btn("➕ Добавить канал", callback_data="admin_add_channel", style="success"),
        _btn("◀️ Назад", callback_data="admin_back", style="primary"),
    )
    return builder.as_markup()


def admin_withdrawals_keyboard(requests, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(requests) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for req in requests[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
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
    _paginate_nav_row(builder, page, total_pages, "admin_withdrawals_page")
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def promo_subscription_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("✅ Да, платная подписка", callback_data="promo_is_sub:1", style="success"),
        _btn("❌ Нет, обычный промокод", callback_data="promo_is_sub:0", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="cancel", style="primary"))
    return builder.as_markup()


def admin_promocodes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn("➕ Создать промокод", callback_data="admin_create_promo", style="success"),
        _btn("📋 Список промокодов", callback_data="admin_list_promos", style="primary"),
    )
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_promo_list_keyboard(promocodes: list[Promocode], page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(promocodes) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for promo in promocodes[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "✅" if promo.uses_count >= promo.max_uses else "🟢"
        builder.row(
            _btn(
                f"{status} {promo.code} ({promo.duration_days}д) [{promo.uses_count}/{promo.max_uses}]",
                callback_data=f"admin_promo_info:{promo.id}",
                style="primary",
            ),
            _btn("✏️", callback_data=f"admin_edit_promo_uses:{promo.id}", style="primary"),
            _btn("🗑️", callback_data=f"admin_delete_promo:{promo.id}", style="danger"),
        )
    _paginate_nav_row(builder, page, total_pages, "admin_promo_page")
    builder.row(_btn("◀️ Назад", callback_data="admin_promocodes", style="primary"))
    return builder.as_markup()


def admin_proxy_pool_keyboard(proxies: list, page: int = 0) -> InlineKeyboardMarkup:
    """proxies: list of (PoolProxy, account_count) tuples."""
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(proxies) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for pool_proxy, count in proxies[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "🟢" if pool_proxy.is_active else "💀"
        host = pool_proxy.proxy.split("@")[-1] if "@" in pool_proxy.proxy else pool_proxy.proxy.replace("socks5://", "")
        builder.row(
            _btn(f"{status} {host} [{count}]", callback_data=f"admin_proxy_info:{pool_proxy.id}", style="primary"),
            _btn("🗑️", callback_data=f"admin_delete_proxy:{pool_proxy.id}", style="danger"),
        )
    _paginate_nav_row(builder, page, total_pages, "admin_proxy_pool_page")
    builder.row(_btn("➕ Добавить прокси", callback_data="admin_add_proxy", style="success"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_api_pool_keyboard(credentials: list, page: int = 0) -> InlineKeyboardMarkup:
    """credentials: list of (PoolApiCredential, account_count) tuples."""
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(credentials) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    for cred, count in credentials[page * LIST_PAGE_SIZE:(page + 1) * LIST_PAGE_SIZE]:
        status = "🟢" if cred.is_active else "⚫️"
        builder.row(
            _btn(f"{status} {cred.api_id} [{count}]", callback_data=f"admin_api_info:{cred.id}", style="primary"),
            _btn("🗑️", callback_data=f"admin_delete_api:{cred.id}", style="danger"),
        )
    _paginate_nav_row(builder, page, total_pages, "admin_api_pool_page")
    builder.row(_btn("➕ Добавить api_id", callback_data="admin_add_api", style="success"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ Назад", callback_data="cancel", style="primary"))
    return builder.as_markup()


def admin_diagnostics_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("📋 Последние ошибки", callback_data=f"admin_user_errors:{telegram_id}", style="primary"))
    builder.row(_btn("◀️ Назад", callback_data="admin_back", style="primary"))
    return builder.as_markup()


def admin_errors_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("◀️ К профилю", callback_data=f"admin_diag_show:{telegram_id}", style="primary"))
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
