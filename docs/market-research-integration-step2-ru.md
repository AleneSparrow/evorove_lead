# Шаг 2: интеграция MarketResearchEngine

Проверено 23 октября 2026. База: `c60f6f08333ccf2fe63d654705593ae33f775882`.
Ветка: `codex/cycle1-research-integration`.
Worktree: `/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Изменения оставлены без коммита. Исходная рабочая копия остаётся на `19d261b`;
её `pilot-reports/` и локальная main не изменялись. Более новой research-реализации
в доступных локальных refs не найдено; remote refs не обновлялись.

## Изменения и фактический путь

Client 0 `run_client_zero_search(research_dry_run=True)` → существующая фабрика
research_pipeline_from_env → LeadGenerationEngine.generate → ResearchPipeline:

1. Бриф сайта/материалов → grounded Business DNA.
2. Существующий MarketResearchEngine: вопросы → бюджетируемый поиск → полученные
   снимки страниц → grounded MarketIntelligenceReport.
3. Отчёт и evidence сохраняются tenant-scoped; run получает market_document_id.
4. ICPHypothesisEngine получает DNA и market report. Допускаются supporting_claims
   из обоих проверенных наборов, неизвестные ID отклоняются.
5. Buyer queries → public prospect search → доказательства конкретного кандидата.
6. Qualification и независимый adversarial review получают market report как
   контекст. Доказательства потребности/контакта берутся из candidate evidence.
7. Неизменённый deterministic gate → CommercialOpportunityRanker → сортировка.
8. Dry run сохраняет решения без Cold/outbox. Разрешённая delivery использует
   прежний atomic admission candidate/claims/outbox и прежний CRM payload.

API использует searches.live_engine. CLI run в research mode без JSONL также
использует эту сборку; Client 0 явно фиксирует dry_run=True. Второй pipeline и
новая схема не создавались. Миграции, включая 0009, не изменены.

Market cache: 7 дней, tenant PK + model + полный DNA + версия интеграции + hints.
Cache hit не вызывает market LLM/search повторно. После expiry исследование
повторяется; источники отдельно кэшируются на день. Изменение tenant исключает
перенос отчёта. Qualification cache включает market document ID, а qualification
document сохраняет эту связь. Невалидное/пустое market research останавливает
run до ICP, status=insufficient_evidence; лимит даёт budget_exhausted, не completed.
GenerationResult остаётся совместимым; для полноты выполнения читать research
run status, который Client 0 выводит отдельно.

## Этапы

| Этап | Фактический статус |
| --- | --- |
| 0. Проверка исходной базы | Выполнена; исторический аудит повторно не проводился |
| 1. DNA / evidence types | Подключён; DNA может загружаться из cache |
| 2. Market research / ICP | Подключён; market report может загружаться из cache |
| SearchPlanner.plan | Не подключён; остаётся buyer_queries_for + bounded reflection |
| 3. Qualification / adversarial / ranking | Условный: источники и опубликованный контакт; rank только после QUALIFIED |
| 4. Reflection | Условный: неуспешная итерация, оставшийся лимит, обоснованные revised queries |
| 4. Adaptive Search | Allocation подключён; изменение весов только при достаточной истории |
| 5. Admission / CRM | Подключён при разрешённой delivery; отключён в research dry run |
| 6. Sales feedback | Условный: зрелые внешние outcomes; текущие тесты синтетические |
| 7. Проверки / evaluation | Сквозные тесты пройдены; реальная precision не установлена |
| 8. Client 0 | Подключён; живой платный прогон в этом шаге не выполнялся |

## Изменённые файлы

- `src/evorove_lead/research_runtime.py`: вызов market engine, cache/evidence/document
  persistence, передача report downstream, qualification cache version и failed_stage.
  Внешний fetcher разрешается при конструировании, чтобы фабричный путь можно было
  проверять через подмену границы загрузки.
- `src/evorove_lead/search_planner.py`: market context и проверка supporting refs.
- `src/evorove_lead/qualification.py`: market context assessment/review, без изменения gate.
- `tests/test_market_research_integration.py`: семь сквозных проверок с учётом параметризации.
- `docs/client-zero-intelligence-runbook.md`: реальный путь, cache, условность этапов,
  необходимость изоляции CRM от cycle 2.
- `docs/market-intelligence-implementation-ru.md`: исправление исторического заявления.
- Этот отчёт.

## Проверки

Использовано существующее `/Users/alenakulish/dev/evorove_lead/.venv/bin/python`.
Внешние границы заменены: поиск, чтение сайта/страниц, LLM completion, CRM transport.
Настоящие ResearchPipeline, MarketResearchEngine, StructuredReasoner, DNA/ICP,
qualification/review/gate/rank, SQLite/PostgreSQL, cache и outbox не подменялись.
Client 0 проверен через реальную конфигурационную фабрику с синтетическими настройками.
В его dry run CRM не сконфигурирована, и assertions проверяют отсутствие Cold/outbox.

Проверяются: прохождение исходного брифа в market engine; порядок LLM-этапов;
изменение market evidence → другая гипотеза/запрос → 1 против 0 handoff;
qualification/review context и candidate-only reason; tenant isolation; cache hit
без повторных market вызовов; expiry; отсутствие дублей при повторном admission;
невалидный tenant, пустой market и бюджетный отказ до ICP; отсутствие reflection
при iterations=1. Существующие тесты отдельно проверяют justified reflection
revision и ограничение adaptive-весов малыми выборками.

Команды ниже запускались из исходной рабочей копии; harness меняет cwd/sys.path
на указанный worktree и вызывает pytest. Он очищен от внешней среды через env -i
и блокирует Python socket.connect, кроме localhost:1,9,55439. Никакие paid API или
рабочие CRM/БД не использовались. Harness сохранён с шага 1Б:

```sh
P=/Users/alenakulish/dev/evorove_lead/.venv/bin/python
H=/var/folders/v_/gxbmkskx2bq_86m1_9wx89bh0000gn/T/evorove-audit-ncm4zg5i/safe_test.py
W=/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
env -i PATH="$PATH" HOME="$HOME" PYTHONDONTWRITEBYTECODE=1 "$P" "$H" "$W" tests/test_market_research_integration.py tests/test_research_pipeline.py tests/test_market_intelligence_engine.py tests/test_qualification_gate.py tests/test_research_controls.py
```

Первый расширенный целевой прогон: **36 passed**. После добавления проверки
лимита итераций/запросов и расширения tenant-проверки повторный focused run
`tests/test_market_research_integration.py tests/test_research_pipeline.py tests/test_research_controls.py`
дал **21 passed**.

Новый PostgreSQL 18.3 создан в `/tmp/evorove-step2.kgFPPC/pgdata` через установленный
`/Library/PostgreSQL/18/bin/initdb` с UTF8, отдельным socket и localhost:55439.
Новая БД evorove_test не является БД предыдущего шага или другой ветки. Существующие
тесты создавали временные схемы; migrations roundtrip выполнялся только в них.

```sh
env -i PATH="$PATH" HOME="$HOME" PYTHONDONTWRITEBYTECODE=1 EVOROVE_TEST_POSTGRES=1 EVOROVE_CRM_REPO=/Users/alenakulish/dev/evorove-crm "$P" "$H" "$W"
```

Итог: **323 passed, 0 failed, 0 skipped, 4 warnings, 5.44 s**. Включены пять
PostgreSQL tests и read-only sibling CRM contract. Warnings: Starlette/AnyIO alias
и три Alembic path_separator. `git diff --check` проходит. Лог:
`/tmp/evorove-step2.kgFPPC/full-tests.log`. Временный PostgreSQL остановлен;
данные сохранены. Зависимости не устанавливались и не обновлялись.

## Границы и следующий пакет

Sonnet 4.5 и тарифы пилота сохранены. Секреты/.env не читались и не менялись.
Нет deployment, commit, push, production migrations, внешних CRM writes и рассылок.
Зелёные scripted tests не доказывают качество реального поиска или доступность
конкретного аккаунта Anthropic.

Подтверждённые риски перед live остаются отдельными задачами: HTTP search transport
подавляет ошибки; внешний SearchTarget может зависнуть в running; не все загрузчики
защищают private redirects/browser resources; handoff не равен подтверждённому Cold;
рабочая CRM автоматически создаёт cold_assigned, запускающий cycle 2. На этом шаге
они не препятствовали изолированной проверке и не исправлялись.

Следующий конкретный пакет: **достоверный статус поиска** в `web_search.py`,
`research_runtime.py`, `tests/test_web_search.py` и `tests/test_research_pipeline.py`.
Различать успешную пустую выдачу и transport/HTTP/invalid-response failure реального
SearxNG adapter; сохранять failure в research run, не допускать Cold/outbox при
ошибке. Критерии: fake opener с timeout/URLError/HTTP error даёт failure; валидный
пустой JSON — пустой поиск; существующие бюджеты, payload и полный suite сохраняются.
До живого открытого веба также требуется отдельная проверка/починка защиты загрузчиков;
до CRM pilot — изолированный контур без cycle 2 и отдельные delivery metrics.
