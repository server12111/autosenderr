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
    multi_account_select_keyboard,
    reply_mode_select_keyboard,
    reply_mode_fixed_keyboard,
    skip_thread_keyboard,
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


def _parse_txt_targets(content: str) -> list:
    """Parse .txt file content into deduplicated list of chat identifiers."""
    raw = content.replace(',', ' ').replace(';', ' ')
    tokens = raw.split()
    result = []
    seen = set()
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        parsed = parse_chat_link(token)
        if parsed:
            identifier = parsed
        elif token.startswith('@') or token.lstrip('-').isdigit():
            identifier = token
        else:
            continue
        if identifier not in seen:
            seen.add(identifier)
            result.append(identifier)
    return result


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
    waiting_txt_file = State()
    waiting_hours = State()


class EditMailingStates(StatesGroup):
    waiting_message_text = State()
    waiting_forward_message = State()
    waiting_target = State()
    waiting_folder = State()
    waiting_txt_file = State()
    waiting_hours = State()
    waiting_target_interval = State()
    waiting_reply_range = State()
    waiting_thread_id = State()
    waiting_thread_id_for_target = State()


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
async def callback_mailing_menu(callback: CallbackQuery, db: Database, state: FSMContext):
    await state.clear()
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
        pe("📨 Перешлите любое сообщение из канала, группы или от пользователя.\n"
        "Бот сохранит его и будет использовать при рассылке."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_forward_message)
async def process_edit_forward_message(message: Message, state: FSMContext, db: Database):
    from aiogram.types import MessageOriginChannel, MessageOriginChat, MessageOriginUser
    origin = message.forward_origin
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    if isinstance(origin, MessageOriginChannel):
        peer = f"@{origin.chat.username}" if origin.chat.username else str(origin.chat.id)
        msg_id = origin.message_id
        await db.add_mailing_forward(mailing_id, peer, msg_id)
        await state.clear()
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
            f"Всего записей: {len(messages)}"),
            parse_mode="HTML",
            reply_markup=mailing_messages_keyboard(mailing_id, messages),
        )
    elif isinstance(origin, MessageOriginChat):
        peer = f"@{origin.sender_chat.username}" if origin.sender_chat.username else str(origin.sender_chat.id)
        msg_id = origin.message_id
        await db.add_mailing_forward(mailing_id, peer, msg_id)
        await state.clear()
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
            f"Всего записей: {len(messages)}"),
            parse_mode="HTML",
            reply_markup=mailing_messages_keyboard(mailing_id, messages),
        )
    elif isinstance(origin, MessageOriginUser):
        text = message.text or message.caption or ""
        photo_path = await save_photo_from_message(message) if message.photo else None
        if not text and not photo_path:
            await message.answer(
                "❌ Поддерживаются только текст и фото. Видео, голосовые и стикеры не поддерживаются."
            )
            return
        entities_json = serialize_entities(message.entities or message.caption_entities)
        await db.add_mailing_message(mailing_id, text, photo_path=photo_path, entities_json=entities_json)
        await state.clear()
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Сообщение от пользователя сохранено!\n"
            f"Всего записей: {len(messages)}"),
            parse_mode="HTML",
            reply_markup=mailing_messages_keyboard(mailing_id, messages),
        )
    else:
        await message.answer(
            "❌ Не удалось определить источник. Перешлите сообщение из канала, группы или от пользователя."
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
    mailing = await db.get_mailing(mailing_id)

    text = f"🎯 Целевые чаты ({len(targets)} шт.):\n\n"
    if targets:
        for i, target in enumerate(targets, 1):
            thread_info = f" [тема #{target.thread_id}]" if target.thread_id else ""
            text += f"{i}. {target.chat_identifier}{thread_info}\n"
    else:
        text += "Целевых чатов пока нет.\n"

    text += "\nНажмите на чат, чтобы удалить его:"

    keep = mailing.keep_targets_on_ban if mailing else False
    await callback.message.edit_text(
        pe(text), parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets)
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
async def process_edit_target(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    parsed = parse_chat_link(text)
    target = parsed if parsed else text

    mailing = await db.get_mailing(mailing_id)
    is_forum = False
    if mailing:
        client = await userbot_manager.get_client(mailing.account_id)
        if client:
            is_forum = await _is_real_forum(client, target)

    target_id = await db.add_mailing_target(mailing_id, target, is_forum=is_forum)

    if is_forum:
        await state.update_data(target_id=target_id, mailing_id=mailing_id)
        await state.set_state(EditMailingStates.waiting_thread_id_for_target)
        await message.answer(
            pe(f"💬 Чат <b>{target}</b> использует темы (Topics).\n\n"
               "Отправьте ссылку на тему или её ID.\n"
               "Примеры: <code>https://t.me/chatname/123</code> или <code>123</code>\n\n"
               "Нажмите «Пропустить» для отправки в General."),
            parse_mode="HTML",
            reply_markup=skip_thread_keyboard(mailing_id, target),
        )
        return

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

    loading_msg = await message.answer(pe("⏳ Загружаем чаты из папки..."), parse_mode="HTML")
    try:
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
        result = await client(CheckChatlistInviteRequest(slug=slug))

        chats = getattr(result, 'chats', [])
        if not chats:
            await loading_msg.delete()
            await message.answer(pe("❌ Папка пуста или не удалось получить чаты."), parse_mode="HTML")
            return

        added = 0
        for entity in chats:
            try:
                if type(entity).__name__ in ('ChannelForbidden', 'ChatForbidden'):
                    continue
                identifier = f"@{entity.username}" if getattr(entity, 'username', None) else str(int(f"-100{entity.id}"))
                await db.add_mailing_target(mailing_id, identifier)
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add chat from folder: {e}")
                continue

        await state.clear()
        targets = await db.get_mailing_targets(mailing_id)

        forum_hint = ""
        added_identifiers = [
            f"@{getattr(e, 'username')}" if getattr(e, 'username', None) else str(int(f"-100{e.id}"))
            for e in chats
            if hasattr(e, 'id') and type(e).__name__ not in ('ChannelForbidden', 'ChatForbidden')
        ]
        new_targets = [t for t in targets if t.chat_identifier in added_identifiers]
        forums = await _find_forum_targets(client, new_targets)
        if forums:
            for t in new_targets:
                if t.chat_identifier in forums:
                    await db.update_target_is_forum(t.id, True)
            targets = await db.get_mailing_targets(mailing_id)
            forum_hint = pe(f"\n\n🧵 Найдено {len(forums)} форум-чатов с темами: {', '.join(forums[:5])}{'...' if len(forums) > 5 else ''}\nНастройте тему через кнопку 🧵 в списке чатов.")

        await loading_msg.delete()
        await message.answer(
            pe(f"✅ Добавлено {added} чатов из папки! Всего чатов: {len(targets)}") + forum_hint,
            parse_mode="HTML",
            reply_markup=mailing_targets_keyboard(mailing_id, targets),
        )

    except Exception as e:
        logger.error(f"Error resolving folder {slug}: {e}")
        await loading_msg.delete()
        await message.answer(
            pe(f"❌ Ошибка при получении чатов из папки: {e}"),
            parse_mode="HTML",
        )


# === TXT file targets (edit mode) ===
@router.callback_query(F.data.startswith("add_txt_target:"))
async def callback_add_txt_target(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_txt_file)
    await callback.message.edit_text(
        pe("📄 Отправьте .txt файл со списком чатов.\n\n"
        "Формат: каждый чат на новой строке или через пробел.\n\n"
        "Примеры:\n"
        "• @username\n"
        "• -1001234567890\n"
        "• https://t.me/chatname"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_txt_file, F.document)
async def process_edit_txt_file(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith('.txt'):
        await message.answer(
            pe("❌ Пожалуйста, отправьте файл с расширением .txt"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return
    if doc.file_size and doc.file_size > 500_000:
        await message.answer(
            pe("❌ Файл слишком большой. Максимум 500 КБ."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    mailing_id = data["mailing_id"]

    file_io = await message.bot.download(doc)
    try:
        content = file_io.read().decode('utf-8')
    except UnicodeDecodeError:
        file_io.seek(0)
        content = file_io.read().decode('cp1251', errors='replace')

    identifiers = _parse_txt_targets(content)
    if not identifiers:
        await message.answer(
            pe("❌ В файле не найдено ни одного чата."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    added = 0
    for identifier in identifiers:
        try:
            await db.add_mailing_target(mailing_id, identifier)
            added += 1
        except Exception:
            pass

    await state.clear()
    targets = await db.get_mailing_targets(mailing_id)

    forum_hint = ""
    mailing = await db.get_mailing(mailing_id)
    if mailing:
        client = await userbot_manager.get_client(mailing.account_id)
        if client:
            new_targets = [t for t in targets if t.chat_identifier in identifiers]
            forums = await _find_forum_targets(client, new_targets)
            if forums:
                for t in new_targets:
                    if t.chat_identifier in forums:
                        await db.update_target_is_forum(t.id, True)
                targets = await db.get_mailing_targets(mailing_id)
                forum_hint = pe(f"\n\n🧵 Найдено {len(forums)} форум-чатов с темами: {', '.join(forums[:5])}{'...' if len(forums) > 5 else ''}\nНастройте тему через кнопку 🧵 в списке чатов.")

    await message.answer(
        pe(f"✅ Добавлено {added} чатов из файла! Всего чатов: {len(targets)}") + forum_hint,
        parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets),
    )


@router.message(EditMailingStates.waiting_txt_file)
async def process_edit_txt_wrong(message: Message):
    await message.answer(
        pe("❌ Отправьте .txt файл, а не текст."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
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
        mailing = await db.get_mailing(mailing_id)
        await message.answer(
            pe("✅ Время активности сброшено (24/7)"),
            parse_mode="HTML",
            reply_markup=mailing_menu_keyboard(mailing) if mailing else main_menu_keyboard(),
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

    mailing = await db.get_mailing(mailing_id)
    await message.answer(
        pe(f"✅ Время активности обновлено: {format_active_hours(active_hours_json)}"),
        parse_mode="HTML",
        reply_markup=mailing_menu_keyboard(mailing) if mailing else main_menu_keyboard(),
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

    user = await db.get_user(callback.from_user.id)
    mailings = await db.get_user_mailings(user.id)
    await callback.message.edit_text(
        pe("✅ Рассылка удалена"),
        parse_mode="HTML",
        reply_markup=mailings_keyboard(mailings),
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
        pe("📨 Перешлите любое сообщение из канала, группы или от пользователя.\n"
        "Бот сохранит его и будет использовать при рассылке."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_forward_message)
async def process_create_forward_message(message: Message, state: FSMContext, db: Database):
    from aiogram.types import MessageOriginChannel, MessageOriginChat, MessageOriginUser
    origin = message.forward_origin
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    if isinstance(origin, MessageOriginChannel):
        peer = f"@{origin.chat.username}" if origin.chat.username else str(origin.chat.id)
        msg_id = origin.message_id
        await db.add_mailing_forward(mailing_id, peer, msg_id)
        await state.set_state(CreateMailingStates.adding_messages)
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
            f"Всего записей: {len(messages)}\n\nДобавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
        )
    elif isinstance(origin, MessageOriginChat):
        peer = f"@{origin.sender_chat.username}" if origin.sender_chat.username else str(origin.sender_chat.id)
        msg_id = origin.message_id
        await db.add_mailing_forward(mailing_id, peer, msg_id)
        await state.set_state(CreateMailingStates.adding_messages)
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Пересылка сохранена!\n📌 Источник: {peer} / сообщение #{msg_id}\n"
            f"Всего записей: {len(messages)}\n\nДобавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
        )
    elif isinstance(origin, MessageOriginUser):
        text = message.text or message.caption or ""
        photo_path = await save_photo_from_message(message) if message.photo else None
        if not text and not photo_path:
            await message.answer(
                "❌ Поддерживаются только текст и фото. Видео, голосовые и стикеры не поддерживаются."
            )
            return
        entities_json = serialize_entities(message.entities or message.caption_entities)
        await db.add_mailing_message(mailing_id, text, photo_path=photo_path, entities_json=entities_json)
        await state.set_state(CreateMailingStates.adding_messages)
        messages = await db.get_mailing_messages(mailing_id)
        await message.answer(
            pe(f"✅ Сообщение от пользователя сохранено!\n"
            f"Всего записей: {len(messages)}\n\nДобавьте ещё или нажмите «Готово»:"),
            parse_mode="HTML",
            reply_markup=mailing_creation_messages_keyboard(mailing_id, messages),
        )
    else:
        await message.answer(
            "❌ Не удалось определить источник. Перешлите сообщение из канала, группы или от пользователя."
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
async def process_create_target(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    parsed = parse_chat_link(text)
    target = parsed if parsed else text

    mailing = await db.get_mailing(mailing_id)
    is_forum = False
    if mailing:
        client = await userbot_manager.get_client(mailing.account_id)
        if client:
            is_forum = await _is_real_forum(client, target)

    target_id = await db.add_mailing_target(mailing_id, target, is_forum=is_forum)

    if is_forum:
        await state.update_data(target_id=target_id, mailing_id=mailing_id)
        await state.set_state(EditMailingStates.waiting_thread_id_for_target)
        await message.answer(
            pe(f"💬 Чат <b>{target}</b> использует темы (Topics).\n\n"
               "Отправьте ссылку на тему или её ID.\n"
               "Примеры: <code>https://t.me/chatname/123</code> или <code>123</code>\n\n"
               "Нажмите «Пропустить» для отправки в General."),
            parse_mode="HTML",
            reply_markup=skip_thread_keyboard(mailing_id, target),
        )
        return

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

    loading_msg = await message.answer(pe("⏳ Загружаем чаты из папки..."), parse_mode="HTML")
    try:
        from telethon.tl.functions.chatlists import CheckChatlistInviteRequest
        result = await client(CheckChatlistInviteRequest(slug=slug))

        chats = getattr(result, 'chats', [])
        if not chats:
            await loading_msg.delete()
            await message.answer(pe("❌ Папка пуста или не удалось получить чаты."), parse_mode="HTML")
            return

        added = 0
        for entity in chats:
            try:
                if type(entity).__name__ in ('ChannelForbidden', 'ChatForbidden'):
                    continue
                identifier = f"@{entity.username}" if getattr(entity, 'username', None) else str(int(f"-100{entity.id}"))
                await db.add_mailing_target(mailing_id, identifier)
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add chat from folder: {e}")
                continue

        await state.set_state(CreateMailingStates.adding_targets)
        targets = await db.get_mailing_targets(mailing_id)

        forum_hint = ""
        added_identifiers = [
            f"@{getattr(e, 'username')}" if getattr(e, 'username', None) else str(int(f"-100{e.id}"))
            for e in chats
            if hasattr(e, 'id') and type(e).__name__ not in ('ChannelForbidden', 'ChatForbidden')
        ]
        new_targets = [t for t in targets if t.chat_identifier in added_identifiers]
        forums = await _find_forum_targets(client, new_targets)
        if forums:
            for t in new_targets:
                if t.chat_identifier in forums:
                    await db.update_target_is_forum(t.id, True)
            targets = await db.get_mailing_targets(mailing_id)
            forum_hint = pe(f"\n\n🧵 Найдено {len(forums)} форум-чатов с темами: {', '.join(forums[:5])}{'...' if len(forums) > 5 else ''}\nНастройте тему через кнопку 🧵 в списке чатов.")

        await loading_msg.delete()
        await message.answer(
            pe(f"✅ Добавлено {added} чатов из папки! Всего чатов: {len(targets)}\n\nДобавьте ещё или нажмите «Готово»:") + forum_hint,
            parse_mode="HTML",
            reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
        )

    except Exception as e:
        logger.error(f"Error resolving folder {slug}: {e}")
        await loading_msg.delete()
        await message.answer(
            pe(f"❌ Ошибка при получении чатов из папки: {e}"),
            parse_mode="HTML",
        )


# === TXT file targets (create mode) ===
@router.callback_query(
    CreateMailingStates.adding_targets, F.data.startswith("create_add_txt:")
)
async def callback_create_add_txt(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(mailing_id=mailing_id)
    await state.set_state(CreateMailingStates.waiting_txt_file)
    await callback.message.edit_text(
        pe("📄 Отправьте .txt файл со списком чатов.\n\n"
        "Формат: каждый чат на новой строке или через пробел.\n\n"
        "Примеры:\n"
        "• @username\n"
        "• -1001234567890\n"
        "• https://t.me/chatname"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(CreateMailingStates.waiting_txt_file, F.document)
async def process_create_txt_file(message: Message, state: FSMContext, db: Database, userbot_manager: UserbotManager):
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith('.txt'):
        await message.answer(
            pe("❌ Пожалуйста, отправьте файл с расширением .txt"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return
    if doc.file_size and doc.file_size > 500_000:
        await message.answer(
            pe("❌ Файл слишком большой. Максимум 500 КБ."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    mailing_id = data["mailing_id"]

    file_io = await message.bot.download(doc)
    try:
        content = file_io.read().decode('utf-8')
    except UnicodeDecodeError:
        file_io.seek(0)
        content = file_io.read().decode('cp1251', errors='replace')

    identifiers = _parse_txt_targets(content)
    if not identifiers:
        await message.answer(
            pe("❌ В файле не найдено ни одного чата."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    added = 0
    for identifier in identifiers:
        try:
            await db.add_mailing_target(mailing_id, identifier)
            added += 1
        except Exception:
            pass

    await state.set_state(CreateMailingStates.adding_targets)
    targets = await db.get_mailing_targets(mailing_id)

    forum_hint = ""
    mailing = await db.get_mailing(mailing_id)
    if mailing:
        client = await userbot_manager.get_client(mailing.account_id)
        if client:
            new_targets = [t for t in targets if t.chat_identifier in identifiers]
            forums = await _find_forum_targets(client, new_targets)
            if forums:
                for t in new_targets:
                    if t.chat_identifier in forums:
                        await db.update_target_is_forum(t.id, True)
                targets = await db.get_mailing_targets(mailing_id)
                forum_hint = pe(f"\n\n🧵 Найдено {len(forums)} форум-чатов с темами: {', '.join(forums[:5])}{'...' if len(forums) > 5 else ''}\nНастройте тему через кнопку 🧵 в списке чатов.")

    await message.answer(
        pe(f"✅ Добавлено {added} чатов из файла! Всего чатов: {len(targets)}\n\nДобавьте ещё или нажмите «Готово»:") + forum_hint,
        parse_mode="HTML",
        reply_markup=mailing_creation_targets_keyboard(mailing_id, targets),
    )


@router.message(CreateMailingStates.waiting_txt_file)
async def process_create_txt_wrong(message: Message):
    await message.answer(
        pe("❌ Отправьте .txt файл, а не текст."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
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
        mailing = await db.get_mailing(mailing_id)
        await callback.message.edit_text(
            pe("🚀 Рассылка запущена!"),
            parse_mode="HTML",
            reply_markup=mailing_menu_keyboard(mailing) if mailing else main_menu_keyboard(),
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


@router.callback_query(F.data.startswith("mailing_multi_accounts:"))
async def callback_mailing_multi_accounts(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)

    if not accounts:
        await callback.answer("У вас нет аккаунтов", show_alert=True)
        return

    selected_ids = await db.get_mailing_extra_account_ids(mailing_id)
    mailing = await db.get_mailing(mailing_id)
    await callback.message.edit_text(
        pe("👥 Выберите аккаунты для чередования.\n"
           "Нажмите на аккаунт чтобы добавить/убрать.\n"
           "Выберите режим чередования, затем «Готово»."),
        parse_mode="HTML",
        reply_markup=multi_account_select_keyboard(accounts, selected_ids, mailing_id, mailing.account_rotation_mode),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_mailing_account:"))
async def callback_toggle_mailing_account(callback: CallbackQuery, db: Database):
    parts = callback.data.split(":")
    account_id = int(parts[1])
    mailing_id = int(parts[2])

    await db.toggle_mailing_extra_account(mailing_id, account_id)

    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)
    selected_ids = await db.get_mailing_extra_account_ids(mailing_id)
    mailing = await db.get_mailing(mailing_id)
    await callback.message.edit_reply_markup(
        reply_markup=multi_account_select_keyboard(accounts, selected_ids, mailing_id, mailing.account_rotation_mode)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_rotation_mode:"))
async def callback_toggle_rotation_mode(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)
    new_mode = "per_cycle" if mailing.account_rotation_mode == "per_target" else "per_target"
    await db.update_mailing_rotation_mode(mailing_id, new_mode)

    user = await db.get_user(callback.from_user.id)
    accounts = await db.get_user_accounts(user.id)
    selected_ids = await db.get_mailing_extra_account_ids(mailing_id)
    await callback.message.edit_reply_markup(
        reply_markup=multi_account_select_keyboard(accounts, selected_ids, mailing_id, new_mode)
    )
    await callback.answer()


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

    user = await db.get_user(callback.from_user.id)
    mailings = await db.get_user_mailings(user.id)
    await callback.message.edit_text(
        pe("❌ Создание рассылки отменено"),
        parse_mode="HTML",
        reply_markup=mailings_keyboard(mailings),
    )
    await callback.answer()


async def _is_real_forum(client, target) -> bool:
    """True only if the chat has actual custom topics (not just General). Requires membership."""
    try:
        from telethon.tl.functions.channels import GetForumTopicsRequest
        entity = await client.get_entity(target)
        if not getattr(entity, 'forum', False):
            return False
        result = await client(GetForumTopicsRequest(
            channel=entity,
            q='',
            offset_date=0,
            offset_id=0,
            offset_topic=0,
            limit=10,
        ))
        custom_topics = [t for t in result.topics if t.id != 1]
        return len(custom_topics) > 0
    except Exception:
        return False


async def _has_forum_flag(client, target) -> bool:
    """Lightweight check — only entity.forum flag, works without membership."""
    try:
        entity = await client.get_entity(target)
        return bool(getattr(entity, 'forum', False))
    except Exception:
        return False


async def _find_forum_targets(client, targets: list) -> list[str]:
    """Check which chat identifiers have forum flag (lightweight, no membership needed)."""
    forums = []
    for t in targets:
        if await _has_forum_flag(client, t.chat_identifier):
            forums.append(t.chat_identifier)
    return forums


# === Keep Targets Toggle ===
@router.callback_query(F.data.startswith("toggle_keep_targets:"))
async def callback_toggle_keep_targets(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)
    if not mailing:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return
    new_val = not mailing.keep_targets_on_ban
    await db.update_mailing_keep_targets(mailing_id, new_val)
    status = "включена" if new_val else "выключена"
    await callback.answer(f"Настройка {status}")

    targets = await db.get_mailing_targets(mailing_id)
    text = f"🎯 Целевые чаты ({len(targets)} шт.):\n\n"
    if targets:
        for i, target in enumerate(targets, 1):
            thread_info = f" [тема #{target.thread_id}]" if target.thread_id else ""
            text += f"{i}. {target.chat_identifier}{thread_info}\n"
    else:
        text += "Целевых чатов пока нет.\n"
    text += "\nНажмите на чат, чтобы удалить его:"
    await callback.message.edit_text(
        pe(text), parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets)
    )


# === Topics (Threads) ===
@router.callback_query(F.data.startswith("set_target_thread:"))
async def callback_set_target_thread(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    target_id = int(parts[1])
    mailing_id = int(parts[2])
    await state.update_data(target_id=target_id, mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_thread_id_for_target)
    await callback.message.edit_text(
        pe("🧵 Отправьте ссылку на тему или её ID:\n\n"
           "Примеры:\n"
           "• https://t.me/chatname/123\n"
           "• 123 (числовой ID темы)\n\n"
           "Отправьте 0 — сбросить привязку к теме."),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_thread_id_for_target)
async def process_thread_id_for_target(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    target_id = data["target_id"]
    mailing_id = data["mailing_id"]

    if text == "0":
        await db.update_target_thread(target_id, None)
        await state.clear()
        targets = await db.get_mailing_targets(mailing_id)
        mailing = await db.get_mailing(mailing_id)
        keep = mailing.keep_targets_on_ban if mailing else False
        await message.answer(
            pe("✅ Привязка к теме удалена."),
            parse_mode="HTML",
            reply_markup=mailing_targets_keyboard(mailing_id, targets),
        )
        return

    # Try to parse thread_id from link or number
    thread_id = None
    m = re.search(r't\.me/\S+/(\d+)', text)
    if m:
        thread_id = int(m.group(1))
    elif text.isdigit():
        thread_id = int(text)

    if not thread_id:
        await message.answer(
            pe("❌ Не удалось определить ID темы. Отправьте ссылку или число."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    await db.update_target_thread(target_id, thread_id)
    await state.clear()

    targets = await db.get_mailing_targets(mailing_id)
    mailing = await db.get_mailing(mailing_id)
    keep = mailing.keep_targets_on_ban if mailing else False
    await message.answer(
        pe(f"✅ Тема #{thread_id} привязана к чату."),
        parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets),
    )


@router.callback_query(F.data.startswith("skip_thread:"))
async def callback_skip_thread(callback: CallbackQuery, state: FSMContext, db: Database):
    parts = callback.data.split(":", 2)
    mailing_id = int(parts[1])
    target_identifier = parts[2] if len(parts) > 2 else ""
    await state.clear()
    targets = await db.get_mailing_targets(mailing_id)
    mailing = await db.get_mailing(mailing_id)
    keep = mailing.keep_targets_on_ban if mailing else False
    await callback.message.edit_text(
        pe(f"✅ Чат добавлен без привязки к теме (General)."),
        parse_mode="HTML",
        reply_markup=mailing_targets_keyboard(mailing_id, targets),
    )
    await callback.answer()


# === Reply Mode ===
@router.callback_query(F.data.startswith("mailing_reply_mode:"))
async def callback_mailing_reply_mode(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    mailing = await db.get_mailing(mailing_id)

    if not mailing:
        await callback.answer("Рассылка не найдена", show_alert=True)
        return

    if not mailing.reply_mode:
        text = pe(
            "↩️ Ответная рассылка\n\n"
            "Когда включена — бот отвечает на существующие сообщения в чате, "
            "а не отправляет новое сообщение.\n\n"
            "Выберите режим:\n"
            "• На последнее — отвечает на самое новое сообщение\n"
            "• На N-е с конца — на конкретную позицию (2–10)\n"
            "• Случайно — случайная позиция из диапазона (макс. 10)"
        )
        await callback.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=reply_mode_select_keyboard(mailing_id)
        )
    else:
        await db.update_mailing_reply_mode(mailing_id, None, 1, 1, 5)
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

    await callback.answer()


@router.callback_query(F.data.startswith("reply_mode_last:"))
async def callback_reply_mode_last(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    await db.update_mailing_reply_mode(mailing_id, 'last', 1, 1, 5)
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
    await callback.answer("✅ Режим: на последнее сообщение")


@router.callback_query(F.data.startswith("reply_mode_fixed:"))
async def callback_reply_mode_fixed(callback: CallbackQuery, db: Database):
    mailing_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        pe("🔢 Выберите позицию с конца (2–10):"),
        parse_mode="HTML",
        reply_markup=reply_mode_fixed_keyboard(mailing_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reply_mode_fixed_pos:"))
async def callback_reply_mode_fixed_pos(callback: CallbackQuery, db: Database):
    parts = callback.data.split(":")
    mailing_id = int(parts[1])
    n = int(parts[2])
    await db.update_mailing_reply_mode(mailing_id, 'fixed', n, 1, 5)
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
    await callback.answer(f"✅ Режим: {n}-е с конца")


@router.callback_query(F.data.startswith("reply_mode_random:"))
async def callback_reply_mode_random(callback: CallbackQuery, state: FSMContext):
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(mailing_id=mailing_id)
    await state.set_state(EditMailingStates.waiting_reply_range)
    await callback.message.edit_text(
        pe("🎲 Введите диапазон в формате: МИН-МАКС\n\n"
        "Пример: 1-5 (случайное сообщение с 1-й по 5-ю позицию с конца)\n"
        "Максимальное значение: 10"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(EditMailingStates.waiting_reply_range)
async def process_reply_range(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    mailing_id = data["mailing_id"]

    m = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', text)
    if not m:
        await message.answer(
            pe("❌ Неверный формат. Введите диапазон как: 1-8"),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    rmin, rmax = int(m.group(1)), int(m.group(2))
    if rmin < 1 or rmax > 10 or rmin > rmax:
        await message.answer(
            pe("❌ Диапазон должен быть от 1 до 10, минимум ≤ максимум."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    await db.update_mailing_reply_mode(mailing_id, 'random', 1, rmin, rmax)
    await state.clear()

    mailing = await db.get_mailing(mailing_id)
    account = await db.get_account(mailing.account_id)
    messages_list = await db.get_mailing_messages(mailing_id)
    targets = await db.get_mailing_targets(mailing_id)
    status = "🟢 Активна" if mailing.is_active else "🔴 Остановлена"
    last_sent = _fmt_dt(mailing.last_sent_at)
    active_hours = format_active_hours(mailing.active_hours_json)
    text_out = pe(
        f"📋 Рассылка: {mailing.name}\n\n"
        f"Статус: {status}\n"
        f"Аккаунт: {account.phone if account else 'не найден'}\n"
        f"Интервал: {mailing.interval_seconds} сек\n"
        f"Время активности: {active_hours}\n"
        f"Сообщений: {len(messages_list)}\n"
        f"Целевых чатов: {len(targets)}\n"
        f"Последняя отправка: {last_sent}\n\n"
        "Выберите действие:"
    )
    await message.answer(
        text_out, parse_mode="HTML",
        reply_markup=mailing_menu_keyboard(mailing),
    )
