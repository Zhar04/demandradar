# DemandRadar

Мультиисточниковый агрегатор сигналов спроса (Казахстан/СНГ) на товарные группы владельца:
кровати, матрасы, постельное бельё, ЛДСП/офисная мебель, кухни, кресла, стеллажи, витрины,
МДФ, торговое оборудование, объекты «под ключ».

Собирает **формализованный** (тендеры), **латентный** (поисковый спрос, маркетплейсы) и
**опережающий** (новые компании, стройки, новости) спрос → нормализует в единую модель →
фильтрует по ключевым словам и кодам ЕНС ТРУ → дедуплицирует → скорит → доставляет в
Telegram и веб-дашборд с воронкой сделок.

Принципы: **ноль платных API** (ИИ опционален и локален: Ollama / Claude Code, по умолчанию
выключен), ядро — детерминированный код; приоритет официальным API и легальному чтению
публичных данных; mock-режим каждого коннектора без ключей.

## Статус

Проект в активной разработке. Текущий этап и прогресс: [PROGRESS.md](PROGRESS.md).
План и архитектура: [PLAN.md](PLAN.md).

## Быстрый старт (dev)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env        # ключи можно НЕ заполнять — всё работает в mock-режиме
.venv\Scripts\python -m pytest
```

Основные команды (всё работает в mock-режиме без единого ключа):

```powershell
# один проход конвейера: сбор -> фильтр -> дедуп -> скоринг -> алерты (dry-run: в консоль)
.venv\Scripts\python -m demandradar --once --dry-run

# веб-дашборд: воронка, метрики, сигналы, мониторинг коннекторов, отчёт+CSV
.venv\Scripts\python -m demandradar --serve --port 8080
# -> открыть http://127.0.0.1:8080

# дневной дайджест в Telegram (без токена / с --dry-run — в консоль)
.venv\Scripts\python -m demandradar --digest

# точечные прогоны
.venv\Scripts\python -m demandradar --once --connector goszakup --backfill 7

# Модуль A: разобрать объявление коммерческой недвижимости по ссылке (точечно!)
.venv\Scripts\python -m demandradar --add-listing "https://krisha.kz/a/show/..."

# точечное письмо по сигналу: без --confirm — только черновик (лог в БД)
.venv\Scripts\python -m demandradar --outreach <dedup_key> --to buyer@company.kz
.venv\Scripts\python -m demandradar --outreach <dedup_key> --to buyer@company.kz --confirm

# собрать плейбук в .docx
.venv\Scripts\python scripts\build_playbook_docx.py

# демон 24/7: планировщик опроса + дневной дайджест
.venv\Scripts\python -m demandradar --daemon
```

## Демон 24/7

`--daemon` опрашивает каждый коннектор по своему интервалу (`DR_POLL_MINUTES`,
дефолт 30 мин; переопределение — `poll_minutes` у источника в `config/sources.yaml`),
продолжает с последнего курсора после рестарта (таблица `connector_state`),
переживает сбой любого источника и шлёт дневной дайджест в `DR_DIGEST_HOUR`
(дефолт 17:00). Остановка — Ctrl+C (graceful: дорабатывает цикл).

## Docker и деплой на VPS

```bash
# на сервере (Ubuntu + docker compose):
git clone https://github.com/Zhar04/demandradar.git && cd demandradar
cp .env.example .env && nano .env       # вписать токены (без них всё в mock)
docker compose up -d --build            # radar (демон) + dashboard (порт 8080)
docker compose logs -f radar            # логи сбора
curl http://127.0.0.1:8080/health       # health-check
```

Дашборд в compose слушает только `127.0.0.1` VPS — наружу публикуй через
reverse-proxy (Caddy/Nginx) с базовой авторизацией: в дашборде есть контакты
и заметки, не выставляй его в интернет без пароля. Прокси-пул для обхода
блокировок подключается реализацией `ProxyProvider` (`src/demandradar/net/proxy.py`)
без правок коннекторов — сейчас NoProxy.

## Переключение mock → live (по мере получения ключей)

| Ключ в .env | Что оживает |
|---|---|
| `GOSZAKUP_TOKEN` | goszakup: GraphQL API (основной поток тендеров) |
| `DR_SCRAPE_LIVE=1` | mitwork, med/fms.ecc, mp.kz, RSS-новости (уважают Crawl-delay) |
| `DATA_EGOV_APIKEY` | новые юрлица по ОКЭД (опережающий) |
| `HH_TOKEN` | вакансии watchlist-компаний |
| `YANDEX_DIRECT_TOKEN` | Wordstat (латентный) |
| `TELEGRAM_BOT_TOKEN`+`CHAT_ID` | алерты и дайджест в Telegram |
| `SMTP_*` | реальная отправка точечных писем (всё равно нужен `--confirm`) |

Плейбук работы с воронкой и статусами: [docs/PLAYBOOK.md](docs/PLAYBOOK.md).

## Конфигурация

| Файл | Что там |
|---|---|
| `.env` (из `.env.example`) | Секреты и режимы. Нет ключа = mock-режим коннектора. |
| `config/sources.yaml` | Реестр источников: тип доступа, приоритет, этап, юр. статус. |
| `config/categories.yaml` | Товарные группы: ключевые слова, коды ЕНС ТРУ/ОКЭД (черновик). |

## Получение доступов (когда понадобятся)

**Токен goszakup (бесплатный, на 1 год).** Регистрация организации на goszakup.gov.kz
(нужна ЭЦП НУЦ РК) → в кабинете «Профиль участника → Выпуск токена (для разработчиков)»
(роль «Администратор организации»), либо письмо оператору АО «ЦЭФ» support@ecc.kz с просьбой
дать доступ к унифицированным сервисам. Токен → `GOSZAKUP_TOKEN` в `.env` — коннектор сам
переключится из mock в live.

**Telegram-бот.** В Telegram: @BotFather → `/newbot` → скопировать токен в
`TELEGRAM_BOT_TOKEN`. Свой chat_id: написать своему боту любое сообщение, затем открыть
`https://api.telegram.org/bot<ТОКЕН>/getUpdates` — поле `message.chat.id` → `TELEGRAM_CHAT_ID`.

**ИИ-слой (опционально, по умолчанию выключен).**
- Ollama: установить с ollama.com → `ollama pull qwen3:8b` → в `.env`:
  `DR_LLM_PROVIDER=ollama`. Слабое железо — `OLLAMA_MODEL=qwen3:4b`.
- Claude Code: если установлен CLI `claude`, достаточно `DR_LLM_PROVIDER=claude_code` —
  задачи пойдут через локальный агент (используется ваша подписка Claude, софт не ходит
  в платные API сам).
- Выключить ИИ: `DR_LLM_PROVIDER=null` (дефолт). Все функции работают на эвристиках.

## Разработка

- Тесты: `.venv\Scripts\python -m pytest` · Линт: `.venv\Scripts\python -m ruff check src tests`
- Локальный CI: `.\scripts\ci.ps1` · CI GitHub Actions: `.github/workflows/ci.yml`
- Артефакты процесса: PLAN.md · PROGRESS.md · DECISIONS.md · ASSUMPTIONS.md

Разделы «Запуск демона 24/7», «Docker», «Деплой на VPS» появятся на Этапе 7.
