# Шаг 7: подготовка живого прогона

Проверено 24 октября 2026. Worktree:
`/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Ветка `codex/cycle1-research-integration`, HEAD `c60f6f0`. Живой прогон, платные
вызовы, commit, push, deployment и production-миграции не выполнялись.

## Что уже закрыто до живого веба

- шаг 2: MarketResearchEngine в ResearchPipeline;
- шаг 3: ошибка поиска ≠ пустая выдача; partial/failed доходят до API/CLI;
- шаг 4: lease/heartbeat, recover → interrupted, не вечный running;
- шаг 5: загрузчики и Playwright режут private/metadata так же, как evidence;
- шаг 6: CRM не пишется без isolation; handoff ≠ confirmed Cold.

## Что оператор делает сама, когда разрешит dry run

1. Отдельная база цикла 1. `alembic upgrade head` → `0012`. Рабочий
   `DATABASE_URL` агент не читает.
2. Окружение: `RESEARCH_ENABLED=1`, явная модель и тарифы,
   `WEB_SEARCH_BASE_URL`. `RESEARCH_ALLOW_PAID_LLM=1` только после одобрения.
3. `RESEARCH_CRM_DELIVERY` и `CRM_CYCLE2_ISOLATED` выключены.
4. Preflight, затем Client 0 dry run:

```sh
.venv/bin/python -m evorove_lead.client_zero --preflight
.venv/bin/python -m evorove_lead.client_zero --research-dry-run
```

Dry run тратит токены, если LLM разрешён. Он не flush-ит outbox и не пишет
Cold. Пустой `no_fit` — допустимый исход.

## Чего в этой сессии нет

- живого SearxNG probe;
- платного Anthropic вызова;
- записи в любую CRM;
- Chromium для optional render-теста (не нужен dry run, SPA всё ещё имеет
  HTTP fallback).

## Оставшиеся блокеры до CRM-пилота

- линия `codex/cycle1-research-integration` не на GitHub;
- на базе, уже накатанной до cursor-`0009`, применять только `0010`–`0012`;
  research-линию со старым `0009`/`0011` не продолжать — пересобрать пустую базу;
- рабочая CRM с `EVOROVE_BASE_URL` запускает цикл 2 — для пилота нужна
  отдельная CRM без этих обработчиков и явный `CRM_CYCLE2_ISOLATED=1`;
- market-signal LLM вне research budget;
- confirmed Cold ≠ handoff.

Следующий шаг после этой подготовки — отдельное разрешение владельца на
`--research-dry-run` с платными вызовами, не CRM-пилот.

## Проверки этой сессии

Целевые загрузчики 28, CRM 40, шаг 2 — 37, шаг 3 — 73, шаг 4 — 15.
Полный безопасный набор: **388 passed, 0 failed, 0 skipped, 4 warnings, 7.75 s**.
Платных вызовов, probe SearxNG и записей CRM не было.
