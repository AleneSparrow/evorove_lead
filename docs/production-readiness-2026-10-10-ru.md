# EVOROVE: аудит инфраструктуры и подготовка Client 0

Проверки начаты 9 октября, отчёт завершён 10 октября 2026. Это подготовка к
эксплуатации, не свидетельство production-ready. Production deployment,
production migrations, платные LLM-вызовы, отправка сообщений и push не выполнялись.
Соседние репозитории, FOUNDATION.md и действующие конфигурации не изменены.

## 1. Что подтверждено в действующей инфраструктуре

Источник: read-only `railway status --json`, выборочные команды через уже
настроенный Railway SSH, публичные HTTP GET. Значения секретов не выводились;
`.env`, CLI credential files и приватные SSH-ключи не открывались.

Railway project: `prolific-clarity`, environment `production`, регион работающих
сервисов — `sfo`, по одной реплике. Срез состояния 9 октября:

| Компонент | Наблюдение |
| --- | --- |
| `ai-business-process-engine` | RUNNING/SUCCESS, commit `e349b3130a675830f5e1028708f5c9d89c93b61e` |
| `evorove-crm` | RUNNING/SUCCESS, commit `5bca99a1143b90575031b5efc5d09560ca44ce26` |
| `evorove-lead` | RUNNING/SUCCESS, commit `ef1e11dbad5499072553e19d52c409516f5d06cf` |
| `searxng` | RUNNING/SUCCESS, тот же lead commit, build root `searxng`, публичного домена нет |
| PostgreSQL | Три running service: `Postgres`, `Postgres-9WEU`, `Postgres-EY-2`; volumes READY: 500 MB / 5 GB / 5 GB |
| `evorove-sweep-cron` | Расписание `*/15 * * * *`, завершившийся контейнер; это не доказательство успешной рассылки |
| `evorove-lead-market-signals-cron` | Расписание `0 13 * * *`, завершившийся контейнер; результаты его прошлых запусков не проверены |
| `n8n` | RUNNING, persistent volume 5 GB; содержимое workflows не проверялось |

Оба домена `evorove.com` и `api.evorove.com` привязаны к Sales-сервису, target port
8080. HTTPS GET `/health` на API и CRM вернул `200 {"status":"ok"}`.
Публичный Lead `/openapi.json` возвращает действующие маршруты.
Lead `/health` отсутствует; предлагаем использовать существующий `/openapi.json`
для liveness. Это не DB readiness check.

Lead → CRM идёт через `evorove-crm.railway.internal`; Lead → поиск через
`searxng.railway.internal`. GET CRM `/health` из production Lead вернул 200.
Доступ к internal delivery status и insights Lead без авторизации — 401;
CRM board без авторизации — 401. Совпадение рабочих секретов и авторизованная
запись не проверялись — тестовые карточки в production не создавались.

Production Lead DB проверена через существующий SQLAlchemy adapter в транзакции
`SET TRANSACTION READ ONLY`: `alembic_version=0008`. Таблицы:
`briefs`, `candidates`, `crm_deliveries`, `hypotheses`, `hypothesis_outcomes`,
`hypothesis_pattern_library`, `market_signals`, `rejected_traces`, `search_targets`,
`traces`. Новых research-таблиц нет. Строки клиентов не извлекались.
Точное соответствие трёх Railway PostgreSQL-сервисов трём приложениям не установлено:
имя DB-сервиса для backup нужно подтвердить по reference в Railway, не угадывать.

Railway deployments используют restart ON_FAILURE; market-signals cron — NEVER.
Из проверенных manifests только Sales имеет healthcheck `/health` и pre-deploy.
Явных resource limits в полученных manifests нет. Настройка внешних alerting,
backup schedule и PITR пока не подтверждена.

## 2. Git и локальное окружение

Все три checkout на `main`. `git ls-remote` подтвердил текущие remote main:
Lead `ef1e11d…`, CRM `5bca99a…`, Sales `e349b31…`, совпадающие с production.
Lead HEAD равен remote; вся новая реализация, миграции 0009–0011 и подготовка
deployment пока незакоммичены. CRM checkout чистый. Sales checkout имеет чужие
незакоммиченные изменения и отстаёт от сохранённого upstream на 6 commits;
его рабочее дерево не менялось. Не выполнять массовый `git add` в соседних repos.

Уже работающий local sandbox: Sales :8001, CRM :8000, Lead :8002,
SearxNG :8080 и отдельные PostgreSQL. Он не равен production и не был
перезапущен. Новые проверки использовали отдельные контейнеры и БД.

## 3. Реальный поиск: работает, качество ещё не доказано

Предыдущий `search_unconnected` относился к процессу на Mac. В production
`WEB_SEARCH_BASE_URL` присутствует, реальный запрос через установленный
WebSearchClient вернул 5 результатов за 1.30 s. Провайдера не меняли.

Проверенные запросы и наблюдения:

| Запрос | Результат |
| --- | --- |
| `US small business appointment automation` (local raw SearxNG) | 20 результатов, преимущественно конкуренты/обзоры; Brave upstream 429, DuckDuckGo CAPTCHA |
| `US HVAC company expanding hiring customer service dispatcher` (production WebSearchClient) | 5 hits: Indeed, LinkedIn, Johnson Controls, Facebook; не готовые SMB-лиды |
| `"HVAC" "hiring" "dispatcher" "United States" -site:indeed.com -site:linkedin.com -site:facebook.com` | 3.41 s, 5 hits; два ZipRecruiter URL не удалось извлечь |
| `"plumbing" "opening a new location" "2026" -site:linkedin.com -site:facebook.com` | 3.03 s, пустой ответ; неизвестно, отсутствие результатов или ошибка upstream |
| `HVAC careers customer service Texas -site:indeed.com -site:linkedin.com -site:ziprecruiter.com -site:glassdoor.com` | 2.15 s, 5 hits, 4 уникальных домена; удалось извлечь 2 из первых 3 страниц |

Последний запрос вернул:

- `https://jobs.johnsoncontrols.com/job/WD30278599`: 10 886 символов,
  SHA256 `8441f803fb8af627d126c4cf13bbe7e64e59900a0404985b9cf48b1a1dd717a1`.
- `https://jobs.johnsoncontrols.com/job/WD30280997`: fetch rejected.
- `https://texasairmechanical.com/employment/`: 2 907 символов,
  SHA256 `255aea1b27306d2b1f64a53976079a5a71a6171f02279ab348261e861f9ea53d`.
- `https://careers-ars.icims.com/jobs/69284/hvac-technician-4-day-work-week%21/job`:
  обнаружен, страница в этой выборке не извлекалась.
- `https://texasacehvac.com/about/careers/`: обнаружен, страница не извлекалась.

У двух извлечённых страниц publication timestamp неизвестен. Публикация вакансии
не доказывает потребность в Evorove; Johnson Controls не соответствует ожидаемому
SMB-профилю. Ни одна компания не объявлена qualified, контакты в CRM не передавались.
Это диагностические запросы, а не заранее подготовленный список для Client 0.

WebSearchClient ограничивает выдачу пятью hits, timeout 10 s; ошибки и пустая
выдача сейчас не различаются в порте. US определяется запросом и evidence gate,
не гарантированным country filter поисковика. Company dedup проверен тестами;
в реальной выдаче найдены повторения домена. LinkedIn не извлекался.

Сравнение альтернатив по публичным сведениям, не по собственному benchmark:

| Вариант | Запросы / качество / скорость / надёжность | Стоимость и SaaS |
| --- | --- | --- |
| SearxNG | Работает сейчас; операторы поиска зависят от upstream, наблюдали 1.3–3.41 s и блокировки. В этой небольшой выборке много каталогов | Нет отдельного search API счёта, есть инфраструктура; права на источники не возникают из self-hosting |
| Brave Search API | Собственный индекс, фильтрация/переранжирование и local metadata; фактическое качество/latency в EVOROVE не измерены | $5/1000 search requests; для хранения API-результатов нужен план с storage rights |
| Tavily | Поиск basic/advanced и extraction; фактические скорость, релевантность и SLA здесь не проверены | PAYG $0.008/credit: basic 1, advanced 2 credits/query; коммерческие условия хранения нужно проверить перед интеграцией |

Источники: [SearxNG limiter](https://docs.searxng.org/admin/searx.limiter),
[Brave plans and storage rights](https://brave.com/search/api/),
[Tavily credits](https://docs.tavily.com/documentation/api-credits).
Решение: сохранить SearxNG для ограниченного пилота. Оснований заменять его без
сопоставимого live benchmark и согласования пока нет.

## 4. LLM и анализ сайта

Production Lead: `AI_PROVIDER=anthropic`, `ANTHROPIC_MODEL=claude-sonnet-4-5`.
Наличие Anthropic key подтверждено только как boolean; OpenAI key в Lead нет.
Поэтому требование «проверить существующий OpenAI» не соответствует интеграции
именно цикла 1. Провайдер и модель сохранены. Ключ в сообщении не нужен.

Новый adapter имеет timeout 45 s и **не делает автоматических paid retries**.
Повторный запуск — отдельная попытка с tenant budget. Тесты проверяют bounded
request, usage accounting, truncation, отсутствие usage и безопасную ошибку API.
Pydantic, ссылки на evidence и gate проверены offline; реальная модель ещё не
проверена. Её доступность и качество JSON нельзя вывести из наличия credentials.

У production образа нет Chromium. Plain fetch `evorove.com` дал 46 символов
видимого текста; PresenceSource с заголовками — 91. Подготовленный образ использует
существующий Playwright fallback и получил 6 661, затем 6 865 символов реального
сайта; deterministic offer reader принял материал. TLS не отключался.
На Mac Python сначала не находил CA issuer; `SSL_CERT_FILE=/etc/ssl/cert.pem`
устранил локальную проблему без изменения сертификатов или production.

`deploy/client-zero-brief.txt` содержит только заданные владельцем продукт и
US-targeting. Это owner assertions, не найденные публичные факты. В снимках
материалы теперь имеют `material_name`; локальный бриф маркируется
`owner_material/public=false`, сайт — `first_party/public=true`. URL у owner
material обозначает связанный бизнес, а не доказательство публикации этого текста.
Обновлён ключ DNA cache, чтобы не переиспользовать прежнюю маркировку.

## 5. Три цикла и опасность непреднамеренного запуска продаж

Cycle 1 payload проверен против фактических Pydantic-классов и domain parser
локального CRM checkout: tenant, person_id, touch_id, hypothesis_id, kind=assembled,
tab=Cold. Схемы загружаются без запуска CRM app. Production-код CRM имеет тот же
commit. Транзакционный outbox, retry, dedup и tenant isolation проверены тестами.

CRM при первом assembled автоматически создаёт `cold_assigned` в своём
integration outbox. Cycle 2 `/lead-commands` принимает его и вызывает
`outreach.assign_cold`; при подключённом mailbox это может привести к отправке.
Поэтому **production CRM Cold нельзя использовать как безвредный preview**.
Старый `/found` — отдельный исторический путь, не актуальный основной handoff.

Не выполнены: авторизованный HTTP end-to-end с реальными тремя БД, проверка
совпадения tenant ID в этих БД, реальная доставка/обратный outcome. Для пилота
выбран отдельный локальный tenant; production business ID не придуман.
Изменения соседних repos для подготовки не потребовались. Если нужен pilot Cold
в production при гарантированно выключенных продажах, потребуется отдельно
согласованный способ изоляции CRM/Sales или tenant-level pause до первого touch.

## 6. Подготовленные deployment-файлы

- `deploy/Dockerfile`: отдельный opt-in образ Python 3.11 + Chromium, uid 10001,
  явный allowlist COPY; без секретов/отчётов, без миграций в CMD.
- `deploy/compose.pilot.yml`: отдельная БД/volume/network, без опубликованного DB
  порта, без CRM/mailbox, paid gate=0, 20 companies/3 iterations, 2 CPU/2 GB для
  research, 256 MB shared memory, 1 CPU/512 MB для DB. Trust auth только здесь.
- `deploy/railway.toml`: предлагаемый config path, liveness `/openapi.json`,
  1 replica, ON_FAILURE/3, без автоматического cron/predeploy.
- `pilot_report.py`: read-only export по tenant + run ID; полный dossier,
  market/DNA, discoveries/rejections, reflection, rank; файл 0600, без overwrite.
  `company_name=null`, если отдельного подкреплённого name claim нет.
- `pilot-reports/` исключён из Git и Docker context; контакты не печатаются CLI.

Корневые Dockerfile/compose действующего deployment не перезаписаны. Старый
Dockerfile всё ещё выполняет Alembic при старте; новый надо выбрать явно.
Образ собран локально для ARM64; целевая Railway-сборка и её архитектура ещё не
проверены. Зависимости имеют диапазоны версий: зафиксировать release image digest
и версии пакетов после целевой сборки, не считать mutable tags воспроизводимым release.
Проверенный local image ID:
`sha256:1a9dfa2ba6de8adcf77dc70f0a610c5f0b169d3ef22c341545db78e6eda5af2f`.

## 7. Переменные и фоновые задания

| Настройка | Требование |
| --- | --- |
| `DATABASE_URL` | Только Lead warehouse; для первого пилота отдельная local DB, уже задана в Compose |
| `WEB_SEARCH_BASE_URL` | Production уже настроен; pilot использует существующий local :8080 |
| `ANTHROPIC_API_KEY` | Уже есть в production; в local pilot владелец предоставляет через защищённое окружение, не через чат или файл |
| `ANTHROPIC_MODEL` | Сохранить `claude-sonnet-4-5`; не менять без проверки |
| `RESEARCH_ENABLED` | Новый runtime, production пока не включён |
| `RESEARCH_ALLOW_PAID_LLM` | Только после отдельного разрешения |
| `RESEARCH_CRM_DELIVERY` | 0 для первого пилота |
| `RESEARCH_INPUT_USD_PER_MTOK`, `RESEARCH_OUTPUT_USD_PER_MTOK` | Явный актуальный тариф; расчёт ниже использует 3/15 |
| `RESEARCH_BUDGET_JSON` | 20 компаний, 3 итерации, 10 запросов, 60 страниц, 24 LLM calls, 60k/10k tokens, $1/run, $3/day |
| `RESEARCH_MAX_OUTPUT_TOKENS` | Default 2000; truncation означает остановку/недостаток evidence, не дописывание JSON |
| `EVOROVE_CLIENT_ZERO_BUSINESS_ID` | Pilot — отдельный локальный ID; production — существующий согласованный ID всех трёх циклов |
| `CRM_BASE_URL`, `INTERNAL_TASK_SECRET` | Production уже присутствуют; в pilot отсутствуют |

Search API использует FastAPI BackgroundTasks: задача не является durable job
queue. Crash может оставить search target в running; run lease имеет TTL, но
автоматический recovery target отдельно не доказан. Первый запуск — CLI, одна
реплика. Не масштабировать и не включать расписание до controlled run.

`python -m evorove_lead.searches` / `/searches/run-due` — существующий research
schedule. Market-signals cron — отдельный старый LLM-путь, его вызовы не входят
в новый ResearchBudget. Для общего production cost cap нужно сначала согласовать
его отключение/ограничение; в этой сессии cron не менялся. Наличие n8n означает,
что дополнительно нужно проверить внешние triggers, прежде чем включать research.

## 8. Проверки

`EVOROVE_TEST_POSTGRES=1 EVOROVE_CRM_REPO=/Users/alenakulish/dev/evorove-crm
.venv/bin/python -m pytest -q` → **309 passed, 4 warnings, 9.04 s**.
Warnings: Starlette/AnyIO deprecation и Alembic path_separator (3).
Дополнительно весь suite внутри собранного образа (Python 3.11, uid 10001):
**303 passed, 6 skipped, 1 warning, 8.23 s**. Пять PostgreSQL-тестов и sibling
CRM check пропущены только в этом отдельном контейнере; они пройдены в полном
локальном прогоне выше. Container warning — Starlette/httpx deprecation.

Проверены реальный PostgreSQL, полный scripted pipeline, конкурентные budgets/
admission, миграции head → 0008 → head с сохранением legacy candidate и pending
outbox; неизменный CRM schema parser; provider errors; export isolation и 0600.
Docker build прошёл; non-root import smoke прошёл; Alembic из образа установил
0011 в отдельной pilot DB; приложение из образа вернуло OpenAPI 200 и internal
без secret 401. Local `pg_dump -Fc` → новая `pilot_restore_check` → `pg_restore`
завершились успешно, восстановленная Alembic revision — 0011.
Это проверка механики backup, не restore production backup.
Preflight из нового контейнера: `search_probe=results_received`,
`delivery_enabled=false`, exit 2 только из-за отсутствия разрешения paid LLM.
Полный снимок повторного чтения сайта сохранён локально в
`pilot-reports/site-readiness-2026-10-10.json` (0600, исключён из Git).

## 9. Первый Client 0 run

Точные команды — [deployment runbook](deployment-client-zero-ru.md).
Сначала approved paid run только в isolated Compose, без production writes.
Ключ берётся из защищённого окружения владельца; модель/тарифы передаются явно.
Результаты: summary с run ID и отдельный JSON для ручной оценки. Пустой или
budget-exhausted результат допустим; gate не ослаблять.

Ручная проверка каждого dossier: market claim действительно в источнике,
ICP описывает механизм покупки, запрос проверяет гипотезу, компания — US SMB,
need не выведена только из индустрии, контакт действительно связан с компанией,
тайминг датирован, reviewer учёл альтернативные объяснения, reflection меняет
неудачный поиск, ranking применяется только после gate. Adaptive Search на первой
малой когорте намеренно не делает сильных выводов. Commercial effectiveness,
precision/recall и downstream conversion остаются неизвестными до разметки и
реальных outcomes. 300-case evaluation dataset всё ещё отсутствует.

## 10. Production rollout и rollback

До разрешения rollout: подтвердить Lead DB service reference; создать provider
backup, восстановить его в отдельную DB и проверить 0008 → 0011 на реальной копии.
Production backup не создан: destination, retention и restore access ещё не
подтверждены. Нельзя назвать локальный dump production backup.

После отдельного одобрения: выбрать `/deploy/railway.toml`, новый Dockerfile;
согласовать однократный pre-deploy `alembic upgrade 0011`; держать research/paid/
delivery выключенными при первом развёртывании. Не включать автоматически
research crons. Старый сервис не останавливается вручную; переключение трафика
допустимо только после healthy deployment. Проверить revision и auth, затем
согласовать отдельный dry run. Список шагов и команды есть в runbook.

Rollback предпочтительно кодом: research/paid/delivery off, вернуть прежний
release image; добавочные таблицы оставить. Downgrade удаляет evidence и не
отменяет уже доставленные CRM touches/продажи. Восстановление DB — только отдельное
решение с оценкой потери новых записей. Не replay/flush outbox при rollback вслепую.

Мониторинг до включения расписания: health, failed/stuck run, last successful run,
budget-exhausted, model errors, source fetch failures, pending CRM deliveries,
daily cost, объём research tables, backup age. Structured research records есть;
внешние alerts и retention policy ещё не настроены. Не логировать тексты с
контактами, ключи, DSN или полные provider exception bodies.

## 11. Расходы и необходимые решения

Официальный base тариф Sonnet 4.5 — $3 input / $15 output за миллион токенов:
[Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).
При 60k/10k потолок token estimate — $0.33; дополнительный денежный cap — $1/run.
Текущая доступность именно настроенного model alias требует разрешённой API-проверки.
Не переключать модель автоматически при API error. Cost/qualified lead пока null:
реальных qualified leads в этой проверке нет. Railway compute/storage/egress
оплачиваются отдельно, текущий invoice не проверялся. Новых платных API вызовов — 0.

Нужны отдельные решения владельца:

1. Один live LLM pilot с лимитом $1, до 20 компаний/3 итераций, isolated DB,
   без CRM и писем. Это ближайший рекомендуемый шаг.
2. После review пилота: production backup + restore drill, config change,
   миграции 0009–0011 и deployment Lead. Отдельно согласовать взаимодействие cron.
3. Production CRM delivery только после защиты от непреднамеренной рассылки
   или отдельного разрешения на цикл 2. Рассылки не входят в текущую задачу.

Никаких новых ключей или аккаунтов для существующего production Lead не нужно
запрашивать в чате. Для local pilot нужен безопасный доступ к уже существующему
Anthropic credential через окружение. Push выполняет только владелец.

## 12. Изменения именно этой задачи подготовки

Добавлены `deploy/Dockerfile`, `deploy/compose.pilot.yml`, `deploy/railway.toml`,
`deploy/client-zero-brief.txt`, `src/evorove_lead/pilot_report.py`, этот отчёт и
`docs/deployment-client-zero-ru.md`. Изменены `client_zero.py` (run ID в dry-run
summary), `evidence.py`/`research_runtime.py` (provenance материала, ссылки run на
DNA/market), README и ignore files. Добавлены `test_pilot_report.py`,
`test_research_provider.py`, `test_sibling_crm_contract.py`; дополнен
`test_postgres_research.py` проверкой сохранности данных. Новых миграций после
0011 нет. Тестовые контейнеры остановлены; pilot volume сохранён для повторения.
`git diff --check` пройден. Изменения не закоммичены и не отправлены в remote.
