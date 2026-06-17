# Kirkalab — фронтенд (admin UI)

Лёгкий статический фронтенд без сборки (vanilla HTML/CSS/JS). Общается с FastAPI-API по относительным путям `/api/v1/*`.

## Состав

- `index.html` — разметка: вход, сброс пароля, профиль, список пользователей.
- `app.js` — API-клиент и логика: логин, хранение токенов в `localStorage`, авто-`refresh` при 401, профиль (`/auth/me`), список пользователей (`/users/`, только для админа), подтверждение email и сброс пароля.
- `styles.css` — тёмная тема.

## Как работает в production

Caddy раздаёт статику из `/srv` (смонтировано из `./frontend`) и проксирует только `/api/*` и `/health` на сервис `app:8000`. Настройки — в `deploy/Caddyfile` и `docker-compose.prod.yml`.

Запуск:

```
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

После запуска фронтенд доступен по `https://$DOMAIN/`, API — по `https://$DOMAIN/api/v1/...`.

## Локальная разработка

Для локальной проверки раздайте папку с того же origin, что и API, или настройте прокси `/api` на `http://localhost:8000`, чтобы избежать CORS.
