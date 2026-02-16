from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..database.db import Database
from ..keyboards.inline import main_menu_keyboard, back_to_menu_keyboard, admin_keyboard

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, db: Database):
    user = await db.get_or_create_user(
        message.from_user.id, message.from_user.username
    )

    text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Это бот для управления рассылками через ваши Telegram аккаунты.\n\n"
        "🔹 Добавьте аккаунты для рассылок\n"
        "🔹 Создавайте рассылки с рандомизацией текста\n"
        "🔹 Настраивайте автоответчик\n"
        "🔹 Управляйте временем активности\n\n"
        "Выберите действие:"
    )

    await message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, db: Database):
    user = await db.get_or_create_user(
        callback.from_user.id, callback.from_user.username
    )

    text = (
        "📋 Главное меню\n\n"
        "Выберите действие:"
    )

    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    text = (
        "ℹ️ Помощь\n\n"
        "📋 Мои рассылки - управление рассылками\n"
        "👤 Аккаунты - добавление и управление аккаунтами\n"
        "💳 Подписка - информация о подписке и оплата\n\n"
        "🔹 Как добавить аккаунт:\n"
        "1. Перейдите в «Аккаунты»\n"
        "2. Нажмите «Добавить аккаунт»\n"
        "3. Введите API ID и API Hash (получить на my.telegram.org)\n"
        "4. Введите номер телефона\n"
        "5. Введите код из Telegram\n\n"
        "🔹 Как создать рассылку:\n"
        "1. Перейдите в «Мои рассылки»\n"
        "2. Нажмите «Создать рассылку»\n"
        "3. Введите название и выберите аккаунт\n"
        "4. Добавьте тексты для рандомизации\n"
        "5. Добавьте целевые чаты/группы\n"
        "6. Запустите рассылку\n\n"
        "🔹 Автоответчик:\n"
        "Отвечает на входящие личные сообщения.\n"
        "Каждому пользователю отвечает только один раз."
    )

    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Действие отменено",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()
