import json
import re
import os
import uuid
import logging
import pytz
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

_KYIV_TZ = pytz.timezone('Europe/Kiev')


def _fmt_dt(dt) -> str:
    """Format datetime in Kyiv timezone."""
    if dt is None:
        return "никогда"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.utc)
    return dt.astimezone(_KYIV_TZ).strftime("%d.%m.%Y %H:%M")

from ..database.db import Database
from ..keyboards.inline import (
    mailings_keyboard,
    mailing_menu_keyboard,
    mailing_messages_keyboard,
    mailing_targets_keyboard,
    select_account_keyboard,
    mailing_creation_messages_keyboard,
    mailing_creation_targets_keyboard,
    active_hours_keyboard,
    launch_mailing_keyboard,
    delete_mailing_confirm_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    photo_collection_keyboard,
    parse_mode_keyboard,
    select_account_for_mailing_keyboard,
)
from ..utils.time_utils import format_active_hours, parse_time_range, create_active_hours_json
from ..services import MailingService
from ..userbot.manager import UserbotManager
from ..utils.premium_emoji import pe

logger = logging.getLogger(__name__)

router = Router()

PHOTOS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "mailing_photos")


async def save_photo_from_message(message: Message) -> str | None:
    """Download photo from message and save to disk. Returns file path."""
    if not message.photo:
        return None
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    photo = message.photo[-1]  # largest size
    file_name = f"{uuid.uuid4().hex}.jpg"
    file_path = os.path.join(PHOTOS_DIR, file_name)
    await message.bot.download(photo, destination=file_path)
    return file_path


def _strip_html(text: str) -> str:
    """Remove HTML tags from text for plain display."""
    import html as _html
    clean = re.sub(r'<[^>]+>', '', text)
    return _html.unescape(clean)


def serialize_entities(entities) -> str | None:
    """Serialize aiogram message entities to JSON for storage."""
    if not entities:
        return None
    result = []
    for e in entities:
        d = {"type": e.type, "offset": e.offset, "length": e.length}
        if e.type == "custom_emoji":
            d["custom_emoji_id"] = e.custom_emoji_id
        elif e.type == "text_link":
            d["url"] = e.url
        elif e.type == "pre":
            d["language"] = getattr(e, "language", "") or ""
        result.append(d)
    return json.dumps(result, ensure_ascii=False) if result else None


def message_preview(msg) -> str:
    """Generate preview text for a mailing message."""
    if msg.is_forward:
        return f"[Переслано] из {msg.forward_peer} #{msg.forward_msg_id}"
    photo_count = len(msg.photo_paths)
    if photo_count > 1:
        prefix = f"[{photo_count} Фото] "
    elif photo_count == 1:
        prefix = "[Фото] "
    else:
        prefix = ""
    raw = _strip_html(msg.text or "")
    preview = raw[:40] + "..." if len(raw) > 40 else raw
    return f"{prefix}{preview}" if (prefix or preview) else "[Фото]"


def parse_chat_link(text: str) -> str | None:
    """Extract chat identifier from a t.me link. Returns @username or None."""
    text = text.strip()
    # Match t.me/username or t.me/+invite links
    m = re.match(r'(?:https?://)?t\.me/\+?([\w]+)', text)
    if m:
        username = m.group(1)
        # Skip special paths
        if username.lower() in ('addlist', 'joinchat', 'proxy', 'socks'):
            return None
        return f"@{username}"
    return None


def parse_folder_slug(text: str) -> str | None:
    """Extract folder slug from a t.me/addlist/... link."""
    text = text.strip()
    m = re.match(r'(?:https?://)?t\.me/addlist/([\w-]+)', text)
    if m:
        return m.group(1)
    return None


class CreateMailingStates(StatesGroup):
    waiting_name = State()
    waiting_account = State()
    waiting_interval = State()
    adding_messages = State()
    waiting_message_text = State()
    waiting_forward_message = State()
    adding_targets = State()
    waiting_target = State()
    waiting_folder = State()
    waiting_hours = State()


class EditMailingStates(StatesGroup):
    waiting_message_text = State()
    waiting_forward_message = State()
    waiting_target = State()
    waiting_folder = State()
    waiting_hours = State()
    waiting_target_interval = State()


@router.callback_query(F.data.startswith("account_mailings:"))
async def callback_account_mailings(callback: CallbackQuery, db: Database):
    """Show mailings for a specific account."""
    account_id = int(callback.data.split(":")[1])
    user = await db.get_user(callback.from_user.id)
    all_mailings = await db.get_user_mailings(user.id)
    mailings = [m for m in all_mailings if m.account_id == account_id]

    account = await db.get_account(account_id)
    name = account.display_name if account else "аккаунт"

    text = f"📋 Рассылки аккаунта {name}:\n\n"
    if mailings:
        for m in mailings:
            status = "🟢 Активна" if m.is_active else "🔴 Остановлена"
            text += f"• {m.name} - {status}\n"
    else:
        text += "Рассылок для этого аккаунта нет.\n"

    text += "\nВыберите рассылку или создайте новую:"
    await callback.message.edit_text(pe(text), parse_mode="HTML", reply_markup=mailings_keyboard(mailings))
    await callback.answer()


@router.callback_query(F.data == "mailings")
async def callback_mailings(callback: CallbackQuery, db: Database):
    user = await db.get_user(callback.from_user.id)
    mailings = await db.get_user_mailings(user.id)

    text = "📋 Ваши рассылки:\n\n"
    if mailings:
        for m in mailings:
            status = "🟢 Активна" if m.is_active else "🔴 Остановлена"
            text += f"• {m.name} - {status}\n"
    else:
        text += "У вас пока нет рассылок.\n"

    text += "\nВыберите рассылку или создайте новую:"

    await callback.message.edit_text(pe(text), parse_mode="HTML", reply_markup=mailings_keyboard(mailings))
    await callback.answer()


@router.callback_query(F.data.startswith("mailing:"))
async def callback_mailing_menu(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)

    if not mailing:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    account = await db.get_account(mailing.account_id)
    messages = await db.get_mailing_messages(mailing_id)
    targets = await db.get_mailing_targets(mailing_id)

    status = "🟢 Активна" if mailing.is_active else "🔴 Остановлена"
    last_sent = _fmt_dt(mailing.last_sent_at)
    active_hours = format_active_hours(mailing.active_hours_json)

    text = pe(
        f"📋 Рассылка: {mailing.name}\n\n"
        f"Статус: {status}\n"
        f"Аккаунт: {account.phone if account else 'не найден'}\n"
        f"Интервал: {mailing.interval_seconds} сек\n"
        f"Время активности: {active_hours}\n"
        f"Сообщений: {len(messages)}\n"
        f"Целевых чатов: {len(targets)}\n"
        f"Последняя отправка: {last_sent}\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mailing_menu_keyboard(mailing))
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_mailing:"))
async def callback_toggle_mailing(
    callback: CallbackQuery, db: Database, mailing_service: MailingService
):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)

    if not mailing:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    if mailing.is_active:
        await mailing_service.stop_mailing(mailing_id)
        await callback.answer("🔴 Рассылка остановлена")
    else:
        success = await mailing_service.start_mailing(mailing_id)
        if success:
            await callback.answer("🟢 Рассылка запущена")
        else:
            await callback.answer(
                "❌ Не удалось запустить рассылку. Проверьте аккаунт и настройки.",
                show_alert=True,
            )
            return

    mailing = await db.get_mailing(mailing_id)
    account = await db.get_account(mailing.account_id)
    messages = await db.get_mailing_messages(mailing_id)
    targets = await db.get_mailing_targets(mailing_id)

    status = "🟢 Активна" if mailing.is_active else "🔴 Остановлена"
    last_sent = _fmt_dt(mailing.last_sent_at)
    active_hours = format_active_hours(mailing.active_hours_json)

    text = pe(
        f"📋 Рассылка: {mailing.name}\n\n"
        f"Статус: {status}\n"
        f"Аккаунт: {account.phone if account else 'не найден'}\n"
        f"Интервал: {mailing.interval_seconds} сек\n"
        f"Время активности: {active_hours}\n"
        f"Сообщений: {len(messages)}\n"
        f"Целевых чатов: {len(targets)}\n"
        f"Последняя отправка: {last_sent}\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mailing_menu_keyboard(mailing))


# === Mailing Messages ===
@router.callback_query(F.data.startswith("mailing_messages:"))
async def callback_mailing_messages(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    messages = await db.get_mailing_messages(mailing_id)

    text = f"📝 Сообщения рассылки ({len(messages)} шт.):\n\n"
    if messages:
        for i, msg in enumerate(messages, 1):
            text += f"{i}. {message_preview(msg)}\n"
    else:
        text += "Сообщений пока нет.\n"

    text += "\nНажмите на сообщение, чтобы удалить его:"

    await callback.message.edit_text(
        pe(text), parse_mode="HTML", reply_markup=mailing_messages_keyboard(mailing_id, messages)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_mailing_message:"))
async def callback_add_mailing_message(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])

    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_message_text)

    await callback.message.edit_text(
        pe("✏️ Отправьте текст или фото для рассылки.\n"
        "Можно отправить несколько фото (до 10) — они будут отправлены альбомом."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_mailing_forward:"))
async def callback_add_mailing_forward(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_forward_message)
    await callback.message.edit_text(
        pe("📨 Перешлите любое сообщение из канала или группы.\n"
        "Бот збереже посилання на оригінал і при розсилці буде пересилати його."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_forward_message)
async def process_edit_forward_message(message: Message, state: FSMContext, db: Database):
    from aiogram.types import MessageOriginChannel, MessageOriginChat
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        peer = f"@{origin.chat.username}" if origin.chat.username else str(origin.chat.id)
        msg_id = origin.message_id
    elif isinstance(origin, MessageOriginChat):
        peer = f"@{origin.sender_chat.username}" if origin.sender_chat.username else str(origin.sender_chat.id)
        msg_id = origin.message_id
    else:
        await message.answer(
            "❌ Не удалось определить источник. Перешлите сообщение из канала или группы."
        )
        return

    data = await state.get_data()
    mailing_id = data["mailing_id"]
    await db.add_mailing_forward(mailing_id, peer, msg_id)
    await state.clear()

    messages = await db.get_mailing_messages(mailing_id)
    await message.answer(
        pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
        f"Всего записей: {len(messages)}"),
        parse_mode="HTML",
        reply_markup=mailing_messages_keyboard(mailing_id, messages),
    )


@router.message(EditMailingStates.waiting_message_text, F.photo)
async def process_edit_message_photo(
    message: Message, state: FSMContext, db: Database, album: list[Message] = None
):
    data = await state.get_data()
    mailing_id = data["mailing_id"]
    pending_photos = data.get("pending_photos", [])

    messages_to_process = album or [message]
    caption = data.get("pending_caption")
    caption_entities_json = data.get("pending_caption_entities")

    for msg in messages_to_process:
        if len(pending_photos) >= 10:
            break
        photo_path = await save_photo_from_message(msg)
        if photo_path:
            pending_photos.append(photo_path)
        if caption is None and msg.caption:
            caption = (msg.caption or "").strip()
            caption_entities_json = serialize_entities(msg.caption_entities)

    await state.update_data(pending_photos=pending_photos, pending_caption=caption,
                            pending_caption_entities=caption_entities_json)

    if len(pending_photos) >= 10:
        await message.answer(
            pe(f"📸 Добавлено {len(pending_photos)} фото (максимум).\n"
            "Нажмите «Сохранить» для завершения."),
            parse_mode="HTML",
            reply_markup=photo_collection_keyboard(mailing_id, len(pending_photos), is_create=False),
        )
    else:
        await message.answer(
            pe(f"📸 Фото добавлено ({len(pending_photos)}/10).\n"
            "Отправьте ещё фото или нажмите «Сохранить»."),
            parse_mode="HTML",
            reply_markup=photo_collection_keyboard(mailing_id, len(pending_photos), is_create=False),
        )


@router.message(EditMailingStates.waiting_message_text)
async def process_edit_message_text(message: Message, state: FSMContext, db: Database):
    text = (message.text or "").strip()
    if not text:
        await message.answer(pe("❌ Отправьте текст или фото."), parse_mode="HTML")
        return
    entities_json = serialize_entities(message.entities)
    data = await state.get_data()
    mailing_id = data["mailing_id"]
    pending_photos = data.get("pending_photos", [])

    if pending_photos:
        await db.add_mailing_message(mailing_id, text, photo_paths=pending_photos,
                                     entities_json=entities_json)
        await state.clear()
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Сообщение с {len(pending_photos)} фото добавлено! Всего сообщений: {len(messages)}"),
            parse_mode="HTML",
            reply_markup=mailing_messages_keyboard(mailing_id, messages),
        )
    else:
        await db.add_mailing_message(mailing_id, text, entities_json=entities_json)
        await state.clear()
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Текст добавлен! Всего сообщений: {len(messages)}"),
            parse_mode="HTML",
            reply_markup=mailing_messages_keyboard(mailing_id, messages),
        )


@router.callback_query(F.data.startswith("edit_save_photos:"))
async def callback_edit_save_photos(callback: CallbackQuery, state: FSMContext, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    pending_photos = data.get("pending_photos", [])

    if not pending_photos:
        await callback.answer("Нет фото для сохранения", show_alert=True)
        return

    caption = data.get("pending_caption") or ""
    entities_json = data.get("pending_caption_entities")
    await db.add_mailing_message(mailing_id, caption, photo_paths=pending_photos,
                                 entities_json=entities_json)
    await state.clear()

    messages = await db.get_mailing_messages(mailing_id)
    await callback.message.edit_text(
        pe(f"✅ Сообщение с {len(pending_photos)} фото добавлено! Всего сообщений: {len(messages)}"),
        parse_mode="HTML",
        reply_markup=mailing_messages_keyboard(mailing_id, messages),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_msg:"))
async def callback_delete_message(callback: CallbackQuery, db: Database):
    message_id = int(callback.data.split(":")[1])

    async with db._conn.execute(
        "SELECT mailing_id FROM mailing_messages WHERE id = ?", (message_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            await callback.answer("Текст не найден", show_alert=True)
            return
        mailing_id = row["mailing_id"]

    await db.delete_mailing_message(message_id)
    messages = await db.get_mailing_messages(mailing_id)

    await callback.answer("Сообщение удалено")

    text = f"📝 Сообщения рассылки ({len(messages)} шт.):\n\n"
    if messages:
        for i, msg in enumerate(messages, 1):
            text += f"{i}. {message_preview(msg)}\n"
    else:
        text += "Сообщений пока нет.\n"

    await callback.message.edit_text(
        pe(text), parse_mode="HTML", reply_markup=mailing_messages_keyboard(mailing_id, messages)
    )


# === Mailing Targets ===
@router.callback_query(F.data.startswith("mailing_targets:"))
async def callback_mailing_targets(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    targets = await db.get_mailing_targets(mailing_id)

    text = f"🎯 Целевые чаты ({len(targets)} шт.):\n\n"
    if targets:
        for i, target in enumerate(targets, 1):
            text += f"{i}. {target.chat_identifier}\n"
    else:
        text += "Целевых чатов пока нет.\n"

    text += "\nНажмите на чат, чтобы удалить его:"

    await callback.message.edit_text(
        pe(text), parse_mode="HTML", reply_markup=mailing_targets_keyboard(mailing_id, targets)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_mailing_target:"))
async def callback_add_mailing_target(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])

    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_target)

    await callback.message.edit_text(
        pe("🎯 Введите username, ID или ссылку на чат/группу:\n\n"
        "Примеры:\n"
        "• @username\n"
        "• -1001234567890\n"
        "• https://t.me/chatname"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_target)
async def process_edit_target(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    # Try to parse as t.me link
    parsed = parse_chat_link(text)
    target = parsed if parsed else text

    await db.add_mailing_target(mailing_id, target)
    await state.clear()

    targets = await db.get_mailing_targets(mailing_id)

    await message.answer(
        pe(f"✅ Чат добавлен! Всего чатов: {len(targets)}"),
        parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets),
    )


@router.callback_query(F.data.startswith("delete_target:"))
async def callback_delete_target(callback: CallbackQuery, db: Database):
    target_id = int(callback.data.split(":")[1])

    async with db._conn.execute(
        "SELECT mailing_id FROM mailing_targets WHERE id = ?", (target_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            await callback.answer("Чат не найден", show_alert=True)
            return
        mailing_id = row["mailing_id"]

    await db.delete_mailing_target(target_id)
    targets = await db.get_mailing_targets(mailing_id)

    await callback.answer("Чат удалён")

    text = f"🎯 Целевые чаты ({len(targets)} шт.):\n\n"
    if targets:
        for i, target in enumerate(targets, 1):
            text += f"{i}. {target.chat_identifier}\n"
    else:
        text += "Целевых чатов пока нет.\n"

    await callback.message.edit_text(
        pe(text), parse_mode="HTML", reply_markup=mailing_targets_keyboard(mailing_id, targets)
    )


# === Per-target interval (edit mode) ===
@router.callback_query(F.data.startswith("edit_target_interval:"))
async def callback_edit_target_interval(callback: CallbackQuery, state: FSMContext, db: Database):
    parts = callback.data.split(":")
    target_id = int(parts[1])
    mailing_id = int(parts[2])

    targets = await db.get_mailing_targets(mailing_id)
    target = next((t for t in targets if t.id == target_id), None)
    current = target.interval_seconds if target else None
    current_str = f"{current} сек" if current else "по умолчанию (общий интервал рассылки)"

    await state.update_data(target_id=target_id, mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_target_interval)

    await callback.message.edit_text(
        pe(f"⏱️ Интервал для чата: {target.chat_identifier if target else ''}\n\n"
        f"Текущий: {current_str}\n\n"
        "Введите интервал в секундах (минимум 30).\n"
        "Отправьте 0 — использовать общий интервал рассылки."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_target_interval)
async def process_target_interval(message: Message, state: FSMContext, db: Database):
    try:
        interval = int(message.text.strip())
        if interval != 0 and interval < 30:
            await message.answer(pe("❌ Минимальный интервал — 30 секунд (или 0 для использования общего интервала)"), parse_mode="HTML")
            return
    except ValueError:
        await message.answer(pe("❌ Введите число (секунды) или 0"), parse_mode="HTML")
        return

    data = await state.get_data()
    target_id = data["target_id"]
    mailing_id = data["mailing_id"]

    await db.update_target_interval(target_id, interval if interval > 0 else None)
    await state.clear()

    targets = await db.get_mailing_targets(mailing_id)
    text = f"✅ Интервал обновлён!\n\n🎯 Целевые чаты ({len(targets)} шт.):\n\n"
    for i, t in enumerate(targets, 1):
        iv = f" [{t.interval_seconds}с]" if t.interval_seconds else " [умолч.]"
        text += f"{i}. {t.chat_identifier}{iv}\n"

    await message.answer(pe(text), parse_mode="HTML", reply_markup=mailing_targets_keyboard(mailing_id, targets))


# === Folder targets (edit mode) ===
@router.callback_query(F.data.startswith("add_folder_target:"))
async def callback_add_folder_target(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])

    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_folder)

    await callback.message.edit_text(
        pe("📁 Отправьте ссылку на папку чатов:\n\n"
        "Пример:\n"
        "• https://t.me/addlist/xxxxx"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_folder)
async def process_edit_folder(
    message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager
):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    slug = parse_folder_slug(text)
    if not slug:
        await message.answer(
            "❌ Неверная ссылка. Отправьте ссылку в формате:\n"
            "https://t.me/addlist/xxxxx"
        )
        return

    # Get account for this mailing to use Telethon
    mailing = await db.get_mailing(mailing_id)
    if not mailing:
        await message.answer(pe("❌ Рассылка не найдена"), parse_mode="HTML")
        await state.clear()
        return

    client = await userbot_manager.get_client(mailing.account_id)
    if not client:
        await message.answer(pe("❌ Аккаунт не подключён. Проверьте аккаунт."), parse_mode="HTML")
        await state.clear()
        return

    try:
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
        result = await client(CheckChatlistInviteRequest(slug=slug))

        chats = getattr(result, 'chats', [])
        if not chats:
            await message.answer(pe("❌ Папка пуста или не удалось получить чаты."), parse_mode="HTML")
            return

        added = 0
        for entity in chats:
            try:
                if hasattr(entity, 'username') and entity.username:
                    identifier = f"@{entity.username}"
                else:
                    chat_id = int(f"-100{entity.id}")
                    identifier = str(chat_id)
                await db.add_mailing_target(mailing_id, identifier)
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add chat from folder: {e}")
                continue

        await state.clear()
        targets = await db.get_mailing_targets(mailing_id)

        await message.answer(
            pe(f"✅ Добавлено {added} чатов из папки! Всего чатов: {len(targets)}"),
            parse_mode="HTML",
            reply_markup=mailing_targets_keyboard(mailing_id, targets),
        )

    except Exception as e:
        logger.error(f"Error resolving folder {slug}: {e}")
        await message.answer(
            pe(f"❌ Ошибка при получении чатов из папки: {e}"),
            parse_mode="HTML",
        )


# === Active Hours ===
@router.callback_query(F.data.startswith("mailing_hours:"))
async def callback_mailing_hours(callback: CallbackQuery, db: Database, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)

    current_hours = format_active_hours(mailing.active_hours_json)

    await state.update_data(mailing_id=mailing_id, edit_mode=True)
    await state.set_state(EditMailingStates.waiting_hours)

    await callback.message.edit_text(
        pe(f"⏰ Время активности\n\n"
        f"Текущие настройки: {current_hours}\n\n"
        "Введите диапазон времени в формате:\n"
        "10:00-13:00\n\n"
        "Можно указать несколько диапазонов через запятую:\n"
        "10:00-13:00, 18:00-22:00\n\n"
        "Или отправьте 'сброс' для работы 24/7"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_hours)
async def process_edit_hours(message: Message, state: FSMContext, db: Database):
    text = message.text.strip().lower()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    if text in ("сброс", "reset", "24/7"):
        await db.update_mailing_active_hours(mailing_id, None)
        await state.clear()
        await message.answer(
            pe("✅ Время активности сброшено (24/7)"),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    ranges = []
    for part in text.split(","):
        part = part.strip()
        parsed = parse_time_range(part)
        if parsed:
            ranges.append(parsed)

    if not ranges:
        await message.answer(
            "❌ Неверный формат. Используйте формат: 10:00-13:00\n"
            "Или несколько диапазонов: 10:00-13:00, 18:00-22:00"
        )
        return

    active_hours_json = create_active_hours_json(ranges)
    await db.update_mailing_active_hours(mailing_id, active_hours_json)
    await state.clear()

    await message.answer(
        pe(f"✅ Время активности обновлено: {format_active_hours(active_hours_json)}"),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


# === Delete Mailing ===
@router.callback_query(F.data.startswith("delete_mailing:"))
async def callback_delete_mailing(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)

    if not mailing:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        pe(f"❓ Вы уверены, что хотите удалить рассылку «{mailing.name}»?"),
        parse_mode="HTML",
        reply_markup=delete_mailing_confirm_keyboard(mailing_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_mailing:"))
async def callback_confirm_delete_mailing(
    callback: CallbackQuery, db: Database, mailing_service: MailingService
):
    mailing_id = int(callback.data.split(":")[1])

    await mailing_service.delete_mailing(mailing_id)

    await callback.message.edit_text(
        pe("✅ Рассылка удалена"),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# === Create Mailing ===
@router.callback_query(F.data == "create_mailing")
async def callback_create_mailing(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateMailingStates.waiting_name)

    await callback.message.edit_text(
        pe("➕ Создание рассылки\n\n"
        "Шаг 1/6: Введите название рассылки:"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_name)
async def process_mailing_name(message: Message, state: FSMContext, db: Database):
    name = message.text.strip()
    await state.update_data(name=name)

    user = await db.get_user(message.from_user.id)
    accounts = await db.get_user_accounts(user.id)

    if not accounts:
        await message.answer(
            "❌ У вас нет добавленных аккаунтов.\n"
            "Сначала добавьте аккаунт в разделе «Аккаунты».",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    await state.set_state(CreateMailingStates.waiting_account)

    await message.answer(
        "Шаг 2/6: Выберите аккаунт для рассылки:",
        reply_markup=select_account_keyboard(accounts),
    )


@router.callback_query(
    CreateMailingStates.waiting_account, F.data.startswith("select_account:")
)
async def process_select_account(callback: CallbackQuery, state: FSMContext):
    account_id = int(callback.data.split(":")[1])
    await state.update_data(account_id=account_id)
    await state.set_state(CreateMailingStates.waiting_interval)

    await callback.message.edit_text(
        "Шаг 3/6: Введите интервал между сообщениями (в секундах):\n\n"
        "Например: 300 (это 5 минут)",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_interval)
async def process_mailing_interval(message: Message, state: FSMContext, db: Database):
    try:
        interval = int(message.text.strip())
        if interval < 30:
            await message.answer(pe("❌ Минимальный интервал - 30 секунд"), parse_mode="HTML")
            return
    except ValueError:
        await message.answer(pe("❌ Введите число (секунды)"), parse_mode="HTML")
        return

    data = await state.get_data()
    user = await db.get_user(message.from_user.id)

    mailing_id = await db.create_mailing(
        user_id=user.id,
        account_id=data["account_id"],
        name=data["name"],
        interval_seconds=interval,
    )

    await state.update_data(mailing_id=mailing_id)
    await state.set_state(CreateMailingStates.adding_messages)

    messages = await db.get_mailing_messages(mailing_id)

    await message.answer(
        "Шаг 4/6: Добавьте сообщения для рассылки\n\n"
        "Вы можете добавить текст, фото или фото с подписью.\n"
        "Несколько сообщений — для рандомизации.\n"
        "Минимум 1 сообщение обязательно.",
        reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
    )


@router.callback_query(
    CreateMailingStates.adding_messages, F.data.startswith("create_add_message:")
)
async def callback_create_add_message(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateMailingStates.waiting_message_text)

    await callback.message.edit_text(
        pe("✏️ Отправьте текст или фото для рассылки.\n"
        "Можно отправить несколько фото (до 10) — они будут отправлены альбомом."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    CreateMailingStates.adding_messages, F.data.startswith("create_add_forward:")
)
async def callback_create_add_forward(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateMailingStates.waiting_forward_message)
    await callback.message.edit_text(
        pe("📨 Перешлите любое сообщение из канала или группы.\n"
        "Бот збереже посилання на оригінал і при розсилці буде пересилати його."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_forward_message)
async def process_create_forward_message(message: Message, state: FSMContext, db: Database):
    from aiogram.types import MessageOriginChannel, MessageOriginChat
    origin = message.forward_origin
    if isinstance(origin, MessageOriginChannel):
        peer = f"@{origin.chat.username}" if origin.chat.username else str(origin.chat.id)
        msg_id = origin.message_id
    elif isinstance(origin, MessageOriginChat):
        peer = f"@{origin.sender_chat.username}" if origin.sender_chat.username else str(origin.sender_chat.id)
        msg_id = origin.message_id
    else:
        await message.answer(
            "❌ Не удалось определить источник. Перешлите сообщение из канала или группы."
        )
        return

    data = await state.get_data()
    mailing_id = data["mailing_id"]
    await db.add_mailing_forward(mailing_id, peer, msg_id)
    await state.set_state(CreateMailingStates.adding_messages)

    messages = await db.get_mailing_messages(mailing_id)
    await message.answer(
        pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
        f"Всего записей: {len(messages)}\n\nДобавьте ещё или нажмите «Готово»:"),
        parse_mode="HTML",
        reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
    )


@router.message(CreateMailingStates.waiting_message_text, F.photo)
async def process_create_message_photo(
    message: Message, state: FSMContext, db: Database, album: list[Message] = None
):
    data = await state.get_data()
    mailing_id = data["mailing_id"]
    pending_photos = data.get("pending_photos", [])

    messages_to_process = album or [message]
    caption = data.get("pending_caption")
    caption_entities_json = data.get("pending_caption_entities")

    for msg in messages_to_process:
        if len(pending_photos) >= 10:
            break
        photo_path = await save_photo_from_message(msg)
        if photo_path:
            pending_photos.append(photo_path)
        if caption is None and msg.caption:
            caption = (msg.caption or "").strip()
            caption_entities_json = serialize_entities(msg.caption_entities)

    await state.update_data(pending_photos=pending_photos, pending_caption=caption,
                            pending_caption_entities=caption_entities_json)

    if len(pending_photos) >= 10:
        await message.answer(
            pe(f"📸 Добавлено {len(pending_photos)} фото (максимум).\n"
            "Нажмите «Сохранить» для завершения."),
            parse_mode="HTML",
            reply_markup=photo_collection_keyboard(mailing_id, len(pending_photos), is_create=True),
        )
    else:
        await message.answer(
            pe(f"📸 Фото добавлено ({len(pending_photos)}/10).\n"
            "Отправьте ещё фото или нажмите «Сохранить»."),
            parse_mode="HTML",
            reply_markup=photo_collection_keyboard(mailing_id, len(pending_photos), is_create=True),
        )


@router.message(CreateMailingStates.waiting_message_text)
async def process_create_message_text(message: Message, state: FSMContext, db: Database):
    text = (message.text or "").strip()
    if not text:
        await message.answer(pe("❌ Отправьте текст или фото."), parse_mode="HTML")
        return
    entities_json = serialize_entities(message.entities)
    data = await state.get_data()
    mailing_id = data["mailing_id"]
    pending_photos = data.get("pending_photos", [])

    if pending_photos:
        await db.add_mailing_message(mailing_id, text, photo_paths=pending_photos,
                                     entities_json=entities_json)
        await state.update_data(pending_photos=[], pending_caption=None,
                                pending_caption_entities=None)
        await state.set_state(CreateMailingStates.adding_messages)
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Сообщение с {len(pending_photos)} фото добавлено! Всего сообщений: {len(messages)}\n\n"
            "Добавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
        )
    else:
        await db.add_mailing_message(mailing_id, text, entities_json=entities_json)
        await state.set_state(CreateMailingStates.adding_messages)
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Текст добавлен! Всего сообщений: {len(messages)}\n\n"
            "Добавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
        )


@router.callback_query(F.data.startswith("create_save_photos:"))
async def callback_create_save_photos(callback: CallbackQuery, state: FSMContext, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    pending_photos = data.get("pending_photos", [])

    if not pending_photos:
        await callback.answer("Нет фото для сохранения", show_alert=True)
        return

    caption = data.get("pending_caption") or ""
    entities_json = data.get("pending_caption_entities")
    await db.add_mailing_message(mailing_id, caption, photo_paths=pending_photos,
                                 entities_json=entities_json)
    await state.update_data(pending_photos=[], pending_caption=None,
                            pending_caption_entities=None)
    await state.set_state(CreateMailingStates.adding_messages)

    messages = await db.get_mailing_messages(mailing_id)
    await callback.message.edit_text(
        pe(f"✅ Сообщение с {len(pending_photos)} фото добавлено! Всего сообщений: {len(messages)}\n\n"
        "Добавьте ещё или нажмите «Готово»:"),
        parse_mode="HTML",
        reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
    )
    await callback.answer()


@router.callback_query(
    CreateMailingStates.adding_messages, F.data.startswith("create_delete_msg:")
)
async def callback_create_delete_msg(callback: CallbackQuery, state: FSMContext, db: Database):
    message_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    await db.delete_mailing_message(message_id)
    messages = await db.get_mailing_messages(mailing_id)

    await callback.answer("Текст удалён")
    await callback.message.edit_text(
        f"📝 Тексты ({len(messages)} шт.). Добавьте ещё или нажмите «Готово»:",
        reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
    )


@router.callback_query(
    CreateMailingStates.adding_messages, F.data.startswith("create_messages_done:")
)
async def callback_create_messages_done(callback: CallbackQuery, state: FSMContext, db: Database):
    data = await state.get_data()
    mailing_id = data["mailing_id"]
    targets = await db.get_mailing_targets(mailing_id)

    await state.set_state(CreateMailingStates.adding_targets)

    await callback.message.edit_text(
        "Шаг 5/6: Добавьте целевые чаты/группы\n\n"
        "Введите username или ID чата.\n"
        "Минимум 1 чат обязателен.",
        reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
    )
    await callback.answer()


@router.callback_query(
    CreateMailingStates.adding_targets, F.data.startswith("create_add_target:")
)
async def callback_create_add_target(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateMailingStates.waiting_target)

    await callback.message.edit_text(
        pe("🎯 Введите username, ID или ссылку на чат/группу:\n\n"
        "Примеры:\n"
        "• @username\n"
        "• -1001234567890\n"
        "• https://t.me/chatname"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_target)
async def process_create_target(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    # Try to parse as t.me link
    parsed = parse_chat_link(text)
    target = parsed if parsed else text

    await db.add_mailing_target(mailing_id, target)
    await state.set_state(CreateMailingStates.adding_targets)

    targets = await db.get_mailing_targets(mailing_id)

    await message.answer(
        pe(f"✅ Чат добавлен! Всего чатов: {len(targets)}\n\n"
        "Добавьте ещё или нажмите «Готово»:"),
        parse_mode="HTML",
        reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
    )


@router.callback_query(
    CreateMailingStates.adding_targets, F.data.startswith("create_delete_target:")
)
async def callback_create_delete_target(callback: CallbackQuery, state: FSMContext, db: Database):
    target_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    await db.delete_mailing_target(target_id)
    targets = await db.get_mailing_targets(mailing_id)

    await callback.answer("Чат удалён")
    await callback.message.edit_text(
        f"🎯 Чаты ({len(targets)} шт.). Добавьте ещё или нажмите «Готово»:",
        reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
    )


@router.callback_query(
    CreateMailingStates.adding_targets, F.data.startswith("create_add_folder:")
)
async def callback_create_add_folder(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateMailingStates.waiting_folder)

    await callback.message.edit_text(
        pe("📁 Отправьте ссылку на папку чатов:\n\n"
        "Пример:\n"
        "• https://t.me/addlist/xxxxx"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_folder)
async def process_create_folder(
    message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager
):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    slug = parse_folder_slug(text)
    if not slug:
        await message.answer(
            "❌ Неверная ссылка. Отправьте ссылку в формате:\n"
            "https://t.me/addlist/xxxxx"
        )
        return

    mailing = await db.get_mailing(mailing_id)
    if not mailing:
        await message.answer(pe("❌ Рассылка не найдена"), parse_mode="HTML")
        await state.clear()
        return

    client = await userbot_manager.get_client(mailing.account_id)
    if not client:
        await message.answer(pe("❌ Аккаунт не подключён. Проверьте аккаунт."), parse_mode="HTML")
        await state.clear()
        return

    try:
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
        result = await client(CheckChatlistInviteRequest(slug=slug))

        chats = getattr(result, 'chats', [])
        if not chats:
            await message.answer(pe("❌ Папка пуста или не удалось получить чаты."), parse_mode="HTML")
            return

        added = 0
        for entity in chats:
            try:
                if hasattr(entity, 'username') and entity.username:
                    identifier = f"@{entity.username}"
                else:
                    chat_id = int(f"-100{entity.id}")
                    identifier = str(chat_id)
                await db.add_mailing_target(mailing_id, identifier)
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add chat from folder: {e}")
                continue

        await state.set_state(CreateMailingStates.adding_targets)
        targets = await db.get_mailing_targets(mailing_id)

        await message.answer(
            pe(f"✅ Добавлено {added} чатов из папки! Всего чатов: {len(targets)}\n\n"
            "Добавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
        )

    except Exception as e:
        logger.error(f"Error resolving folder {slug}: {e}")
        await message.answer(
            pe(f"❌ Ошибка при получении чатов из папки: {e}"),
            parse_mode="HTML",
        )


@router.callback_query(
    CreateMailingStates.adding_targets, F.data.startswith("create_targets_done:")
)
async def callback_create_targets_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    await state.set_state(CreateMailingStates.waiting_hours)

    await callback.message.edit_text(
        "Шаг 6/6: Время активности\n\n"
        "Хотите настроить часы работы рассылки?\n"
        "Или работать 24/7?",
        reply_markup=active_hours_keyboard(mailing_id),
    )
    await callback.answer()


@router.callback_query(CreateMailingStates.waiting_hours, F.data.startswith("skip_hours:"))
async def callback_skip_hours(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    await state.clear()

    await callback.message.edit_text(
        pe("✅ Рассылка создана!\n\n"
        "Нажмите «Запустить рассылку», чтобы начать отправку."),
        parse_mode="HTML",
        reply_markup=launch_mailing_keyboard(mailing_id),
    )
    await callback.answer()


@router.callback_query(CreateMailingStates.waiting_hours, F.data.startswith("setup_hours:"))
async def callback_setup_hours(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        pe("⏰ Введите диапазон времени в формате:\n"
        "10:00-13:00\n\n"
        "Можно указать несколько диапазонов через запятую:\n"
        "10:00-13:00, 18:00-22:00"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_hours)
async def process_create_hours(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    ranges = []
    for part in text.split(","):
        part = part.strip()
        parsed = parse_time_range(part)
        if parsed:
            ranges.append(parsed)

    if not ranges:
        await message.answer(
            "❌ Неверный формат. Используйте формат: 10:00-13:00\n"
            "Или несколько диапазонов: 10:00-13:00, 18:00-22:00"
        )
        return

    active_hours_json = create_active_hours_json(ranges)
    await db.update_mailing_active_hours(mailing_id, active_hours_json)
    await state.clear()

    await message.answer(
        pe(f"✅ Рассылка создана!\n"
        f"Время активности: {format_active_hours(active_hours_json)}\n\n"
        "Нажмите «Запустить рассылку», чтобы начать отправку."),
        parse_mode="HTML",
        reply_markup=launch_mailing_keyboard(mailing_id),
    )


@router.callback_query(F.data.startswith("launch_mailing:"))
async def callback_launch_mailing(
    callback: CallbackQuery, db: Database, mailing_service: MailingService
):
    mailing_id = int(callback.data.split(":")[1])

    success = await mailing_service.start_mailing(mailing_id)

    if success:
        await callback.message.edit_text(
            pe("🚀 Рассылка запущена!"),
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer("Рассылка запущена!")
    else:
        await callback.answer(
            "❌ Не удалось запустить. Проверьте аккаунт.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("change_mailing_account:"))
async def callback_change_mailing_account(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)

    if not accounts:
        await callback.answer("У вас нет аккаунтов", show_alert=True)
        return

    await callback.message.edit_text(
        pe("🔄 Выберите аккаунт для рассылки:"),
        parse_mode="HTML",
        reply_markup=select_account_for_mailing_keyboard(accounts, mailing_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_mailing_account:"))
async def callback_set_mailing_account(callback: CallbackQuery, db: Database):
    parts = callback.data.split(":")
    account_id = int(parts[1])
    mailing_id = int(parts[2])

    await db.update_mailing_account(mailing_id, account_id)
    await callback.answer("✅ Аккаунт изменён")

    mailing = await db.get_mailing(mailing_id)
    account = await db.get_account(mailing.account_id)
    messages = await db.get_mailing_messages(mailing_id)
    targets = await db.get_mailing_targets(mailing_id)

    status = "🟢 Активна" if mailing.is_active else "🔴 Остановлена"
    last_sent = _fmt_dt(mailing.last_sent_at)
    active_hours = format_active_hours(mailing.active_hours_json)

    text = pe(
        f"📋 Рассылка: {mailing.name}\n\n"
        f"Статус: {status}\n"
        f"Аккаунт: {account.display_name if account else 'не найден'}\n"
        f"Интервал: {mailing.interval_seconds} сек\n"
        f"Время активности: {active_hours}\n"
        f"Сообщений: {len(messages)}\n"
        f"Целевых чатов: {len(targets)}\n"
        f"Последняя отправка: {last_sent}\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=mailing_menu_keyboard(mailing))


@router.callback_query(F.data.startswith("change_msg_format:"))
async def callback_change_msg_format(callback: CallbackQuery, db: Database):
    parts = callback.data.split(":")
    message_id = int(parts[1])
    mailing_id = int(parts[2])

    await callback.message.edit_text(
        "🔤 Выберите формат текста сообщения:",
        reply_markup=parse_mode_keyboard(message_id, mailing_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_parse_mode:"))
async def callback_set_parse_mode(callback: CallbackQuery, db: Database):
    parts = callback.data.split(":")
    mode = parts[1]
    message_id = int(parts[2])
    mailing_id = int(parts[3])

    await db.update_message_parse_mode(message_id, mode)
    await callback.answer(f"✅ Формат изменён на {mode}")

    messages = await db.get_mailing_messages(mailing_id)

    text = f"📝 Сообщения рассылки ({len(messages)} шт.):\n\n"
    if messages:
        for i, msg in enumerate(messages, 1):
            fmt = f"[{msg.parse_mode or 'html'}]"
            text += f"{i}. {fmt} {message_preview(msg)}\n"
    else:
        text += "Сообщений пока нет.\n"

    await callback.message.edit_text(
        text, reply_markup=mailing_messages_keyboard(mailing_id, messages)
    )


@router.callback_query(F.data.startswith("cancel_creation:"))
async def callback_cancel_creation(
    callback: CallbackQuery, state: FSMContext, db: Database
):
    mailing_id = int(callback.data.split(":")[1])

    await db.delete_mailing(mailing_id)
    await state.clear()

    await callback.message.edit_text(
        pe("❌ Создание рассылки отменено"),
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()
