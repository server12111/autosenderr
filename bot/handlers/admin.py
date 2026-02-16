from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    admin_keyboard,
    admin_promocodes_keyboard,
    admin_promo_list_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
)
from ..config import config

router = Router()


class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_promo_code = State()
    waiting_promo_days = State()
    waiting_promo_max_uses = State()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🔧 Админ-панель\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    users = await db.get_all_users()
    total_users = len(users)

    from datetime import datetime
    active_subs = sum(
        1 for u in users
        if u.subscription_end and u.subscription_end > datetime.now()
    )

    accounts = await db.get_all_active_accounts()
    total_accounts = len(accounts)

    mailings = await db.get_active_mailings()
    active_mailings = len(mailings)

    text = (
        "📊 Статистика бота\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных подписок: {active_subs}\n"
        f"📱 Аккаунтов: {total_accounts}\n"
        f"📋 Активных рассылок: {active_mailings}\n"
    )

    await callback.message.edit_text(text, reply_markup=admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast")
async def callback_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_broadcast)

    await callback.message.edit_text(
        "📢 Рассылка всем пользователям\n\n"
        "Введите текст сообщения:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    text = message.text.strip()
    users = await db.get_all_users()

    sent = 0
    failed = 0

    status_msg = await message.answer("⏳ Рассылка...")

    for user in users:
        try:
            await message.bot.send_message(user.telegram_id, text)
            sent += 1
        except Exception:
            failed += 1

    await state.clear()

    await status_msg.edit_text(
        f"✅ Рассылка завершена\n\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_promocodes")
async def callback_admin_promocodes(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🎟 Управление промокодами\n\n"
        "Выберите действие:",
        reply_markup=admin_promocodes_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_create_promo")
async def callback_admin_create_promo(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_promo_code)
    await callback.message.edit_text(
        "➕ Создание промокода\n\n"
        "Введите текст промокода:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_promo_code)
async def process_promo_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    code = message.text.strip()
    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.waiting_promo_days)

    await message.answer(
        f"Промокод: <b>{code}</b>\n\n"
        "Введите количество дней подписки:",
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminStates.waiting_promo_days)
async def process_promo_days(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Введите число. Попробуйте снова:",
            reply_markup=cancel_keyboard(),
        )
        return

    if days <= 0:
        await message.answer(
            "❌ Количество дней должно быть больше 0. Попробуйте снова:",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.update_data(promo_days=days)
    await state.set_state(AdminStates.waiting_promo_max_uses)

    data = await state.get_data()
    code = data["promo_code"]

    await message.answer(
        f"Промокод: <b>{code}</b>\n"
        f"Дней подписки: {days}\n\n"
        "Введите количество использований (например, 1 — одноразовый, 10 — на 10 человек):",
        reply_markup=cancel_keyboard(),
    )


@router.message(AdminStates.waiting_promo_max_uses)
async def process_promo_max_uses(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    try:
        max_uses = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Введите число. Попробуйте снова:",
            reply_markup=cancel_keyboard(),
        )
        return

    if max_uses <= 0:
        await message.answer(
            "❌ Количество использований должно быть больше 0. Попробуйте снова:",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    code = data["promo_code"]
    days = data["promo_days"]

    await db.create_promocode(code, days, max_uses)
    await state.clear()

    uses_text = f"{max_uses}x" if max_uses > 1 else "одноразовый"

    await message.answer(
        f"✅ Промокод создан!\n\n"
        f"Код: <b>{code}</b>\n"
        f"Дней подписки: {days}\n"
        f"Использований: {uses_text}",
        reply_markup=admin_promocodes_keyboard(),
    )


@router.callback_query(F.data == "admin_list_promos")
async def callback_admin_list_promos(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    promocodes = await db.get_all_promocodes()

    if not promocodes:
        await callback.message.edit_text(
            "🎟 Список промокодов\n\n"
            "Промокодов пока нет.",
            reply_markup=admin_promocodes_keyboard(),
        )
        await callback.answer()
        return

    text = "🎟 Список промокодов:\n\n"
    for promo in promocodes:
        if promo.uses_count >= promo.max_uses:
            status = "✅ Исчерпан"
        else:
            status = f"🟢 {promo.uses_count}/{promo.max_uses}"
        text += f"<b>{promo.code}</b> — {promo.duration_days} дн. — {status}\n"

    await callback.message.edit_text(
        text,
        reply_markup=admin_promo_list_keyboard(promocodes),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_promo:"))
async def callback_admin_delete_promo(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    promo_id = int(callback.data.split(":")[1])
    await db.delete_promocode(promo_id)

    await callback.answer("✅ Промокод удалён")

    # Refresh the list
    promocodes = await db.get_all_promocodes()

    if not promocodes:
        await callback.message.edit_text(
            "🎟 Список промокодов\n\n"
            "Промокодов пока нет.",
            reply_markup=admin_promocodes_keyboard(),
        )
        return

    text = "🎟 Список промокодов:\n\n"
    for promo in promocodes:
        if promo.uses_count >= promo.max_uses:
            status = "✅ Исчерпан"
        else:
            status = f"🟢 {promo.uses_count}/{promo.max_uses}"
        text += f"<b>{promo.code}</b> — {promo.duration_days} дн. — {status}\n"

    await callback.message.edit_text(
        text,
        reply_markup=admin_promo_list_keyboard(promocodes),
    )


@router.callback_query(F.data == "admin_back")
async def callback_admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🔧 Админ-панель\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()
