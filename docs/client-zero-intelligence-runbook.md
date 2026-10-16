# Client 0 — контролируемая проверка evidence research

Никаких изменений соседних репозиториев, deployment и рассылок. Платный LLM и
запись в реальную CRM разрешаются отдельно. Не публикуйте секреты или `.env`.

## 1. Безопасный preflight

Из корня `evorove_lead`:

```sh
.venv/bin/python -m evorove_lead.client_zero --preflight
```

Команда не вызывает LLM, CRM или веб. Показывает только несекретные flags.
Exit 2 означает blocker. При уже разрешённом и настроенном SearxNG можно выполнить:

```sh
.venv/bin/python -m evorove_lead.client_zero --preflight --probe-search
```

Это один настоящий поисковый запрос к настроенному endpoint. Пустой ответ не
доказывает рабочий поиск. В текущей сессии endpoint не настроен, probe не выполнялся.

## 2. Локальные проверки без платных сервисов

```sh
.venv/bin/python -m pytest -q
```

PostgreSQL tests используют **только** localhost:55439/evorove_test, отдельные
случайные schemas и не читают production DATABASE_URL. Для временного локального
контейнера (если Docker и образ уже доступны):

```sh
docker run --rm -d --name evorove-lead-test-pg -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=evorove_test -e POSTGRES_DB=evorove_test -p 127.0.0.1:55439:5432 postgres:17-alpine
EVOROVE_TEST_POSTGRES=1 .venv/bin/python -m pytest -q tests/test_postgres_research.py
docker stop evorove-lead-test-pg
```

Trust authentication здесь допустима исключительно для этого временного localhost
контейнера с тестовыми данными. Не использовать эту команду для production.

## 3. Подготовка research dry run — после отдельного разрешения платных вызовов

Владелец настраивает окружение сама:

- существующие WEB_SEARCH_BASE_URL, DATABASE_URL для отдельной базы цикла 1,
  EVOROVE_CLIENT_ZERO_BUSINESS_ID и существующие Anthropic credentials/model;
- RESEARCH_ENABLED=1;
- RESEARCH_ALLOW_PAID_LLM=1 **только после отдельного одобрения**;
- RESEARCH_INPUT_USD_PER_MTOK и RESEARCH_OUTPUT_USD_PER_MTOK — проверенные тарифы
  выбранной модели; тариф обязателен, цена по имени неизвестной модели не угадывается;
- RESEARCH_CRM_DELIVERY не включать для dry run;
- при необходимости RESEARCH_BUDGET_JSON и RESEARCH_MAX_OUTPUT_TOKENS (default 2000).

Сначала применить Alembic в отдельной тестовой базе цикла 1. Рабочий DATABASE_URL
агент не читает, не запрашивает и не выводит. Обычная команда после настройки
владельцем: `.venv/bin/alembic upgrade head`.

Затем:

```sh
.venv/bin/python -m evorove_lead.client_zero --research-dry-run
```

Этот режим принудительно отключает создание CRM deliveries и **не flush-ит старый
outbox**, даже если CRM настроена. Он всё ещё может расходовать платные LLM-токены:
слово dry run относится к CRM, не к стоимости исследования. Результат включает
research_status/stop_reason, counts, без контактных данных.

Проверить в existing защищённом `/insights/business?business_id=...` research status,
стоимость и qualification decisions; полные dossiers находятся в tenant warehouse.
Убедиться, что пройдена последовательность business_analysis → market_research →
hypothesis_generation → prospect_search → evidence_collection → qualification →
adversarial_review → qualification_gate. Результат может быть пустым — это не ошибка
и не разрешение ослабить gate.

## 4. Ручная evaluation

Схема JSONL:

```sh
.venv/bin/python -m evorove_lead.evaluation --dataset unused --schema
```

Подготовить 300 вручную размеченных случаев (6 профилей, B2B+B2C, 180/120 без
пересечений). `provenance=synthetic` обязательно для выдуманных примеров. Для baseline
указать зафиксированные результаты исходной версии и baseline_version, не результаты
новой системы под старым именем.

```sh
.venv/bin/python -m evorove_lead.evaluation --dataset /absolute/path/manual-labels.jsonl --split holdout
```

Без dataset — exit 2 и `labelled_dataset_missing`. Нулевые знаменатели — null,
positive reply/conversion без данных — null. Оценка не запускает сеть или LLM.

## 5. CRM pilot — отдельный этап разрешения

Только после просмотра evidence, evaluation и отдельного одобрения реальной CRM
записи включить RESEARCH_CRM_DELIVERY=1 и использовать существующий search API/
расписание или Client 0 без `--research-dry-run`. Подключение должно использовать
persistent warehouse и существующий OutboxCrmLeadTouchSink.

Payload и API остаются прежними. Не включать исходящие кампании цикла 2 этой
проверкой. При CRM outage pending сохраняется; повтор через существующий
`/api/v1/internal/crm-deliveries/flush`. Доставка в Cold не означает сообщение человеку.
