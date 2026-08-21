import html
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..database.db import Database
from ..keyboards.inline import (
    tools_menu_keyboard,
    parser_intro_keyboard,
    parser_manage_keyboard,
    parser_view_keyboard,
    parser_delete_confirm_keyboard,
    select_account_for_parser_keyboard,
    parser_chats_keyboard,
    parser_stopwords_keyboard,
    parser_autoreply_choice_keyboard,
    cancel_keyboard,
)
from ..utils.tg import safe_answer, safe_edit
from ..utils.premium_emoji import pe
from ..services import ParserService, _PARSER_MAX_CHATS_PER_ACCOUNT, _PARSER_MAX_ACTIVE_PER_USER
from .mailings import parse_chat_link

logger = logging.getLogger(__name__)

router = Router()


class ParserSetupStates(StatesGroup):
    waiting_account = State()
    waiting_chats = State()
    waiting_keywords = State()
    waiting_stopwords = State()
    waiting_autoreply_choice = State()
    waiting_autoreply_text = State()


# === Tools menu ===

@router.callback_query(F.data == "tools")
async def callback_tools_menu(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        pe("🛠 <b>Инструменты</b>\n\nДополнительные функции для работы с аккаунтами."),
        parse_mode="HTML",
        reply_markup=tools_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "parser_intro")
async def callback_parser_intro(callback: CallbackQuery):
    await safe_edit(
        callback.message,
        pe(
            "🔎 <b>Парсер сообщений</b>\n\n"
            "Слушает выбранные чаты и присылает вам сообщения, которые "
            "совпали с заданными ключевыми словами.\n\n"
            "Как это работает:\n"
            "1️⃣ Выбираете аккаунт и чаты для отслеживания\n"
            "2️⃣ Задаёте ключевые слова — на что реагировать\n"
            "3️⃣ Задаёте стоп-слова — что игнорировать (необязательно)\n"
            "4️⃣ По желанию — автоответ в чат на совпадение (один раз на автора)\n\n"
            "Каждый найденный автор — уведомление и автоответ ровно один раз, "
            "повторно тот же человек больше не потревожит.\n\n"
            f"Лимит: до {_PARSER_MAX_ACTIVE_PER_USER} активных парсеров, "
            f"до {_PARSER_MAX_CHATS_PER_ACCOUNT} чатов на аккаунт."
        ),
        parse_mode="HTML",
        reply_markup=parser_intro_keyboard(),
    )
    await callback.answer()


# === Manage existing parsers ===

async def _render_parser_manage(callback: CallbackQuery, db: Database, page: int = 0):
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    settings_list = await db.get_user_parser_settings(user.id)
    pairs = []
    for s in settings_list:
        acc = await db.get_account(s.account_id)
        if acc:
            pairs.append((s, acc))
    active_count = sum(1 for s, _ in pairs if s.is_active)
    can_add = active_count < _PARSER_MAX_ACTIVE_PER_USER
    text = "📋 <b>Мои парсеры</b>\n\nПока не настроено ни одного." if not pairs else \
        "📋 <b>Мои парсеры</b>\n\n🟢 — активен, ⚫️ — остановлен"
    await safe_edit(callback.message, pe(text), parse_mode="HTML", reply_markup=parser_manage_keyboard(pairs, can_add, page=page))


@router.callback_query(F.data.startswith("parser_manage_page:"))
async def callback_parser_manage_page(callback: CallbackQuery, db: Database):
    page = int(callback.data.split(":")[1])
    await _render_parser_manage(callback, db, page=page)
    await callback.answer()


@router.callback_query(F.data == "parser_manage")
async def callback_parser_manage(callback: CallbackQuery, db: Database):
    await _render_parser_manage(callback, db)
    await callback.answer()


async def _render_parser_view(callback: CallbackQuery, db: Database, account_id: int) -> bool:
    settings = await db.get_parser_settings(account_id)
    account = await db.get_account_for_user(account_id, callback.from_user.id)
    if not settings or not account:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return False
    status = "🟢 активен" if settings.is_active else "⚫️ остановлен"
    autoreply = "включён" if settings.auto_reply_enabled else "выключен"
    text = pe(
        f"🔎 <b>Парсер: {html.escape(account.display_name)}</b>\n\n"
        f"Статус: {status}\n"
        f"Чатов: {len(settings.chats)}\n"
        f"Ключевые слова: {html.escape(settings.keywords or '-')}\n"
        f"Стоп-слова: {html.escape(settings.stop_words or '-')}\n"
        f"Автоответ: {autoreply}"
    )
    await safe_edit(callback.message, text, parse_mode="HTML", reply_markup=parser_view_keyboard(settings))
    return True


@router.callback_query(F.data.startswith("parser_view:"))
async def callback_parser_view(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    await _render_parser_view(callback, db, account_id)
    await callback.answer()


@router.callback_query(F.data.startswith("parser_toggle:"))
async def callback_parser_toggle(callback: CallbackQuery, db: Database, parser_service: ParserService):
    account_id = int(callback.data.split(":")[1])
    settings = await db.get_parser_settings(account_id)
    if not settings or not await db.get_account_for_user(account_id, callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    if not settings.is_active:
        user = await db.get_user(callback.from_user.id)
        active_count = await db.count_active_parsers_for_user(user.id) if user else 0
        if active_count >= _PARSER_MAX_ACTIVE_PER_USER:
            await callback.answer(
                f"Лимит {_PARSER_MAX_ACTIVE_PER_USER} активных парсеров исчерпан — отключите другой.",
                show_alert=True,
            )
            return
    await db.set_parser_active(account_id, not settings.is_active)
    await parser_service.reload_config(account_id)
    await _render_parser_view(callback, db, account_id)
    await callback.answer()


@router.callback_query(F.data.startswith("parser_delete:"))
async def callback_parser_delete(callback: CallbackQuery, db: Database):
    account_id = int(callback.data.split(":")[1])
    if not await db.get_account_for_user(account_id, callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await safe_edit(
        callback.message,
        pe("🗑️ Удалить этот парсер вместе с настройками?"),
        parse_mode="HTML",
        reply_markup=parser_delete_confirm_keyboard(account_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("parser_delete_confirm:"))
async def callback_parser_delete_confirm(callback: CallbackQuery, db: Database, parser_service: ParserService):
    account_id = int(callback.data.split(":")[1])
    if not await db.get_account_for_user(account_id, callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await db.delete_parser_settings(account_id)
    await parser_service.reload_config(account_id)
    await callback.answer("Парсер удалён")
    await _render_parser_manage(callback, db)


# === Setup wizard ===

@router.callback_query(F.data == "parser_configure")
async def callback_parser_configure(callback: CallbackQuery, state: FSMContext, db: Database):
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    active_count = await db.count_active_parsers_for_user(user.id)
    if active_count >= _PARSER_MAX_ACTIVE_PER_USER:
        await callback.answer(
            f"У вас уже {_PARSER_MAX_ACTIVE_PER_USER} активных парсера — отключите один, чтобы добавить новый.",
            show_alert=True,
        )
        return
    accounts = await db.get_user_accounts(user.id)
    existing = await db.get_user_parser_settings(user.id)
    used_account_ids = {s.account_id for s in existing}
    free_accounts = [a for a in accounts if a.id not in used_account_ids]
    if not free_accounts:
        await safe_edit(
            callback.message,
            pe(
                "❌ Нет свободного аккаунта для нового парсера.\n"
                "Добавьте аккаунт в разделе «Аккаунты» — или отключите парсер на уже занятом."
            ),
            parse_mode="HTML",
            reply_markup=parser_intro_keyboard(),
        )
        await callback.answer()
        return
    await state.set_state(ParserSetupStates.waiting_account)
    await safe_edit(
        callback.message,
        pe("🔎 Настройка парсера\n\nШаг 1/4: Выберите аккаунт, который будет слушать чаты:"),
        parse_mode="HTML",
        reply_markup=select_account_for_parser_keyboard(free_accounts),
    )
    await callback.answer()


@router.callback_query(ParserSetupStates.waiting_account, F.data.startswith("parser_select_account_page:"))
async def callback_parser_select_account_page(callback: CallbackQuery, db: Database):
    page = int(callback.data.split(":")[1])
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    accounts = await db.get_user_accounts(user.id)
    existing = await db.get_user_parser_settings(user.id)
    used_account_ids = {s.account_id for s in existing}
    free_accounts = [a for a in accounts if a.id not in used_account_ids]
    await safe_edit(
        callback.message,
        pe("🔎 Настройка парсера\n\nШаг 1/4: Выберите аккаунт, который будет слушать чаты:"),
        parse_mode="HTML",
        reply_markup=select_account_for_parser_keyboard(free_accounts, page=page),
    )
    await callback.answer()


@router.callback_query(ParserSetupStates.waiting_account, F.data.startswith("parser_select_account:"))
async def callback_parser_pick_account(callback: CallbackQuery, state: FSMContext, db: Database):
    account_id = int(callback.data.split(":")[1])
    if not await db.get_account_for_user(account_id, callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    if await db.get_parser_settings(account_id):
        await callback.answer("У этого аккаунта уже есть парсер", show_alert=True)
        return
    await state.update_data(account_id=account_id, chats=[])
    await state.set_state(ParserSetupStates.waiting_chats)
    await safe_edit(
        callback.message,
        pe(
            "Шаг 2/4: Отправьте чат для отслеживания (ссылку, @username или ID).\n"
            "Можно добавить несколько — по одному сообщению за раз."
        ),
        parse_mode="HTML",
        reply_markup=parser_chats_keyboard([]),
    )
    await callback.answer()


@router.message(ParserSetupStates.waiting_chats)
async def process_parser_chat(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    chats = list(data.get("chats", []))
    if not text:
        await message.answer(pe("❌ Отправьте текстовое сообщение."), parse_mode="HTML", reply_markup=parser_chats_keyboard(chats))
        return
    if len(chats) >= _PARSER_MAX_CHATS_PER_ACCOUNT:
        await message.answer(
            pe(f"❌ Достигнут лимит {_PARSER_MAX_CHATS_PER_ACCOUNT} чатов на аккаунт. Нажмите «Готово»."),
            parse_mode="HTML",
            reply_markup=parser_chats_keyboard(chats),
        )
        return
    parsed = parse_chat_link(text)
    target = parsed if parsed else text
    if target in chats:
        await message.answer(pe("❌ Этот чат уже добавлен."), parse_mode="HTML", reply_markup=parser_chats_keyboard(chats))
        return
    chats.append(target)
    await state.update_data(chats=chats)
    await safe_answer(
        message,
        pe(f"✅ Чат добавлен! Всего: {len(chats)}\n\nДобавьте ещё или нажмите «Готово»:"),
        parse_mode="HTML",
        reply_markup=parser_chats_keyboard(chats),
    )


@router.callback_query(ParserSetupStates.waiting_chats, F.data.startswith("parser_chat_del:"))
async def callback_parser_chat_del(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    chats = list(data.get("chats", []))
    if 0 <= idx < len(chats):
        chats.pop(idx)
        await state.update_data(chats=chats)
    await safe_edit(
        callback.message,
        pe(f"Шаг 2/4: Отправьте чат для отслеживания.\nВсего добавлено: {len(chats)}"),
        parse_mode="HTML",
        reply_markup=parser_chats_keyboard(chats),
    )
    await callback.answer()


@router.callback_query(ParserSetupStates.waiting_chats, F.data == "parser_chats_done")
async def callback_parser_chats_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chats = data.get("chats", [])
    if not chats:
        await callback.answer("Добавьте хотя бы один чат", show_alert=True)
        return
    await state.set_state(ParserSetupStates.waiting_keywords)
    await safe_edit(
        callback.message,
        pe("Шаг 3/4: Введите ключевые слова через запятую — сообщение должно содержать хотя бы одно из них:"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(ParserSetupStates.waiting_keywords)
async def process_parser_keywords(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    keywords = [w.strip() for w in text.split(",") if w.strip()]
    if not keywords:
        await message.answer(
            pe("❌ Введите хотя бы одно ключевое слово через запятую."),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return
    await state.update_data(keywords=", ".join(keywords))
    await state.set_state(ParserSetupStates.waiting_stopwords)
    await safe_answer(
        message,
        pe(
            "Шаг 4/4: Введите стоп-слова через запятую (сообщения с ними будут "
            "игнорироваться) — или нажмите «Пропустить»:"
        ),
        parse_mode="HTML",
        reply_markup=parser_stopwords_keyboard(),
    )


async def _ask_autoreply(message: Message, state: FSMContext, edit: bool):
    await state.set_state(ParserSetupStates.waiting_autoreply_choice)
    text = pe("Отправлять автоответ в чат при совпадении? Уходит только один раз на каждого автора.")
    markup = parser_autoreply_choice_keyboard()
    if edit:
        await safe_edit(message, text, parse_mode="HTML", reply_markup=markup)
    else:
        await safe_answer(message, text, parse_mode="HTML", reply_markup=markup)


@router.message(ParserSetupStates.waiting_stopwords)
async def process_parser_stopwords(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    stop_words = [w.strip() for w in text.split(",") if w.strip()]
    await state.update_data(stop_words=", ".join(stop_words) if stop_words else None)
    await _ask_autoreply(message, state, edit=False)


@router.callback_query(ParserSetupStates.waiting_stopwords, F.data == "parser_stopwords_skip")
async def callback_parser_stopwords_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(stop_words=None)
    await _ask_autoreply(callback.message, state, edit=True)
    await callback.answer()


async def _finalize_parser_setup(
    target, state: FSMContext, db: Database, parser_service: ParserService,
    user_telegram_id: int, auto_reply_enabled: bool, auto_reply_text: Optional[str],
    edit: bool,
):
    data = await state.get_data()
    account_id = data.get("account_id")
    chats = data.get("chats") or []
    keywords = data.get("keywords")
    stop_words = data.get("stop_words")
    user = await db.get_user(user_telegram_id)
    await state.clear()

    if not account_id or not chats or not keywords or not user:
        text = pe("❌ Сессия устарела. Настройте парсер заново.")
        if edit:
            await safe_edit(target, text, parse_mode="HTML", reply_markup=tools_menu_keyboard())
        else:
            await safe_answer(target, text, parse_mode="HTML", reply_markup=tools_menu_keyboard())
        return

    interim = pe("⏳ Сохраняю парсер и подключаюсь к чатам...")
    if edit:
        await safe_edit(target, interim, parse_mode="HTML")
    else:
        await safe_answer(target, interim, parse_mode="HTML")

    await db.create_parser_settings(account_id, user.id, chats, keywords, stop_words, auto_reply_enabled, auto_reply_text)
    await parser_service.reload_config(account_id)
    failed = await parser_service.join_chats(account_id, chats)

    summary = f"✅ <b>Парсер настроен и запущен!</b>\n\nЧатов: {len(chats)}\nКлючевые слова: {html.escape(keywords)}"
    if failed:
        summary += (
            f"\n\n⚠️ Не удалось зайти в {len(failed)} чат(ов) — добавьте аккаунт вручную:\n"
            + "\n".join(html.escape(f) for f in failed[:10])
        )
    await safe_answer(target, pe(summary), parse_mode="HTML", reply_markup=parser_intro_keyboard())


@router.callback_query(ParserSetupStates.waiting_autoreply_choice, F.data == "parser_autoreply_no")
async def callback_parser_autoreply_no(callback: CallbackQuery, state: FSMContext, db: Database, parser_service: ParserService):
    await callback.answer()
    await _finalize_parser_setup(callback.message, state, db, parser_service, callback.from_user.id, False, None, edit=True)


@router.callback_query(ParserSetupStates.waiting_autoreply_choice, F.data == "parser_autoreply_yes")
async def callback_parser_autoreply_yes(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ParserSetupStates.waiting_autoreply_text)
    await safe_edit(
        callback.message,
        pe("Введите текст автоответа (уйдёт в чат один раз на каждого автора совпадения):"),
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(ParserSetupStates.waiting_autoreply_text)
async def process_parser_autoreply_text(message: Message, state: FSMContext, db: Database, parser_service: ParserService):
    text = (message.text or "").strip()
    if not text:
        await message.answer(pe("❌ Отправьте текст автоответа."), parse_mode="HTML", reply_markup=cancel_keyboard())
        return
    await _finalize_parser_setup(message, state, db, parser_service, message.from_user.id, True, text, edit=False)
