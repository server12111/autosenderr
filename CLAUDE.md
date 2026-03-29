# feAutoSender — Claude Code Guidelines

## Запуск бота
```bash
python -m bot.main
```
Лог пишеться у `bot_output.log`.

Щоб зупинити всі процеси Python:
```bash
powershell -Command "Get-Process python | Stop-Process -Force"
```

## Стек
- **aiogram 3.x** — Telegram Bot API (адмін-панель)
- **Telethon 1.34+** — userbot-клієнти для розсилок
- **SQLite** (aiosqlite) — база даних (`data/bot.db`)

## Структура
```
bot/
  config.py          — конфіг із .env
  database/db.py     — Database клас, всі моделі, міграції
  handlers/
    start.py         — /start, main_menu, cancel, help
    accounts.py      — додавання/управління акаунтами
    mailings.py      — створення/редагування розсилок
    admin.py         — адмін-команди
  keyboards/inline.py — всі InlineKeyboard функції
  userbot/manager.py  — UserbotManager, підключення Telethon клієнтів
  services.py         — MailingService, AutoresponderService, CryptoBotService і т.д.
  middlewares/        — SubscriptionMiddleware, AlbumMiddleware
```

## Ключові рішення

### Device spoofing (бан-профілактика)
`_DEVICE_POOL` у `bot/userbot/manager.py` — Telethon підключається як Android-пристрій, не як бібліотека.

### Entities + Premium emoji
Повідомлення зберігаються як `text` (raw) + `entities_json` (JSON-серіалізовані aiogram entities).
При відправці `_build_telethon_entities()` у `services.py` конвертує JSON → Telethon об'єкти і передає через `formatting_entities=`.

### Флоу додавання акаунту (єдиний покроковий)
`AddAccountStates` у `accounts.py`:
1. `waiting_proxy` — опційно (socks5://...)
2. `waiting_api_id` / `waiting_api_hash` — опційно
3. `waiting_phone` → `waiting_code` → `waiting_password` (2FA)

### Forward-розсилка
`MailingMessage.is_forward` → `client.forward_messages()` замість `send_message`.

## БД міграції
Патерн `try/except ALTER TABLE` у `_run_migrations()` в `db.py`. Нові колонки додавати там.

## Мова інтерфейсу
Весь текст бота — **російська мова**.
