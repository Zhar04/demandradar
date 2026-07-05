# PROGRESS — живой чеклист

> При обрыве сессии: читать этот файл + PLAN.md + DECISIONS.md, продолжать с первого
> незавершённого пункта текущего этапа. Всё состояние — в git.

## Э0. Discovery + каркас — ✅ ЗАВЕРШЁН (2026-07-05)

- [x] Разведка источников: 4 отчёта (goszakup API; гос-порталы; квазигос/ЭТП/застройщики; агрегаторы) — итоги в config/sources.yaml и PLAN.md §3
- [x] Фактическая GraphQL-схема goszakup снята с офиц. graphdoc (TrdBuy/Lots/Subject, фильтры, пагинация) — без выдумок, поля сверены
- [x] git init (репо: https://github.com/Zhar04/demandradar.git), venv, requirements, pyproject, ruff, pytest, CI (GitHub Actions + scripts/ci.ps1)
- [x] Абстракции: core/models.py (DemandSignal+воронка), net/http.py (Fetcher: ретраи/backoff/троттлинг/UA/Retry-After), net/proxy.py (ProxyProvider→NoProxy), llm/* (Null/Ollama/ClaudeCode + фабрика), connectors/base.py (Connector+реестр)
- [x] config/sources.yaml (реестр 20+ источников с проверенным типом доступа), config/categories.yaml (ключевые слова + черновик ТРУ/ОКЭД)
- [x] PLAN.md, ASSUMPTIONS.md, DECISIONS.md, .env.example
- [x] Тесты: 23 passed; ruff: чисто
- [x] Отчёт латентных/опережающих источников получен, sources.yaml дополнен: data.egov.kz gbd_ul (бесплатный API, ядро опережающего), Wordstat API (бесплатно по заявке, 1000/сутки), hh.ru (нужен бесплатный app token), рабочие RSS (kapital/kursiv/inbusiness/time/profit), Statsnet платный — отклонён как источник

**Самооценка Э0: 92/100** — Корректность 38/40 (реестр источников проверен вживую; -2 за неподтверждённые без токена детали goszakup), Тесты 22/25 (23 юнит-теста на абстракции; -3 нет интеграционных, появятся в Э1), Надёжность 18/20 (ретраи/backoff/троттлинг реализованы и оттестированы), Качество/доки 14/15.

## Э1. Вертикальный срез goszakup — ✅ ЗАВЕРШЁН (2026-07-05)

- [x] storage: schema v1 (signals, connector_state), repo-слой, дедуп INSERT OR IGNORE по sha256(source:source_id), WAL, версионирование миграций
- [x] classify: стемы по началу слова + negative_keywords с «вето» на global-сеть + tru_prefixes (сила: код = 2 слова)
- [x] connectors/goszakup: GraphQL-клиент (limit/after keyset-пагинация, publishDate-фильтр, Bearer), mock-фикстуры 1:1 по форме ответа ows (7 объявлений / 9 лотов, вкл. нерелевантные и объявление без лотов), сигнал = ЛОТ, контакты заказчика из Subject, КАТО→регион
- [x] notify: Telegram через общий Fetcher, HTML-формат, dry-run в консоль без токена
- [x] core/pipeline (изоляция сбоя коннектора, курсор=max(publishDate) в connector_state) + CLI: --once --dry-run --connector --backfill N --db; pip install -e .
- [x] Тесты: 61 passed (были 23) — storage, classifier (позитив/негатив/вето/коды/стемы), goszakup (нормализация, пагинация, ошибки GraphQL), pipeline (фильтр, дедуп, битый коннектор не роняет проход)
- [x] Дым-тест CLI: 1-й прогон new=8 (уголь и «ремонт кровли» отброшены), 2-й прогон new=0 dup=8; ruff чист

**Самооценка Э1: 92/100** — Корректность 37/40 (срез работает конец-в-конец; -3: live-режим не проверен без токена, маппинг enstruList ИД→код отложен, см. A-13), Тесты 23/25 (-2: нет live-интеграционного), Надёжность 18/20 (изоляция ошибок на уровне элемента и коннектора, курсоры, ретраи), Качество/доки 14/15.

**Хвост на Э1-live (когда владелец даст GOSZAKUP_TOKEN):** живая интроспекция для сверки схемы; выгрузка ref_enstru и маппинг ИД→код (A-13); сверка значений refBuyStatusId; реальный backfill-прогон.

## Э2. Обогащение + скоринг + LLM-опция — ✅ ЗАВЕРШЁН (2026-07-05)

- [x] enrich: CompanyProfile + EnrichmentProvider (протокол), MockRegistryProvider (фикстуры в формате data.egov gbd_ul), ContactEnricher с цепочкой провайдеров и кешем company_cache (schema v2, инкрементальная миграция v1→v2 тестируется)
- [x] scoring: config/scoring.yaml (веса, бюджет-бакеты, half-life свежести, home_regions, категорийные факторы, бонус за контакт) + Scorer 0..100; проверка ранжирования на реальном прогоне: showcases/Астана 95.9 > мелкое бельё 66.5
- [x] LLM-опция: пайплайн уточняет категорию «мутных» (other) сигналов через LLMProvider ТОЛЬКО если провайдер доступен; Null → категория остаётся other (тест FakeLLM + деградация)
- [x] pipeline: exists-проверка ДО обогащения (дубликаты не тратят ресурсы), enrich/LLM обёрнуты — не роняют проход
- [x] Тесты: 76 passed; ruff чист; дым-тест CLI: 8 сигналов, у всех score>0, контакт ЛПР добавлен

**Самооценка Э2: 93/100** — Корректность 37/40 (-3: обогащение пока mock-провайдером, веса черновые A-5), Тесты 23/25, Надёжность 19/20 (изоляция enrich/LLM-сбоев тестируется), Качество/доки 14/15.

## Э3..Э7 — ожидают (критерии в PLAN.md §5)

## Блокеры/ожидания от владельца (не блокируют текущую работу)
- Токен goszakup (к Э1-live) — инструкция в PLAN.md §7.1
- Telegram bot token + chat_id (к Э3)
- Подтверждение ТРУ/ОКЭД (PLAN.md §6) и watchlist компаний (к Э5)
