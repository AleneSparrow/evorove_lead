# Шаг 3: исходы поискового транспорта

Завершено 24 октября 2026, повторно проверено в тот же день. Worktree:
`/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Ветка `codex/cycle1-research-integration`, HEAD/база `c60f6f0`.
Незакоммиченные изменения шага 2 сохранены; commit, push и deployment не выполнялись.
Исходный workspace `/Users/alenakulish/dev/evorove_lead` остаётся на `19d261b` с `?? pilot-reports/`.
В этом worktree уже лежат незакоммиченные файлы шага 4 (`run_ownership.py`, `run_recovery.py`); этот пакет их не менял.

## До и после

Раньше HttpSearxngWebSearchClient возвращал `()` при сетевом отказе, слишком большом
ответе, невалидном JSON или отсутствии results. Следующие компоненты не могли
отличить поломку поиска от отсутствия подходящих людей. Внутренние ошибки источников
SearxNG не проверялись. Обработанный отказ мог выглядеть как no_fit/обычная неудачная
итерация исследования.

Теперь типизированный SearchError содержит безопасный код причины, признак
возможного повтора, Retry-After и доступные результаты частичного ответа. В код
ошибки не попадают endpoint, query, тело ответа или содержимое исключения провайдера.
Отдельного параллельного pipeline нет: используется прежний путь HTTP client →
BudgetedSearch → MarketResearchEngine / WebSearchPeopleSearch → ResearchPipeline →
GenerationResult → SearchTarget/API/CLI.

Успешная выдача остаётся tuple SearchHit. Корректный results=[] — успех транспорта.
Ошибки разбора, HTTP, конфигурации и источников больше не возвращаются как пустой
успех. Ошибка/частичный ответ не попадают в client success-cache; полноценные
результаты, включая пустые, кэшируются в пределах жизни экземпляра клиента.
Неполный market report сохраняется для проверки, но не попадает в market cache.

Частичные данные исследуются в рамках прежнего evidence gate. Квалифицированные
результаты сохраняются, но весь запуск получает partial, не completed. Если ни один
поисковый запрос не дал успешного ответа, transport failure даёт failed, не no_fit.
При отказе источников с полезными hits BudgetedSearch передаёт hits дальше и
одновременно сохраняет ошибку в run. При полном отказе одного запроса market stage
может продолжить остальные вопросы; prospect stage сохраняет успешные данные других
запросов. Контакт по-прежнему допускается только через неизменённый QualificationGate.

## Исходы

| Исход поиска | Результат pipeline | Research run / внешний статус |
| --- | --- | --- |
| Валидная непустая выдача | Обычные evidence/qualification/gate | completed; people_found или no_fit по фактическому отбору |
| Валидная пустая prospect-выдача | Нет найденных кандидатов | completed; no_fit допустим |
| Все market-запросы корректно пусты | Нет retrieved market evidence, ICP не запускается | insufficient_evidence; GenerationResult no_fit, это не полный completed research |
| Timeout / сетевой отказ после попыток | Причина сохранена, без ложного пустого успеха | failed при полном отказе; partial при успешной части |
| HTTP 429 / временные 5xx после попыток | Аналогично transport failure | failed / partial и search_http_NNN |
| HTTP 400/401/403/404, 501/505 и прочие неповторяемые статусы | Явная ошибка без бессмысленного retry | failed / partial |
| Невалидные JSON, encoding, структура или oversized response | Явная ошибка, success-cache не заполняется | failed / partial |
| unresponsive_engines непустой, hits есть | Hits сохранены и проверены; неполнота отмечена | partial, search_sources_unresponsive |
| unresponsive_engines непустой, hits нет | Отказ, не отрицательное доказательство | failed, либо partial если другие запросы успешны |
| Поиск не сконфигурирован в live-режиме | Запуск не начинается | API 503; CLI failed/search_not_configured, ненулевой exit |
| Некорректный endpoint/лимит конфигурации | Запуск не начинается | search_invalid_configuration |
| Внутренний cap запросов / research budget | Ограничение выполнения | budget_exhausted; search_request_limit либо существующая причина budget |
| Временная ошибка, затем успешный retry | Обработка успешного ответа | completed допустим; обе попытки учтены |

Новые варианты существующего GenerationStatus: failed, partial, budget_exhausted.
GenerationResult получил совместимое необязательное поле stop_reason с пустым
default. Успешные API/CLI payload не менялись. При отказе API status дополнительно
возвращает stop_reason; run CLI и Client 0 показывают причину и завершаются ненулевым
exit. Daily sweep добавляет errors только при отказах; CLI searches возвращает 1.
Market-signals sweep тоже не маскирует отказ пустым набором новостей.

Причина SearchTarget сохраняется через существующий tenant-scoped ResearchDocument
kind=search_status без новой схемы. Она доступна после пересоздания API. Для её
чтения/записи используется существующая исследовательская БД main с head 0011.
Обработанный запуск сохраняет конечный SearchTarget status до записи диагностических
details и освобождает research lease в прежнем finally. Восстановление running после
аварийного завершения процесса этим пакетом не реализовано.

Legacy per-hypothesis путь тоже обрабатывает SearchError: отказавшая гипотеза не
передаётся в verify_hypothesis с искусственным пустым набором; успешные результаты
других гипотез сохраняются, статус отражает частичный/полный отказ. Optional stub
без live-конфигурации остаётся search_unconnected, а не считается working live search.

## Повторы, лимиты и feedback

- HTTP client делает одну попытку за вызов. Только BudgetedSearch владеет retry:
  максимум две попытки, то есть один повтор. Вложенных retry-циклов нет.
- Retry разрешён для timeout/network и HTTP 429/500/502/503/504. Постоянные HTTP
  ошибки, 501/505, невалидный ответ и unresponsive_engines не повторяются автоматически.
- Retry-After разбирается как секунды или HTTP date. Если ожидание превышает
  2 секунды либо выходит за run deadline, повтора нет; причина возвращается сразу.
  Без Retry-After задержка 0.1 секунды. Короткий Retry-After соблюдается.
- Перед каждой попыткой расходуется существующий queries budget, включая попытку,
  закончившуюся отказом. Перед ожиданием проверяются оставшиеся запросы и deadline.
  Значения BudgetLimits не увеличены. Cache hit тоже консервативно учитывается как
  обращение BudgetedSearch, но не расходует HTTP cap клиента.
- HTTP cap по умолчанию 30 фактических попыток на экземпляр. Несекретный
  LEAD_MAX_SEARCH_REQUESTS позволяет задать cap; 0 явно отключает только этот cap,
  но не research budgets. Отрицательное/нечисловое значение — ошибка конфигурации.
- После неполной итерации reflection не делает выводов о сегменте/потребности и
  не инициирует новый поиск на основании transport failure. Затронутые strategy
  observations не сохраняются для adaptive-весов. Ранее завершённые успешные
  наблюдения остаются действительными; факты кандидатов не превращаются в ошибки.
- Терминальные коды сохраняются в run.search_errors и run.stop_reason. Уже возникший
  budget_exhausted сохраняет свою бюджетную причину; search_errors остаётся отдельной
  диагностикой. Ошибки не записываются как rejected candidate или отсутствие спроса.

## Файлы шага 3

Новые: `tests/test_search_transport.py`, этот отчёт.

Изменены именно в шаге 3:

- `src/evorove_lead/web_search.py`: SearchError, валидация HTTP/JSON/структуры,
  source failure metadata, success-cache, cap, явная live-конфигурация.
- `src/evorove_lead/research_budget.py`: единственный bounded retry и учёт попыток.
- `src/evorove_lead/market_intelligence.py`: продолжение других market queries при
  отказе одного; полный отказ не подменяется отсутствием evidence.
- `src/evorove_lead/research_reflection.py`: остановка стратегии при infrastructure_failure.
- `src/evorove_lead/research_runtime.py`: сохранение ошибок/частичных данных, безопасный
  feedback, исключение неполного market report из cache, конечный GenerationStatus.
- `src/evorove_lead/engine.py`: совместимые error outcomes, обработка legacy поиска.
- `src/evorove_lead/searches.py`: сохранение причины, конечный target status,
  ошибки sweep и CLI exit; live search требует endpoint.
- `src/evorove_lead/api.py`: 503 для ошибок конфигурации, stop_reason из сохранённого
  результата; совместимые ошибки sweep.
- `src/evorove_lead/run.py`, `client_zero.py`: причина/ненулевой exit/preflight errors.
- `tests/test_web_search.py`: прежние assertions «malformed=empty» заменены явными
  assertions SearchError согласно исправленному контракту; проверки не отключались.
- `docs/client-zero-intelligence-runbook.md`: актуальная обработка transport failures.

Сохранены изменения шага 2 в `qualification.py`, `search_planner.py`,
`tests/test_market_research_integration.py`, `docs/market-intelligence-implementation-ru.md`
и `docs/market-research-integration-step2-ru.md`. В общих файлах research_runtime.py
и runbook интеграция MarketResearchEngine сохранена. Sonnet, тарифы, qualification
criteria, правила evidence, миграции, atomic candidate/outbox и CRM payload не менялись.
SearchPlanner.plan не подключался.

## Проверки

Внешние границы заменены fake opener, scripted site fetch/LLM и CRM transport.
Реальные HTTP adapter, BudgetedSearch, MarketResearchEngine, ResearchPipeline,
engine, API, CLI, evidence/gate, cache и хранилища выполняются. В тестах нет живого
поиска, платного LLM, рабочей CRM, отправителей и production БД.

Целевые тесты проверяют валидный результат/пустую выдачу, timeout/network,
429/5xx/постоянные HTTP ошибки, Retry-After, JSON/encoding/structure/size,
source failures, cap, retry recovery и max attempts, несколько market-запросов с
частичным/полным отказом, cache поведения, API/CLI причины, отсутствие running,
ложного no_fit, отрицательных strategy observations и необоснованной reflection.
Проверено чтение API-причины после пересоздания приложения и sweeps обоих режимов.

Повтор 24 октября 2026 из этого worktree. Существующий `.venv` открытого checkout,
`PYTHONPATH` указывает на этот worktree. Платных вызовов, `DATABASE_URL` и
рабочей CRM не было.

```sh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
P=/Users/alenakulish/dev/evorove_lead/.venv/bin/python
export PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1
export EVOROVE_CRM_REPO=/Users/alenakulish/dev/evorove-crm
"$P" -m pytest -q tests/test_search_transport.py tests/test_web_search.py tests/test_searches.py tests/test_market_research_integration.py tests/test_research_pipeline.py tests/test_run.py
```

Целевые: **73 passed, 0 failed, 0 skipped, 1 warning, 3.93 s**.

PostgreSQL — уже запущенный одноразовый кластер
`/private/tmp/evorove-step4.kyizMN/pgdata` на `127.0.0.1:55439`, база
`evorove_test`. Новый кластер не поднимался: порт занят этой тестовой копией.
Production URLs не читались. Тесты создают и снимают свои схемы.

```sh
EVOROVE_TEST_POSTGRES=1 "$P" -m pytest -q
```

Полный набор: **367 passed, 1 failed, 0 skipped, 4 warnings, 8.17 s**.
Включены интеграции шага 2, PostgreSQL concurrency/admission/roundtrip и
read-only контракт локальной CRM. Единственный отказ —
`tests/test_rendering.py::test_renders_real_javascript_generated_content`:
нет бинарника Chromium. Это окружение, не регрессия транспорта. Предупреждения:
Starlette/AnyIO alias и три Alembic `path_separator`. `git diff --check`
сообщает о пустой строке в конце `research_delivery.py` (файл шага 2/admission,
в этом пакете не правился).

## Оставшиеся границы и следующий пакет

SearxNG unresponsive_engines — доступный сигнал неполноты, не полная телеметрия
всех его источников. Если поле отсутствует/пусто, валидный results=[] принимается
как успешный ответ endpoint. Отличить настоящую пустую выдачу от скрытого отказа
внутренних источников без метаданных невозможно. Нет утверждения, что все источники
работали. Сообщение об отказе даже одного источника консервативно делает run partial.
Без deadline/лимита ожидания клиента retry не выполняется скрыто на других уровнях.

Произвольный custom WebSearchClient должен соблюдать контракт SearchError.
Непредусмотренное исключение остаётся dependency_failure/failed, не маскируется
пустым ответом. Общая защита загрузчиков, аварийный stale running, подтверждённые
delivery metrics и изоляция CRM→cycle 2 остаются отдельными пакетами.
Соседний LeadSearchClient и UI имеют открытый string/dict контракт статуса и
принимают дополнительные поля; отображение понятного текста новых статусов в UI
в этом backend-пакете не реализовано. Соседние исходники не изменялись.

Следующий конкретный пакет: восстановление stale SearchTarget running в
`searches.py`/`api.py` с опорой на существующий research lease и timestamp запуска.
Определить срок/условие восстановления, не перехватывать действующий lease и не
допускать двойного admission. Проверки: аварийно оставленный running снова доступен
для запуска; текущий живой запуск не дублируется; две конкурентные попытки дают
один admission. Изменение схемы, если потребуется, сначала обосновать; старые
миграции и разные 0009 не смешивать. До открытого веб-пилота также требуется
отдельная проверка/починка private redirects и browser resources.

Платных запросов, внешних CRM writes, рассылок и production migrations не было.
Временный PostgreSQL остановлен; его данные и логи сохранены. Зависимости не
обновлялись. Все изменения шагов 2 и 3 оставлены без коммита в указанном worktree.
