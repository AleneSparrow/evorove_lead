# Шаг 6: изоляция CRM от цикла 2 и проверка доставки

Проверено 24 октября 2026. Worktree:
`/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Ветка `codex/cycle1-research-integration`, HEAD `c60f6f0`. Commit, push и
deployment не делались. Репозиторий `evorove-crm` и внешний CRM-контракт не
менялись.

## Почему это нужно

Рабочая CRM при новом Cold пишет `cold_assigned` в свой outbox. Если задан
`EVOROVE_BASE_URL`, цикл 2 может написать человеку. Один
`RESEARCH_CRM_DELIVERY=1` эту цепочку не останавливает. Счётчик `handoffs`
считает локальный admit, а не карточку на вкладке Cold.

## Механизм

Живая запись требует оба флага: `RESEARCH_CRM_DELIVERY=1` и
`CRM_CYCLE2_ISOLATED=1`. Isolation — подтверждение оператора, что у целевой
CRM нет обработчиков цикла 2 и нет `EVOROVE_BASE_URL`. Цикл 1 не может
выключить чужой CRM-воркер.

Без обеих переменных:

- research pipeline остаётся dry run;
- `HttpCrmLeadTouchSink` и outbox не открывают HTTP и не меняют очередь;
- flush не трогает outbox и не увеличивает attempts;
- preflight ставит blocker, если CRM настроена или delivery включена без isolation.

Подтверждённая доставка — 2xx с `person_id` и `tab`. `tab=cold` даёт
`accepted_cold`. 2xx без этих полей — `crm_accept_unconfirmed`, запись
остаётся в очереди. Handoff ≠ pending ≠ accepted_cold.

Payload lead-touch не менялся. Exactly-once для CRM нет: CRM по-прежнему
дедуплицирует `touch_id`.

## Файлы шага 6

- `src/evorove_lead/crm_touch.py`
- `src/evorove_lead/research_config.py`
- `src/evorove_lead/sqlalchemy_warehouse.py`
- `src/evorove_lead/api.py`
- `src/evorove_lead/client_zero.py`
- `src/evorove_lead/run.py`
- `src/evorove_lead/insights.py`
- `tests/test_crm_isolation.py`
- `tests/test_crm_delivery.py`
- `tests/test_run.py`
- этот отчёт

Миграция не нужна.

## Проверки

```sh
"$P" -m pytest -q tests/test_crm_isolation.py tests/test_crm_delivery.py tests/test_run.py tests/test_client_zero.py tests/test_sibling_crm_contract.py
```

CRM-пакет: **40 passed**. Регрессии шагов 2/3/4: 37 / 73 / 15 passed.
Полный набор: **388 passed, 0 failed**, 4 warnings.

## Границы

Флаг isolation не доказывает, что рабочая CRM действительно отключена: это
операторское подтверждение. Проверка доставки читает ответ CRM, не ходит на
доску как владелец. Соседний цикл 2 не менялся.
