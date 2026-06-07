# Отчёт: RBAC-тесты kirkalab

- **Репозиторий:** amenemha/kirkalab
- **Ветка:** main
- **Дата:** 2026-06-07
- **Коммит тестов:** f8cefab (Create test_rbac_users.py)

## 1. Итог

В директорию `tests/` добавлены и закоммичены два файла: `conftest.py` (фикстуры) и `test_rbac_users.py` (RBAC-тесты пользователей). Изменения внесены прямо в ветку `main`.

## 2. Закоммиченные файлы

| Файл | Коммит | Назначение |
|------|--------|-----------|
| `tests/conftest.py` | Create conftest.py | Фикстуры client, db |
| `tests/test_rbac_users.py` | f8cefab | RBAC-тесты пользователей |

## 3. Покрытие тестами

| Сценарий | Ожидаемый результат |
|----------|--------------------|
| Анонимный запрос (без токена) | 401 / 403 |
| Невалидный токен | Отказ в доступе |
| Не-админ листает пользователей | 403 "Admin privileges required" |
| Админ листает `/api/v1/users/` | 200, ответ списком |
| Неактивный user -> `/api/v1/auth/me` | Отказ в доступе |

## 4. Запуск локально

```bash
pip install -r requirements.txt
pytest tests/test_rbac_users.py -v
# с покрытием:
pytest tests/test_rbac_users.py -v --cov=app --cov-report=term-missing
```

## 5. Дальнейшие шаги

- Прогнать `pytest -v` локально и убедиться, что все тесты зелёные.
- Добавить CI workflow `.github/workflows/tests.yml`.
