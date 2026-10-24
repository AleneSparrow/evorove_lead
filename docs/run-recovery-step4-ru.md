# Шаг 4: владение запуском и восстановление зависшего running

Проверено 24 октября 2026. Worktree:
`/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Ветка `codex/cycle1-research-integration`, HEAD `c60f6f0`.
На GitHub этой ветки нет. `origin/main` — `27227bb`. Открытый checkout
`/Users/alenakulish/dev/evorove_lead` остаётся на `19d261b` и совпадает с
`origin/cursor/cycle1-lead-intelligence-53c4`. Commit, push и deployment не делались.
Незакоммиченные шаги 2 и 3 сохранены.

## Почему запуск мог зависнуть

`SearchTarget.last_status = running` исключал цель из `due`. Если процесс умирал
до `finally`, запись оставалась running, и планировщик больше её не брал.
Старый research lease в `0011` истекал по времени старта плюс запас, без
heartbeat: долгий живой запуск можно было принять за свободный.

## Механизм

Новых таблиц нет. Используется `research_run_leases` из миграции `0011`.
Право на запуск — один PostgreSQL UPSERT по `business_id`: вставка или замена
только если `expires_at` уже в прошлом. Идентификатор попытки `attempt_…`
пишется в lease и в документ `run_attempt`. Heartbeat продлевает срок только
своей строки. Завершение, освобождение и запись статуса сверяют `run_id`.
Чужой владелец получает `OwnershipLost` и не переписывает новую попытку.

Внутренний research run и внешний `SearchTarget` переводятся вместе:
acquire ставит оба в running, finish или recover ставит один и тот же конечный
статус. `interrupted` не считается due, поэтому планировщик сам не запускает
повтор. Повтор — только новый явный запуск. Это не продолжение с checkpoint:
обработанные страницы заново проходят существующие кэш и дедупликацию.
Дневной бюджет прерванной попытки не возвращается.

Старая запись `running` без `run_attempt` не перезапускается сама.
`acquire` отвечает `legacy_running_unverified`. Снять её может только
`python -m evorove_lead.run_recovery --business-id … --legacy-workers-stopped`
после остановки прежних процессов. Без этого флага recover отказывается.

Оператор видит `attempt_id`, `heartbeat_at`, `lease_expires_at` у живого
запуска и `stop_reason` у отказа, partial, budget и interrupted. Успешный
пустой поиск остаётся `no_fit` без `stop_reason`.

Уже ушедший внешний запрос этой схемой не отменяется. Candidate и outbox
по-прежнему одна транзакция и тот же `touch_id`. Exactly-once для CRM нет.

## Переходы

| Событие | SearchTarget | Research run / attempt |
| --- | --- | --- |
| Атомарный acquire | running | новая попытка running |
| Второй acquire при живом heartbeat | без изменений | `run_already_owned` |
| Heartbeat живого запуска дольше времени старта | running | тот же attempt |
| Исключение или отмена своего процесса | failed или interrupted | та же попытка закрыта |
| Процесс умер без finally | running, пока lease не истёк | running |
| recover после истечения heartbeat | interrupted, `heartbeat_expired` | попытка interrupted |
| Старый running без attempt и без подтверждения оператора | running | отказ `legacy_running_unverified` |
| Подтверждённое снятие старого running | interrupted, `legacy_workers_stopped` | не автоматический retry |
| Успешная пустая выдача | no_fit | completed, без stop_reason |
| Частичный или полный отказ поиска | partial или failed | причина шага 3 сохранена |

## Файлы шага 4

Новые, без коммита:

- `src/evorove_lead/run_ownership.py`
- `src/evorove_lead/run_recovery.py`
- `tests/test_run_recovery.py`
- этот отчёт

Изменены для владения и чтобы не сломать шаг 3:

- `src/evorove_lead/searches.py`: общий claim для API, CLI и планировщика
- `src/evorove_lead/api.py`: статус владельца; пустой `stop_reason` в успешный `no_fit` не попадает
- `src/evorove_lead/engine.py`: генерация под тем же owner
- `docs/client-zero-intelligence-runbook.md`: §6 — как оператор видит и снимает зависший running

Миграция не добавлялась. Разные `0009` двух локальных веток не смешивались.
Sonnet, тарифы, qualification, evidence и CRM payload не менялись.
`SearchPlanner.plan` не подключался.

## Проверки

Существующий `.venv` открытого checkout, `PYTHONPATH` этого worktree.
Платных вызовов и рабочей базы не было. PostgreSQL — уже запущенный
одноразовый кластер `/private/tmp/evorove-step4.kyizMN/pgdata` на
`127.0.0.1:55439`, база `evorove_test`. Новый кластер не создавался,
существующие базы не удалялись. Тесты создают и снимают свои схемы.
Атомарность проверялась независимыми соединениями к этому PostgreSQL,
не in-memory mock. Сроки — управляемые часы, без долгих sleep.

```sh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
P=/Users/alenakulish/dev/evorove_lead/.venv/bin/python
export PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1
export EVOROVE_CRM_REPO=/Users/alenakulish/dev/evorove-crm
EVOROVE_TEST_POSTGRES=1 "$P" -m pytest -q tests/test_run_recovery.py tests/test_postgres_research.py tests/test_search_transport.py tests/test_market_research_integration.py
"$P" -m pytest -q tests/test_market_research_integration.py tests/test_research_pipeline.py tests/test_market_intelligence_engine.py tests/test_qualification_gate.py tests/test_research_controls.py
"$P" -m pytest -q tests/test_search_transport.py tests/test_web_search.py tests/test_searches.py tests/test_market_research_integration.py tests/test_research_pipeline.py tests/test_run.py
EVOROVE_TEST_POSTGRES=1 "$P" -m pytest -q
```

Целевой пакет (recovery + Postgres + регрессии 2/3): **57 passed**, 4 warnings, 5.19 s.
Шаг 2: **37 passed**. Шаг 3: **73 passed**, 1 warning Starlette.
Полный набор: **367 passed, 1 failed, 0 skipped, 4 warnings, 8.02 s**.
PostgreSQL-тесты владения, admission и roundtrip до `0011` вошли в этот прогон.
Единственный отказ — `tests/test_rendering.py::test_renders_real_javascript_generated_content`:
нет бинарника Chromium `chromium_headless_shell-1234`. Код теста не менялся.
Это не регрессия шага 4.

## Границы и следующий пакет

Восстановление не продолжает оборванный обход страниц. Внешний HTTP, который
уже ушёл, может завершиться на чужой стороне после потери владения.
Автоматических повторов нет.

Следующий пакет — защита загрузчиков — выполнен: [шаг 5](loader-ssrf-step5-ru.md),
затем [изоляция CRM](crm-isolation-step6-ru.md) и
[подготовка живого прогона](live-run-prep-step7-ru.md). Живой прогон не запускался.
