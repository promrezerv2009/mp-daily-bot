# mp-daily-bot

mp-daily-bot — сервис ежедневной аналитики по маркетплейсам Ozon и Wildberries с отдачей данных в Telegram-бот и веб-дашборд на FastAPI.

## Возможности

- Сбор заказов, финансовых показателей и рекламных расходов по API Ozon и Wildberries.
- ETL-конвейер с нормализацией данных и записью в PostgreSQL.
- Предрасчёт ежедневных агрегатов и расчёт ключевых метрик (revenue, profit, ROMI и пр.).
- Telegram-бот (aiogram 3) с командами `/yesterday`, `/orders`, `/ads`, `/topsku`, `/export`, `/refresh`.
- Веб-дашборд (FastAPI + Jinja) с переключателями периодов и выгрузкой Excel.
- Планировщик (APScheduler) для ночного обновления (02:00 Europe/Moscow) и ручного форс-обновления.

## Структура проекта

```
mp-daily-bot/
├─ app/
│  ├─ main.py               # FastAPI-приложение и планировщик
│  ├─ bot.py                # Telegram-бот
│  ├─ config.py             # Настройки (Pydantic BaseSettings)
│  ├─ logging_conf.py       # Настройка логирования
│  ├─ db.py                 # Подключение к БД (SQLAlchemy)
│  ├─ models.py             # Модели SQLAlchemy
│  ├─ schemas.py            # Pydantic-схемы
│  ├─ repos/                # Репозитории работы с БД
│  ├─ services/             # ETL, расчёты, отчётность, планировщик
│  ├─ api/                  # REST и веб-роуты
│  ├─ templates/            # Jinja-шаблоны
│  └─ static/               # CSS
├─ migrations/              # Alembic миграции
├─ reports/                 # Папка для Excel-отчётов
├─ tests/                   # Unit-тесты
├─ docker-compose.yml
├─ Dockerfile
├─ entrypoint.sh            # Применение миграций перед стартом
├─ alembic.ini
├─ requirements.txt
├─ .env.example
└─ README.md
```

## Подготовка окружения

1. Скопируйте пример переменных окружения и заполните значения:

   ```bash
   cp .env.example .env
   ```

   Обязательно задайте:

   - `TELEGRAM_BOT_TOKEN` — токен, полученный у BotFather.
   - `ADMIN_IDS`, `MANAGER_IDS` — список chat_id через запятую (узнать можно командой `/start` в боте или через `getUpdates`).
   - `DB_URL` — строка подключения к PostgreSQL (`postgresql+psycopg://user:pass@db:5432/mpdaily`).
   - `OZON_CLIENT_ID`, `OZON_API_KEY` — ключи кабинета продавца Ozon.
   - `WB_TOKEN` — API токен Wildberries (Statistics API).

2. Проверьте доступ к API Ozon/WB — убедитесь, что токены активны и имеют необходимые разрешения (заказы, транзакции, рекламные расходы).

## Запуск в Docker

```bash
docker compose up -d --build
```

Сервисы:

- `db` — PostgreSQL 15.
- `app` — FastAPI + APScheduler (`http://localhost:8000`).
- `bot` — aiogram-бот, запускает polling.

При запуске контейнер `app` применяет миграции (`alembic upgrade head`) через `entrypoint.sh`.

### Команды для управления

- Просмотр логов:

  ```bash
  docker compose logs -f app
  docker compose logs -f bot
  ```

- Остановка:

  ```bash
  docker compose down
  ```

## Telegram-бот

Команды:

- `/start`, `/help` — краткая справка.
- `/yesterday` — сводка за вчера.
- `/orders [period|from..to]`
- `/ads [period|from..to]`
- `/topsku [period|from..to]`
- `/export [period|from..to]` — Excel отчёт.
- `/refresh [period|from..to]` — только admin, форс-обновление данных.

Допустимые периоды: `yesterday`, `7`, `14`, `month` или диапазон `YYYY-MM-DD..YYYY-MM-DD`.

## REST и веб

- `GET /api/metrics?period=yesterday`
- `GET /api/export?from=2024-10-01&to=2024-10-14`
- `GET /` (редирект на `/yesterday`)
- `GET /range?period=7` или `/range?from=YYYY-MM-DD&to=YYYY-MM-DD`

## SDK types

- Генерация типов: `cd packages/sdk && npm install && npm run gen` (требуется запущенный FastAPI по `http://localhost:8000`).
- В `packages/sdk/src/fetch.ts` есть helper `apiFetch` с автоматической прокладкой заголовка `X-Tenant-Id` (`setDefaultTenant("tenant-123")`), используйте его в мобильном/web-клиенте.

## Планировщик

- Cron по умолчанию: `0 2 * * *` (02:00 Europe/Moscow). Настраивается через `.env` (`SCHEDULER_REFRESH_CRON`, `SCHEDULER_TIMEZONE`).
- Ручной запуск: команда `/refresh`.

## Тесты

Локально (Python 3.11+):

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest
```

## Развёртывание на VPS

1. Установите Docker и docker-compose-plugin.
2. Склонируйте репозиторий и заполните `.env`.
3. Запустите `docker compose up -d --build`.
4. Настройте reverse-proxy (например, nginx) для проксирования `app:8000`.
5. Настройте systemd unit или cron для автостарта Docker (если нужно).

## Полезно знать

- База данных: PostgreSQL, ORM — SQLAlchemy 2.0, миграции — Alembic.
- Планировщик: APScheduler (AsyncIO) с timezone `Europe/Moscow`.
- Telegram: aiogram 3, авторизация по chat_id из `.env`.
- Экспорт: openpyxl + pandas (файлы сохраняются в `reports/`).
- Логи: структурированный stdout, уровень задаётся `LOG_LEVEL`.

Проект собран модульно: клиенты API → трансформация → аналитика → репозитории/сервисы → презентация (бот/веб). Это упрощает дальнейшее расширение — можно добавлять новые метрики, платформы и витрины без изменения ядра.
