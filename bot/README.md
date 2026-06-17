# Kirkalab Telegram bot

The first client for the Kirkalab API. It lets users register, sign in and
view their profile directly from Telegram, using the existing
`/api/v1` endpoints. Built with [aiogram](https://aiogram.dev/) 3.x and
`httpx`.

## Commands

- `/start` - greeting and command overview
- `/register` - guided registration (email, handle, password)
- `/login` - guided login; stores a JWT in memory for the session
- `/me` - show the authenticated user's profile
- `/logout` - drop the in-memory token
- `/health` - check API availability
- `/cancel` - abort the current flow

## Configuration

Configuration is read from environment variables (or a local `.env`).
Copy `bot/.env.example` to `bot/.env` and fill in real values. The real
`.env` is never committed.

| Variable | Description | Default |
| --- | --- | --- |
| `BOT_TOKEN` | Telegram token from @BotFather (required) | - |
| `API_BASE_URL` | Base URL of the Kirkalab API | `http://app:8000` |
| `REQUEST_TIMEOUT` | API request timeout, seconds | `10` |

## Run locally

```bash
pip install -r bot/requirements.txt
export BOT_TOKEN=...
export API_BASE_URL=http://localhost:8000
python -m bot.main
```

## Run with Docker Compose

The bot is wired into the root `docker-compose.yml` as the `bot` service
(depends on `app`). Provide `BOT_TOKEN` in your environment or `.env`:

```bash
BOT_TOKEN=... docker compose up -d bot
```

## Notes

- JWT tokens are kept only in process memory, keyed by Telegram user id;
  they are never persisted to disk.
- No secrets are committed to the repository.
