# Kirkalab Telegram bot

Клиент к Kirkalab API в Telegram: inline-меню в стиле Rapira, вход по QR
(deep-link) и классические команды. Построен на
[aiogram](https://aiogram.dev/) 3.x и `httpx`.

## Навигация (inline-меню)

`/start` без параметров и `/menu` показывают главное меню:

- 👤 **Профиль** — данные аккаунта (через `/api/v1/auth/me`)
- 🧮 **Калькулятор ASIC** — 🚧 заглушка «Скоро будет доступно»
- 📊 **Мои отчёты** — 🚧 заглушка
- 💎 **Тариф** — 🚧 заглушка
- ℹ️ **Помощь** — справка и список команд

Новые пункты добавляются в `MAIN_MENU_ITEMS` (`bot/keyboards.py`); готовые
обрабатываются в `bot/handlers/menu.py`, незавершённые попадают в заглушку.

## QR-вход (deep-link)

1. Сайт вызывает `POST /api/v1/auth/qr/start` и показывает QR со ссылкой
   `https://t.me/<bot>?start=qr_<session_id>`.
2. Пользователь сканирует → Telegram открывает бота с `/start qr_<session_id>`.
3. Бот показывает кнопки **✅ Подтвердить вход** / **❌ Отклонить**.
4. При подтверждении бот вызывает `POST /api/v1/auth/qr/approve` с заголовком
   `X-Bot-Secret` и телом `{"session_id", "telegram_user_id"}`.
5. Сайт, опрашивая `/status/{session_id}`, получает токены.

Истёкшие/использованные сессии (404/409) показываются понятным сообщением.

## Команды

- `/start` — приветствие и главное меню (`/start qr_<id>` — QR-вход)
- `/menu` — главное меню
- `/register` — регистрация (email, логин, пароль)
- `/login` — вход по email; JWT хранится в памяти процесса
- `/me` — профиль
- `/logout` — выход
- `/health` — статус API
- `/cancel` — отмена текущего действия

## Конфигурация

Значения читаются из переменных окружения (или локального `.env`).
Скопируйте `bot/.env.example` в `bot/.env` и заполните. Реальный `.env`
никогда не коммитится.

| Переменная | Описание | По умолчанию |
| --- | --- | --- |
| `BOT_TOKEN` | Токен от @BotFather (обязателен) | — |
| `BOT_USERNAME` | Публичный username бота (без @) | `roibot_ai_bot` |
| `BOT_INTERNAL_SECRET` | Секрет для `X-Bot-Secret` при approve QR; должен совпадать с API | — |
| `API_BASE_URL` | Базовый URL Kirkalab API | `http://app:8000` |
| `REQUEST_TIMEOUT` | Таймаут запросов к API, сек | `10` |

## Запуск локально

```bash
pip install -r bot/requirements.txt
export BOT_TOKEN=...
export BOT_INTERNAL_SECRET=...
export API_BASE_URL=http://localhost:8000
python -m bot.main
```

## Запуск в Docker Compose

Бот подключён в dev-окружении `docker-compose.yml` как сервис `bot`
(зависит от `app`):

```bash
BOT_TOKEN=... docker compose up -d bot
```

> ⚠️ В `docker-compose.prod.yml` сервиса `bot` пока **нет** — на проде бот
> не разворачивается этим стеком. Добавление prod-сервиса вынесено за рамки
> данного PR.

## Заметки

- JWT-токены хранятся только в памяти процесса по Telegram user id и не
  пишутся на диск.
- Никакие секреты не коммитятся в репозиторий.

## Архитектура

```
bot/
  config.py            # настройки из окружения (pydantic-settings)
  api_client.py        # async-клиент к API (health/register/login/me/approve_qr)
  deep_link.py         # parse_qr_payload — без зависимости от aiogram
  keyboards.py         # inline-клавиатуры (главное меню, QR-подтверждение)
  main.py              # точка входа, long-polling, сборка роутеров
  handlers/
    __init__.py        # порядок включения роутеров
    tokens.py          # общий in-memory стор JWT
    qr.py              # QR deep-link: подтверждение/отклонение входа
    menu.py            # /start, /menu и menu:* callbacks
    account.py         # /register /login /me /logout /health /cancel
```
