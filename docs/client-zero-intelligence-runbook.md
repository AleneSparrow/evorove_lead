# Client 0 — контролируемая проверка evidence research

Никаких изменений соседних репозиториев, deployment и рассылок. Платный LLM и
запись в реальную CRM разрешаются отдельно. Не публикуйте секреты или `.env`.

## 1. Безопасный preflight

Обновление 23 октября 2026: рабочий research-путь теперь вызывает существующий
MarketResearchEngine после Business DNA. Его вопросы, поиск, загрузка источников
и интерпретация используют общий run/daily budget. Полный market report с evidence
сохраняется в tenant warehouse, передаётся в ICP, qualification и adversarial review.
Он направляет поиск, но не заменяет доказательства потребности конкретного человека.
QualificationGate, commercial rank и atomic candidate/outbox остаются прежними.

Market report кэшируется на 7 дней по tenant, модели, полному DNA, версии интеграции
и дополнительным research hints. При cache hit повторных market LLM/search-вызовов
нет. Источники имеют отдельный однодневный cache; истечение market TTL запускает
новое исследование в пределах общего бюджета. Qualification cache также учитывает
market document ID. В run сохраняются market_document_id и market_cache_hit.

Пустое исследование или невалидный отчёт останавливают run до ICP с
research_status=insufficient_evidence; исчерпание budget даёт budget_exhausted.
Это не completed. Обновление шага 3: transport/HTTP/JSON/source failures теперь
отличаются от пустой выдачи и сохраняются как failed/partial с stop_reason;
лимиты дают budget_exhausted. Инфраструктурный сбой не понижает оценку гипотезы.
Подробности и ограничения наблюдаемости: [шаг 3](search-transport-step3-ru.md).

| Этап | Подключение в research mode |
| --- | --- |
| Business DNA | Всегда; допустим cache hit |
| Market research → ICP | Всегда после DNA; допустим market cache hit |
| Adaptive allocation | Вызывается для гипотез; изменение весов только при достаточной истории |
| Prospect search / evidence | Если остались запросы и есть результаты |
| Qualification / adversarial review | Если собраны источники и опубликован контакт; возможен cache hit |
| Reflection strategy revision | Только после неудачной итерации, если есть лимит итераций и обоснование |
| Commercial ranking | Только для QUALIFIED после deterministic gate |
| CRM admission/outbox | Только при разрешённой delivery; отсутствует в research dry run |
| Sales outcomes для адаптации | Только при наличии зрелых событий; не создаются исследованием |
| LLM SearchPlanner.plan | Не вызывается: применяются существующие buyer_queries_for и bounded reflection |

API использует searches.live_engine; CLI run при RESEARCH_ENABLED=1 без JSONL
использует ту же сборку. Client 0 --research-dry-run явно конструирует тот же
ResearchPipeline с dry_run=True. Legacy/JSONL-сценарии остаются отдельными и не
являются подтверждением полного research-пути. Новые миграции не требуются:
используется граф main с head 0011, не альтернативная реализация 0009.

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

Payload и API остаются прежними. `RESEARCH_CRM_DELIVERY=1` без
`CRM_CYCLE2_ISOLATED=1` не пишет в CRM: pipeline остаётся dry run, HTTP не
отправляется, outbox не flush-ится. Флаг isolation означает, что оператор
подтвердила: у этой CRM нет `EVOROVE_BASE_URL` и нет обработчиков `cold_assigned`.
Рабочая CRM автоматически ставит cold_assigned при новом Cold и может запустить
письмо цикла 2. Handoff и `crm_pending` не равны подтверждённому Cold:
подтверждение — 2xx с `person_id` и `tab=cold` (`crm_accepted_cold`).
2xx без этих полей — `crm_accept_unconfirmed`, запись остаётся в outbox.
Подробности: [шаг 6](crm-isolation-step6-ru.md).

## 6. Зависший running

Одна задача принадлежит одному исполнителю. Право запуска — атомарный UPSERT
в `research_run_leases` (миграция 0011). Попытка имеет `attempt_id`. Heartbeat
каждые 30 секунд продлевает lease на 120 секунд. Долгий живой запуск не считается
зависшим только потому, что давно начался.

Зависший запуск: статус `running` и срок lease уже в прошлом, либо процесс умер
до `finally`. GET `/api/v1/internal/searches/{business_id}` и ежедневный sweep
вызывают recover: истекший lease становится `interrupted` с причиной
`heartbeat_expired`. Планировщик сам этот запуск не повторяет. Повтор — только
новый явный POST или CLI.

Оператор видит у живого запуска `attempt_id`, `heartbeat_at`, `lease_expires_at`.
У отказа, partial, budget и interrupted виден `stop_reason`. Успешный пустой
поиск остаётся `no_fit` без `stop_reason`.

Старая запись `running` без heartbeat не снимается сама:

```sh
.venv/bin/python -m evorove_lead.run_recovery --business-id <id> --legacy-workers-stopped
```

Флаг подтверждает, что прежние процессы остановлены. Без него ответ
`legacy_running_unverified`. Это не продолжение с места остановки: повтор
идёт через существующие кэш и дедупликацию. Дневной бюджет не возвращается.
Подробности: [шаг 4](run-recovery-step4-ru.md).

## 7. Защита загрузчиков

Сайт владельца, страница результата поиска, evidence snapshot и Playwright
используют один `require_public_http_url`. Редирект проверяется на каждом
прыжке. Chromium не открывает `file://`, loopback, private и metadata
(`169.254.169.254`, `metadata.google.internal`). Уже ушедший HTTP этим
правилом не отменяется. Подробности: [шаг 5](loader-ssrf-step5-ru.md).

## 8. Подготовка живого прогона — без запуска

Живой открытый веб и платная модель не запускались в этой сессии. Перед
отдельным разрешением владельца:

1. Отдельная база цикла 1, alembic head `0011`. Рабочий `DATABASE_URL` не
   читать и не применять.
2. `RESEARCH_ENABLED=1`, явные тарифы, `RESEARCH_ALLOW_PAID_LLM=1` только
   после одобрения.
3. SearxNG в `WEB_SEARCH_BASE_URL`. Сначала `--preflight`, затем при
   необходимости `--preflight --probe-search`.
4. `RESEARCH_CRM_DELIVERY` и `CRM_CYCLE2_ISOLATED` выключены. Первая живая
   проверка — `--research-dry-run`.
5. Не указывать рабочую CRM. Preflight блокирует настроенную CRM без
   isolation.
6. Смотреть `stop_reason`, `partial`/`failed`/`no_fit`, lease, стоимость.
   Пустой результат — не ошибка и не повод ослабить gate.

После dry run CRM-пилот только на изолированной CRM и только после
отдельного одобрения. Соседний репозиторий цикла 2 не менять.
Подробности: [шаг 7](live-run-prep-step7-ru.md).
