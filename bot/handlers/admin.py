import io
import os
import shutil
import tempfile
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiosqlite

from ..database.db import Database
from ..keyboards.inline import (
    admin_keyboard,
    admin_stats_period_keyboard,
    admin_promocodes_keyboard,
    admin_promo_list_keyboard,
    admin_settings_keyboard,
    admin_channels_keyboard,
    admin_withdrawals_keyboard,
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
    waiting_price_7d = State()
    waiting_price_30d = State()
    waiting_ref_percent = State()
    waiting_min_withdraw = State()
    waiting_channel_id = State()
    waiting_card_manager = State()
    waiting_db_file = State()


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔧 Админ-панель\n\nВыберите действие:",
        reply_markup=admin_keyboard(),
    )


_PERIOD_LABELS = {
    "day": "День",
    "week": "Неделя",
    "month": "Месяц",
    "year": "Год",
}


def _build_chart_image(data: list, title: str) -> io.BytesIO:
    labels = [d[0] for d in data] or ["—"]
    values = [d[1] for d in data] or [0]
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#1e1e2e")
    ax.set_facecolor("#1e1e2e")
    bars = ax.bar(range(len(labels)), values, color="#7c9eff", width=0.6)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", color="white", fontsize=8)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_color("#444")
    ax.set_title(title, color="white", fontsize=12, pad=10)
    ax.set_ylabel("Пользователи", color="white")
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.1,
                str(val),
                ha="center", va="bottom", color="white", fontsize=8,
            )
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


async def _send_stats(callback: CallbackQuery, db: Database, period: str, bot):
    users = await db.get_all_users()
    total_users = len(users)
    now = datetime.now()
    active_subs = sum(1 for u in users if u.subscription_end and u.subscription_end > now)
    accounts = await db.get_all_active_accounts()
    mailings = await db.get_active_mailings()
    total_mailings = await db.count_all_mailings()
    revenue = await db.get_revenue_by_currency()
    paid_subs = await db.count_paid_subscriptions()

    revenue_parts = [f"{amt:.2f} {cur}" for cur, amt in revenue.items() if amt > 0]
    revenue_line = " | ".join(revenue_parts) if revenue_parts else "0.00 USDT"

    chart_data = await db.get_registrations_by_period(period)
    period_label = _PERIOD_LABELS.get(period, period)
    chart_buf = _build_chart_image(chart_data, f"Новые пользователи — {period_label}")

    caption = (
        f"📊 <b>Статистика бота</b> — {period_label}\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"✅ Активных подписок: <b>{active_subs}</b>\n"
        f"💰 Продано подписок: <b>{paid_subs}</b>\n"
        f"💵 Доход: <b>{revenue_line}</b>\n\n"
        f"📱 Аккаунтов: <b>{len(accounts)}</b>\n"
        f"📋 Активных рассылок: <b>{len(mailings)}</b>\n"
        f"📋 Всего рассылок: <b>{total_mailings}</b>"
    )

    photo = BufferedInputFile(chart_buf.read(), filename="stats.png")
    keyboard = admin_stats_period_keyboard(active=period)

    await callback.message.delete()
    await bot.send_photo(
        chat_id=callback.from_user.id,
        photo=photo,
        caption=caption,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery, db: Database, bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _send_stats(callback, db, "day", bot)


@router.callback_query(F.data.startswith("admin_stats:"))
async def callback_admin_stats_period(callback: CallbackQuery, db: Database, bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    period = callback.data.split(":")[1]
    if period not in _PERIOD_LABELS:
        await callback.answer()
        return
    await callback.answer()
    await _send_stats(callback, db, period, bot)


@router.callback_query(F.data == "admin_broadcast")
async def callback_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text(
        "📢 Рассылка всем пользователям\n\nВведите текст сообщения или отправьте фото с подписью:",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    has_photo = bool(message.photo)
    if not has_photo and not message.text:
        await message.answer("❌ Отправьте текст или фото.", reply_markup=cancel_keyboard())
        return

    users = await db.get_all_users()
    sent = 0
    failed = 0

    status_msg = await message.answer("⏳ Рассылка...")
    for user in users:
        try:
            if has_photo:
                await message.bot.send_photo(
                    user.telegram_id,
                    message.photo[-1].file_id,
                    caption=message.caption or None,
                )
            else:
                await message.bot.send_message(user.telegram_id, message.text)
            sent += 1
        except Exception:
            failed += 1

    await state.clear()
    await status_msg.edit_text(
        f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}",
        reply_markup=admin_keyboard(),
    )


# === Promocodes ===

@router.callback_query(F.data == "admin_promocodes")
async def callback_admin_promocodes(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🎟 Управление промокодами\n\nВыберите действие:",
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
        "➕ Создание промокода\n\nВведите текст промокода:",
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
        f"Промокод: <b>{code}</b>\n\nВведите количество дней подписки:",
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
        await message.answer("❌ Введите число. Попробуйте снова:", reply_markup=cancel_keyboard())
        return
    if days <= 0:
        await message.answer("❌ Количество дней должно быть больше 0.", reply_markup=cancel_keyboard())
        return
    await state.update_data(promo_days=days)
    await state.set_state(AdminStates.waiting_promo_max_uses)
    data = await state.get_data()
    await message.answer(
        f"Промокод: <b>{data['promo_code']}</b>\n"
        f"Дней подписки: {days}\n\n"
        "Введите количество использований:",
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
        await message.answer("❌ Введите число. Попробуйте снова:", reply_markup=cancel_keyboard())
        return
    if max_uses <= 0:
        await message.answer("❌ Количество использований должно быть больше 0.", reply_markup=cancel_keyboard())
        return

    data = await state.get_data()
    code = data["promo_code"]
    days = data["promo_days"]
    await db.create_promocode(code, days, max_uses)
    await state.clear()

    uses_text = f"{max_uses}x" if max_uses > 1 else "одноразовый"
    await message.answer(
        f"✅ Промокод создан!\n\nКод: <b>{code}</b>\nДней подписки: {days}\nИспользований: {uses_text}",
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
            "🎟 Список промокодов\n\nПромокодов пока нет.",
            reply_markup=admin_promocodes_keyboard(),
        )
        await callback.answer()
        return

    text = "🎟 Список промокодов:\n\n"
    for promo in promocodes:
        status = "✅ Исчерпан" if promo.uses_count >= promo.max_uses else f"🟢 {promo.uses_count}/{promo.max_uses}"
        text += f"<b>{promo.code}</b> — {promo.duration_days} дн. — {status}\n"

    await callback.message.edit_text(text, reply_markup=admin_promo_list_keyboard(promocodes))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_promo:"))
async def callback_admin_delete_promo(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(callback.data.split(":")[1])
    await db.delete_promocode(promo_id)
    await callback.answer("✅ Промокод удалён")

    promocodes = await db.get_all_promocodes()
    if not promocodes:
        await callback.message.edit_text(
            "🎟 Список промокодов\n\nПромокодов пока нет.",
            reply_markup=admin_promocodes_keyboard(),
        )
        return

    text = "🎟 Список промокодов:\n\n"
    for promo in promocodes:
        status = "✅ Исчерпан" if promo.uses_count >= promo.max_uses else f"🟢 {promo.uses_count}/{promo.max_uses}"
        text += f"<b>{promo.code}</b> — {promo.duration_days} дн. — {status}\n"
    await callback.message.edit_text(text, reply_markup=admin_promo_list_keyboard(promocodes))


# === Settings panel ===

@router.callback_query(F.data == "admin_settings")
async def callback_admin_settings(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    price_7d = await db.get_price(7)
    price_30d = await db.get_price(30)
    ref_percent = await db.get_ref_percent()
    min_withdraw = await db.get_ref_min_withdraw()
    card_manager = await db.get_setting("card_manager_username") or "autosenderkarta"
    text = (
        "⚙️ Настройки бота\n\n"
        f"💰 Цена подписки 7 дней: {price_7d} USDT\n"
        f"💰 Цена подписки 30 дней: {price_30d} USDT\n"
        f"🤝 Реферальный процент: {ref_percent}%\n"
        f"📤 Минимум вывода реф. баланса: {min_withdraw} USDT\n"
        f"💳 Менеджер (оплата картой): @{card_manager}\n"
    )
    await callback.message.edit_text(text, reply_markup=admin_settings_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_set_price_7d")
async def callback_admin_set_price_7d(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_price_7d)
    await callback.message.edit_text(
        "💰 Введите новую цену подписки на 7 дней (USDT):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_price_7d)
async def process_price_7d(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную сумму:", reply_markup=cancel_keyboard())
        return
    await db.set_price(7, price)
    await state.clear()
    await message.answer(f"✅ Цена на 7 дней обновлена: {price} USDT", reply_markup=admin_settings_keyboard())


@router.callback_query(F.data == "admin_set_price_30d")
async def callback_admin_set_price_30d(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_price_30d)
    await callback.message.edit_text(
        "💰 Введите новую цену подписки на 30 дней (USDT):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_price_30d)
async def process_price_30d(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        price = float(message.text.strip().replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную сумму:", reply_markup=cancel_keyboard())
        return
    await db.set_price(30, price)
    await state.clear()
    await message.answer(f"✅ Цена на 30 дней обновлена: {price} USDT", reply_markup=admin_settings_keyboard())


@router.callback_query(F.data == "admin_set_ref_percent")
async def callback_admin_set_ref_percent(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_ref_percent)
    await callback.message.edit_text(
        "🤝 Введите реферальный процент (например: 10):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_ref_percent)
async def process_ref_percent(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        pct = float(message.text.strip().replace(",", "."))
        if pct < 0 or pct > 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите число от 0 до 100:", reply_markup=cancel_keyboard())
        return
    await db.set_setting("ref_percent", str(pct))
    await state.clear()
    await message.answer(f"✅ Реферальный процент обновлён: {pct}%", reply_markup=admin_settings_keyboard())


@router.callback_query(F.data == "admin_set_min_withdraw")
async def callback_admin_set_min_withdraw(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_min_withdraw)
    await callback.message.edit_text(
        "📤 Введите минимальную сумму для вывода реферального баланса (USDT):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_min_withdraw)
async def process_min_withdraw(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную сумму:", reply_markup=cancel_keyboard())
        return
    await db.set_setting("ref_min_withdraw", str(amount))
    await state.clear()
    await message.answer(f"✅ Минимум для вывода обновлён: {amount} USDT", reply_markup=admin_settings_keyboard())


@router.callback_query(F.data == "admin_set_card_manager")
async def callback_admin_set_card_manager(callback: CallbackQuery, state: FSMContext, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    current = await db.get_setting("card_manager_username") or "autosenderkarta"
    await state.set_state(AdminStates.waiting_card_manager)
    await callback.message.edit_text(
        f"💳 Текущий менеджер для оплаты картой: @{current}\n\n"
        "Введите новый юзернейм (без @):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_card_manager)
async def process_card_manager(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    username = message.text.strip().lstrip("@")
    if not username:
        await message.answer("❌ Введите корректный юзернейм:", reply_markup=cancel_keyboard())
        return
    await db.set_setting("card_manager_username", username)
    await state.clear()
    await message.answer(
        f"✅ Менеджер обновлён: @{username}",
        reply_markup=admin_settings_keyboard(),
    )


# === Required channels management ===

@router.callback_query(F.data == "admin_channels")
async def callback_admin_channels(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    channels = await db.get_required_channels()
    text = "📡 Обязательные каналы\n\n"
    if channels:
        for ch in channels:
            text += f"• {ch.channel_title} (@{ch.channel_username or ch.channel_id})\n"
    else:
        text += "Обязательных каналов нет.\n"
    text += "\nДобавьте каналы, на которые пользователи должны подписаться."
    await callback.message.edit_text(text, reply_markup=admin_channels_keyboard(channels))
    await callback.answer()


@router.callback_query(F.data == "admin_add_channel")
async def callback_admin_add_channel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_channel_id)
    await callback.message.edit_text(
        "📡 Добавление канала\n\n"
        "Перешлите любое сообщение из канала или введите данные в формате:\n"
        "<code>ID|@username|Название</code>\n\n"
        "Пример: <code>-1001234567890|@mychannel|Мой канал</code>\n\n"
        "Или просто добавьте бота в канал как администратора и введите:\n"
        "<code>ID канала</code>",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_channel_id)
async def process_channel_id(message: Message, state: FSMContext, db: Database):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    # Handle forwarded message from channel
    origin = message.forward_origin
    if origin and hasattr(origin, "chat"):
        chat = origin.chat
        channel_id = chat.id
        channel_username = chat.username or ""
        channel_title = chat.title or str(channel_id)
        await db.add_required_channel(channel_id, channel_username, channel_title)
        await state.clear()
        channels = await db.get_required_channels()
        await message.answer(
            f"✅ Канал добавлен: {channel_title}",
            reply_markup=admin_channels_keyboard(channels),
        )
        return

    # Handle manual input: ID|@username|Title
    text = message.text.strip()
    parts = text.split("|")
    if len(parts) >= 3:
        try:
            channel_id = int(parts[0].strip())
            channel_username = parts[1].strip().lstrip("@")
            channel_title = parts[2].strip()
            await db.add_required_channel(channel_id, channel_username, channel_title)
            await state.clear()
            channels = await db.get_required_channels()
            await message.answer(
                f"✅ Канал добавлен: {channel_title}",
                reply_markup=admin_channels_keyboard(channels),
            )
            return
        except ValueError:
            pass

    # Try just ID
    try:
        channel_id = int(text)
        try:
            chat = await message.bot.get_chat(channel_id)
            channel_username = chat.username or ""
            channel_title = chat.title or str(channel_id)
        except Exception:
            channel_username = ""
            channel_title = str(channel_id)
        await db.add_required_channel(channel_id, channel_username, channel_title)
        await state.clear()
        channels = await db.get_required_channels()
        await message.answer(
            f"✅ Канал добавлен: {channel_title}",
            reply_markup=admin_channels_keyboard(channels),
        )
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Перешлите сообщение из канала или введите ID канала:",
            reply_markup=cancel_keyboard(),
        )


@router.callback_query(F.data.startswith("admin_del_channel:"))
async def callback_admin_del_channel(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    channel_id = int(callback.data.split(":")[1])
    await db.remove_required_channel(channel_id)
    await callback.answer("✅ Канал удалён")
    channels = await db.get_required_channels()
    text = "📡 Обязательные каналы\n\n"
    if channels:
        for ch in channels:
            text += f"• {ch.channel_title} (@{ch.channel_username or ch.channel_id})\n"
    else:
        text += "Обязательных каналов нет.\n"
    await callback.message.edit_text(text, reply_markup=admin_channels_keyboard(channels))


# === Withdrawal requests ===

@router.callback_query(F.data == "admin_withdrawals")
async def callback_admin_withdrawals(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    requests = await db.get_withdrawal_requests("pending")
    text = "💸 Запросы на вывод\n\n"
    if requests:
        for req in requests:
            user = await db.get_user_by_id(req.user_id)
            username = f"@{user.username}" if user and user.username else str(req.user_id)
            text += f"• {username} — {req.amount:.2f} USDT\n  Кошелёк: <code>{req.wallet}</code>\n\n"
    else:
        text += "Активных запросов нет."
    await callback.message.edit_text(text, reply_markup=admin_withdrawals_keyboard(requests))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_approve_withdraw:"))
async def callback_approve_withdraw(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split(":")[1])
    await db.update_withdrawal_status(req_id, "approved")
    await callback.answer("✅ Заявка одобрена")

    requests = await db.get_withdrawal_requests("pending")
    text = "💸 Запросы на вывод\n\n"
    if requests:
        for req in requests:
            user = await db.get_user_by_id(req.user_id)
            username = f"@{user.username}" if user and user.username else str(req.user_id)
            text += f"• {username} — {req.amount:.2f} USDT\n  Кошелёк: <code>{req.wallet}</code>\n\n"
    else:
        text += "Активных запросов нет."
    await callback.message.edit_text(text, reply_markup=admin_withdrawals_keyboard(requests))


@router.callback_query(F.data.startswith("admin_decline_withdraw:"))
async def callback_decline_withdraw(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split(":")[1])

    # Get request details to refund balance
    req = None
    all_requests = await db.get_withdrawal_requests("pending")
    for r in all_requests:
        if r.id == req_id:
            req = r
            break

    await db.update_withdrawal_status(req_id, "declined")

    # Refund the balance if request found
    if req:
        await db.add_ref_balance(req.user_id, req.amount)

    await callback.answer("❌ Заявка отклонена, баланс возвращён")

    requests = await db.get_withdrawal_requests("pending")
    text = "💸 Запросы на вывод\n\n"
    if requests:
        for r in requests:
            user = await db.get_user_by_id(r.user_id)
            username = f"@{user.username}" if user and user.username else str(r.user_id)
            text += f"• {username} — {r.amount:.2f} USDT\n  Кошелёк: <code>{r.wallet}</code>\n\n"
    else:
        text += "Активных запросов нет."
    await callback.message.edit_text(text, reply_markup=admin_withdrawals_keyboard(requests))


@router.callback_query(F.data == "admin_back")
async def callback_admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 Админ-панель\n\nВыберите действие:",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_export_db")
async def callback_admin_export_db(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("⏳ Создаю резервную копию БД...")
    tmp = tempfile.mktemp(suffix=".db")
    try:
        async with aiosqlite.connect(db.db_path) as src:
            await src.execute(f"VACUUM INTO '{tmp}'")
        await callback.message.answer_document(
            FSInputFile(tmp, filename="bot_backup.db"),
            caption=f"📦 Резервная копия БД\n🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        )
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@router.callback_query(F.data == "admin_import_db")
async def callback_admin_import_db(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_db_file)
    await callback.message.answer(
        "📥 Отправьте файл базы данных (.db)\n\n"
        "⚠️ Текущая база будет полностью заменена!",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_db_file, F.document)
async def process_import_db(message: Message, state: FSMContext, db: Database):
    doc = message.document
    if not doc.file_name or not doc.file_name.endswith(".db"):
        await message.answer("❌ Файл должен иметь расширение .db")
        return

    tmp = tempfile.mktemp(suffix=".db")
    try:
        from io import BytesIO
        file_info = await message.bot.get_file(doc.file_id)
        buf = BytesIO()
        await message.bot.download_file(file_info.file_path, destination=buf)
        with open(tmp, "wb") as f:
            f.write(buf.getvalue())

        # Проверяем magic bytes SQLite
        with open(tmp, "rb") as f:
            header = f.read(16)
        if b"SQLite format 3" not in header:
            await message.answer(f"❌ Файл не является базой данных SQLite.\nПолучено: {header[:16]}")
            return

        await state.clear()
        await message.answer("⏳ Заменяю базу данных...")

        await db.close()

        # Удаляем WAL файлы если есть
        for ext in ("", "-shm", "-wal"):
            path = db.db_path + ext
            if os.path.exists(path):
                os.remove(path)

        shutil.copy2(tmp, db.db_path)
        await db.connect()

        await message.answer(
            "✅ База данных успешно заменена!\n"
            "Все данные обновлены.",
            reply_markup=admin_keyboard(),
        )
    except Exception as e:
        # Если что-то пошло не так — пробуем переподключиться к старой/новой БД
        try:
            await db.connect()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка при импорте: {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@router.callback_query(F.data == "admin_cleanup_accounts")
async def callback_admin_cleanup_accounts(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    count = await db.count_inactive_accounts()

    if count == 0:
        await callback.message.edit_text(
            "✅ Мёртвых аккаунтов нет — база чистая.",
            reply_markup=admin_keyboard(),
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"🗑 Удалить {count} аккаунтов", callback_data="admin_cleanup_accounts_confirm"),
        InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel"),
    )

    await callback.message.edit_text(
        f"⚠️ <b>Очистка мёртвых аккаунтов</b>\n\n"
        f"Найдено неактивных аккаунтов: <b>{count}</b>\n\n"
        f"Это аккаунты с истёкшими сессиями, забаненные или использованные с двух IP.\n"
        f"Они будут <b>удалены из базы навсегда</b>.\n\n"
        f"Подтвердить удаление?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_cleanup_accounts_confirm")
async def callback_admin_cleanup_accounts_confirm(callback: CallbackQuery, db: Database):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    deleted = await db.purge_inactive_accounts()
    await callback.message.edit_text(
        f"✅ Удалено <b>{deleted}</b> мёртвых аккаунтов.\n\n"
        f"База данных очищена. При следующем перезапуске бот стартует быстрее.",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()
