from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import autoresponder_keyboard, cancel_keyboard

router = Router()


class AutoresponderStates(StatesGroup):
    waiting_text = State()


@router.callback_query(F.data.startswith("autoresponder:"))
async def callback_autoresponder(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    notify_status = "✅ Включены" if account.notify_messages else "❌ Выключены"
    text_preview = account.autoresponder_text or "(не задан)"
    if len(text_preview) > 100:
        text_preview = text_preview[:100] + "..."

    text = (
        f"🤖 Автоответчик для {account.phone}\n\n"
        f"Статус: {status}\n"
        f"Уведомления о сообщениях: {notify_status}\n\n"
        f"Текст автоответа:\n{text_preview}\n\n"
        "ℹ️ Автоответчик отвечает на каждое входящее личное сообщение.\n\n"
        "📬 Уведомления — получайте сообщения о каждом входящем ЛС."
    )

    await callback.message.edit_text(
        text, reply_markup=autoresponder_keyboard(account_id, account.autoresponder_enabled, account.notify_messages)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_autoresponder:"))
async def callback_toggle_autoresponder(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    new_status = not account.autoresponder_enabled

    if new_status and not account.autoresponder_text:
        await callback.answer(
            "⚠️ Сначала задайте текст автоответа", show_alert=True
        )
        return

    await db.update_autoresponder(account_id, new_status)

    status_text = "включён" if new_status else "выключен"
    await callback.answer(f"Автоответчик {status_text}")

    account = await db.get_account(account_id)

    status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    notify_status = "✅ Включены" if account.notify_messages else "❌ Выключены"
    text_preview = account.autoresponder_text or "(не задан)"
    if len(text_preview) > 100:
        text_preview = text_preview[:100] + "..."

    text = (
        f"🤖 Автоответчик для {account.phone}\n\n"
        f"Статус: {status}\n"
        f"Уведомления о сообщениях: {notify_status}\n\n"
        f"Текст автоответа:\n{text_preview}\n\n"
        "ℹ️ Автоответчик отвечает на каждое входящее личное сообщение.\n\n"
        "📬 Уведомления — получайте сообщения о каждом входящем ЛС."
    )

    await callback.message.edit_text(
        text, reply_markup=autoresponder_keyboard(account_id, account.autoresponder_enabled, account.notify_messages)
    )


@router.callback_query(F.data.startswith("toggle_notify:"))
async def callback_toggle_notify(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    account = await db.get_account(account_id)

    if not account:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return

    new_status = not account.notify_messages
    await db.update_notify_messages(account_id, new_status)

    status_text = "включены" if new_status else "выключены"
    await callback.answer(f"Уведомления {status_text}")

    account = await db.get_account(account_id)

    status = "✅ Включён" if account.autoresponder_enabled else "❌ Выключен"
    notify_status = "✅ Включены" if account.notify_messages else "❌ Выключены"
    text_preview = account.autoresponder_text or "(не задан)"
    if len(text_preview) > 100:
        text_preview = text_preview[:100] + "..."

    text = (
        f"🤖 Автоответчик для {account.phone}\n\n"
        f"Статус: {status}\n"
        f"Уведомления о сообщениях: {notify_status}\n\n"
        f"Текст автоответа:\n{text_preview}\n\n"
        "ℹ️ Автоответчик отвечает на каждое входящее личное сообщение.\n\n"
        "📬 Уведомления — получайте сообщения о каждом входящем ЛС."
    )

    await callback.message.edit_text(
        text, reply_markup=autoresponder_keyboard(account_id, account.autoresponder_enabled, account.notify_messages)
    )


@router.callback_query(F.data.startswith("edit_autoresponder_text:"))
async def callback_edit_autoresponder_text(
    callback: CallbackQuery, state: FSMContext
):
    account_id = int(callback.data.split(":")[1])

    await state.update_data(account_id=account_id)
    await state.set_state(AutoresponderStates.waiting_text)

    await callback.message.edit_text(
        "✏️ Введите текст автоответа:\n\n"
        "Этот текст будет отправляться в ответ на входящие личные сообщения.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AutoresponderStates.waiting_text)
async def process_autoresponder_text(message: Message, state: FSMContext, db: Database):
    text = message.text.strip()
    data = await state.get_data()
    account_id = data["account_id"]

    await db.update_autoresponder(account_id, False, text)
    await state.clear()

    account = await db.get_account(account_id)

    await message.answer(
        f"✅ Текст автоответа сохранён!\n\n"
        f"Не забудьте включить автоответчик.",
        reply_markup=autoresponder_keyboard(account_id, account.autoresponder_enabled, account.notify_messages),
    )


@router.callback_query(F.data.startswith("clear_autoresponder_history:"))
async def callback_clear_history(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])

    await db.clear_autoresponder_history(account_id)

    await callback.answer("✅ История автоответов очищена", show_alert=True)
