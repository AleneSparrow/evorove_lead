# Evidence-driven Cycle 1 — реализация и границы проверки

Дата: 9 октября 2026. Владелец разрешила изменения только `evorove_lead`.
FOUNDATION.md и внешние контракты не изменены. Этот документ не меняет основу продукта.

## Baseline и совместимость

Исходная ревизия: `ef1e11dbad5499072553e19d52c409516f5d06cf`.
До изменений: **261 passed**, одно предупреждение зависимости Starlette.

Сохранены LeadGenerationEngine, PresenceSource, OfferUnderstanding,
PeopleSearch/HypothesisPeopleSearch, WebSearchPeopleSearch, WebSearchClient/SearxNG,
AnalysisWarehouse, существующие search API/расписание, market signal hook,
CRM lead-touch payload, person_id/touch_id, retry API и sales outcome API.

Намеренное ужесточение: legacy PeopleHit с одним reason больше не даёт Cold.
Он проходит прежние базовые проверки и затем обязательный gate; отсутствие
проверенного dossier означает `missing_verified_evidence`. Тесты старого
автоматического допуска изменены на проверку отказа. Положительный путь отдельно
проверяется полным новым pipeline, включая PostgreSQL. Это изменение требований
к допуску, а не ослабление тестов.

`RESEARCH_ENABLED` по умолчанию выключен. При выключенном новом pipeline старый
поиск пригоден для диагностики и склада анализа, но не обходит новый gate.

## Уточнение интеграции 23 октября 2026

Исторические заявления ниже о полном market research не соответствовали c60f6f0:
ResearchPipeline сохранял только public_need query plan, не вызывая
MarketResearchEngine. В текущем пакете этот существующий движок подключён:
DNA → budgeted market research → grounded market report → ICP → public prospect
search → candidate evidence → qualification/review → gate → rank → разрешённый
atomic admission. Market report также передаётся в qualification/review и связан
с qualification document через market_document_id. Market evidence не даёт права
признать потребность отдельного кандидата без его собственных источников.

Tenant/model/DNA/hints-scoped cache предотвращает повторное market research
в пределах 7 дней. Ошибка или отсутствие market evidence прерывают run до ICP;
полное завершение не объявляется. Модель, тарифы, миграции и CRM payload не менялись.
Условность этапов и актуальная инструкция: [runbook](client-zero-intelligence-runbook.md).

## Реализованные этапы 0–8 (исторический перечень; см. уточнение выше)

0. Аудит компонентов, baseline, фиксация внешних ограничений.
1. BusinessDNA и типы SourceSnapshot/EvidenceClaim/ClaimSource/ContactEvidence;
   immutable provenance, tenant FK, компании и связи обнаружения.
2. MarketResearchEngine: вопросы → retrieval → интерпретация; ICPHypothesisEngine:
   механизм покупки, требования evidence, disqualifiers, стабильный key/версия;
   SearchPlanner с запросами по гипотезе. Ranking ICP использует количество
   supporting references, затем некалиброванную research confidence.
3. LeadQualificationEngine, отдельный AdversarialValidation, QualificationGate,
   CommercialOpportunityRanker. Ranking не является допуском.
4. ResearchReflectionLoop, AdaptiveSearchPlanner, ResearchBudget, ResearchCache,
   LLMUsageLedger. Все вызовы находятся внутри общего бюджета.
5. LeadGenerationEngine вызывает ResearchPipeline как внутреннюю orchestration;
   кандидат, связи с claims, admission и существующий outbox фиксируются атомарно.
6. Attribution run-hypothesis → stable ICP; зрелые когорты, pending вместо false
   failure, агрегаты повторно вычисляются без повторного начисления событий.
7. Unit/integration tests, PostgreSQL concurrency и Alembic roundtrip,
   offline evaluation harness. Реальной precision-оценки пока нет.
8. Client 0 preflight, отдельный research dry run, инструкция контролируемого запуска.

## Полный порядок

1. Чтение сайта через существующий PresenceSource; offer остаётся grounded.
   Дополнительно пробуются публичные `/services` и `/about` в пределах бюджета.
2. Structured Business DNA: услуги, проблемы, ценность, бизнес-модель, география,
   категории покупателей, positioning, differentiation, pricing, ограничения,
   исключения, запрещённые обещания и неизвестное. Поля ссылаются на claims.
3. Вопросы об отрасли и demand/alternatives; реальный поиск и загрузка документов.
   Пустой поиск не заменяется выдуманным market report.
4. ICP и query plan; консервативное распределение доступных запросов.
5. Discovery через существующий WebSearchPeopleSearch. Компания дедуплицируется
   до LLM; связи со всеми гипотезами сохраняются. Новые источники могут дополнить
   dossier уже встреченной компании. B2C не требует компании.
6. Загрузка публичных страниц, дешевый поиск опубликованного контакта и
   существующая heuristic_decision_maker; structured extraction с source IDs.
7. Шесть независимых оценок, затем отдельный adversarial-вызов. Дополнительные
   запросы reviewer расходуют тот же budget; повторное чтение ограничено.
8. Deterministic gate; qualifying opportunities сортируются по объяснимому rank.
9. Dry run сохраняет research decisions, но не Cold/outbox. Разрешённая CRM-доставка
   использует существующий payload и outbox. Рассылки отсутствуют.

Текст источника передаётся как недоверенные данные. Модель не получает инструменты,
секреты или право отправки; она не может подменить URL/содержимое снимка своим
ответом. LLM возвращает draft claims со source IDs; сервер сам связывает их с
полученными снимками. Смысловая поддержка проверяется отдельным reviewer. Это
снижает риск prompt injection, но не заменяет ручную оценку качества модели.

## QualificationGate

Не использует score для компенсации нарушения обязательных условий. Проверяет:
DNA и tenant; подтверждённую US-географию и локальность при ограниченной зоне;
категорию покупателя; доказательства fit/need/timing/reliability/contact;
существование цитат; согласованную смысловую поддержку initial/review;
тип утверждения (не inference); правильный субъект/домен; публичность и свежесть;
дату buying signal (retrieval date её не заменяет); реальный персональный контакт,
роль для B2B, ограничения контакта; отсутствие disqualifier/critical contradiction;
дедупликацию. Generic inbox не считается персональным decision maker.

Решения: QUALIFIED, RESEARCH_MORE, REJECTED, INSUFFICIENT_EVIDENCE.
Причина в CRM — подтверждённая цитата, а не непроверенный LLM summary.
Существующие consent/STOP-решения цикла 2 не выдумываются: отсутствие канала
синхронизации запретов из CRM остаётся границей интеграции. Публичный email не
означает consent. Цикл 1 сам ничего не отправляет.

## Reflection и адаптация

Максимум три исследовательские итерации на run по умолчанию. История содержит
запросы, rejection codes и observation IDs. Reflection предлагает причину неудачи
и новые запросы; неизвестные observation IDs отклоняются. Повтор того же запроса,
необоснованное продолжение, исчерпание budget/deadline останавливают цикл.
Критерии gate внутри reflection не изменяются. Дополнительный сигнал рынка
передаётся как непроверенная подсказка для исследования, не как доказательство.

AdaptiveSearchPlanner распределяет query budget по качеству, source reliability,
cost per qualified и доступным mature sales outcomes. При малых выборках остаётся
равномерным; нужны минимум 30 обнаружений и три независимые дневные когорты для
изменения оценки стратегии. Каждая гипотеза получает exploration-запрос, если
общий бюджет это позволяет. Сложного RL и обучения моделей нет.

Outcome API остаётся прежним: in_progress, offer_made, done, dropped. Доступны
только timestamps получения события: occurred_at внешнего события неизвестен.
Последнее полученное состояние case используется один раз. Окно зрелости — 30
дней; решения о reweight требуют 30 resolved cases. Pending не считается dropped.
На уровне stable ICP агрегируются независимые hypothesis runs. Выводы являются
наблюдаемыми ассоциациями, не причинными эффектами.

## Хранение и миграции

Применять только к базе цикла 1 после проверки backup и тестового upgrade.
В этой сессии production migrations не применялись.

- `0009_research_evidence`: research_documents (versioned DNA/report/ICP/decisions),
  research_sources, research_claims, research_claim_sources, research_companies,
  research_discoveries; rejection_reason у existing candidates.
- `0010_research_resources`: research_budget_days, research_cache, research_usage.
- `0011_research_admission`: research_admissions, research_run_leases,
  research_candidate_claims; существующая crm_deliveries сохраняется.

Структурированные payloads валидируются Pydantic; provenance и admission дополнены
composite tenant FK. Старые данные не получают выдуманного evidence. Миграции
добавочные; downgrade удаляет новые research-данные и предназначен для теста/
заранее согласованного восстановления, не автоматического production rollback.

Run lease защищает tenant от параллельных запусков, expires после deadline + 120s.
Дневной бюджет резервируется атомарно до LLM. При ошибке неизвестная стоимость
остаётся зарезервированной. Кандидат, admission, claims и pending delivery — одна
транзакция. Crash после commit оставляет outbox для существующего flush. CRM
переигрывает неизменный touch_id с неизменным payload. При отсутствии PostgreSQL
нет live fallback на NullAnalysisWarehouse.

## Стоимость и cache

Defaults: 10 queries, 20 companies, 60 pages, 2 browser reservations, 24 LLM calls,
60k input / 10k output tokens, 3 iterations, 900 seconds, $1/run, $3/tenant/UTC day.
`RESEARCH_BUDGET_JSON` позволяет изменять лимиты. Непрозрачный PresenceSource
резервирует одну страницу и один возможный browser fallback заранее; это верхняя
оценка, не доказательство запуска браузера.

Перед text-only LLM-вызовом резервируется консервативная оценка input tokens
(UTF-8 bytes плюс framing) и output cap. Ответ с usage корректирует reservation.
Сумма token usage × явно заданный тариф — расчётная стоимость; invoice cost
неизвестен. Повторы — новые вызовы с новым reservation; автоматических retries нет.

Cache tenant-scoped: sources/extraction/qualification — 1 день, market — 7 дней,
DNA — 30 дней; ключи включают content/DNA/model/prompt versions. Истёкший cache
не даёт права использовать устаревший buying signal. Снимки сохраняют исходные
retrieval/publication dates.

Сценарная стоимость: при тарифе $3/$15 за миллион input/output tokens, полный
лимит 60k/10k даёт до $0.33 за LLM-токены; денежный $1 cap остаётся дополнительной
защитой для иных тарифов. Плюс существующая инфраструктура SearxNG/БД/приложения.
Фактических платных вызовов в этой реализации: **0**, фактический LLM-расход: **$0**.
Usage в scripted tests — синтетические данные для проверки accounting, не invoice.

## Проверки и ограничения

Итоговый локальный запуск 9 октября 2026:
`EVOROVE_TEST_POSTGRES=1 .venv/bin/python -m pytest -q` — **302 passed,
4 warnings, 5.32s**. Предупреждения: deprecated alias Starlette/AnyIO и три
уведомления Alembic об отсутствующем `path_separator`. Ошибок тестов нет.
Включены PostgreSQL migration upgrade → downgrade до 0008 → upgrade,
конкурентные резервации/допуск и полный pipeline со scripted внешними сервисами.
`git diff --check` также пройден. Это локальные интеграционные проверки,
а не интеграция с действующим поиском, LLM и CRM.

Client 0 preflight завершился с exit 2: поисковый endpoint не настроен,
research выключен, платные вызовы не разрешены. Search probe не запускался.
Проверка evaluation без размеченного файла возвращает
`labelled_dataset_missing`, exit 2; это зафиксированный блокер оценки качества.

Полная последовательность проверяется scripted search/fetch/LLM/CRM и реальными
SQLite/PostgreSQL. Проверяются также: fabricated refs, invalid quotes, conflicting
claims, wrong geo, stale sources, B2C без выдуманной роли, generic contact,
budget/deadline, reflection revision/termination, cache/tenant isolation,
provider/DB/CRM failure, concurrency, retries и migration roundtrip.

Evaluation harness поддерживает 300 manually labelled cases, шесть профилей,
180 development / 120 holdout, B2B+B2C, проверку company/document leakage,
precision/recall с Wilson CI, abstention, unsupported claims, source validity,
duplicates, cost, nullable downstream metrics и frozen baseline predictions.
Разметку 300 реальных случаев не предоставили. Harness возвращает blocker;
никакие synthetic tests не объявляются рыночной оценкой или precision ≥90%.

Нельзя заявлять production-ready: отсутствует live search pilot, quality dataset,
оплаченная проверка реального LLM, интеграционный прогон с реальной CRM. Строгая
политика требует явной US-географии, датированного buying signal и персонального
контакта; recall на реальном вебе пока неизвестен. Search provider может вернуть
пустой результат и при сбое — статус preflight честно сообщает empty_or_unavailable.
Внешний suppression feed, positive replies и фактический occurred_at не добавлены:
для них потребовалось бы отдельное одобрение внешних контрактов.

Инструкция: [Client 0 runbook](client-zero-intelligence-runbook.md).

## Перечень изменённых и добавленных файлов

Пути относительно корня `evorove_lead`:

- `README.md`
- `docs/client-zero-intelligence-runbook.md`
- `docs/cycle-1-contract.md`
- `docs/market-intelligence-implementation-ru.md`
- `migrations/env.py`
- `migrations/versions/0009_research_evidence.py`
- `migrations/versions/0010_research_resources.py`
- `migrations/versions/0011_research_admission.py`
- `src/evorove_lead/adaptive_search.py`
- `src/evorove_lead/business_dna.py`
- `src/evorove_lead/client_zero.py`
- `src/evorove_lead/crm_touch.py`
- `src/evorove_lead/engine.py`
- `src/evorove_lead/evaluation.py`
- `src/evorove_lead/evidence.py`
- `src/evorove_lead/insights.py`
- `src/evorove_lead/intelligence_store.py`
- `src/evorove_lead/llm_usage.py`
- `src/evorove_lead/market_intelligence.py`
- `src/evorove_lead/opportunity_ranking.py`
- `src/evorove_lead/qualification.py`
- `src/evorove_lead/research_budget.py`
- `src/evorove_lead/research_cache.py`
- `src/evorove_lead/research_config.py`
- `src/evorove_lead/research_delivery.py`
- `src/evorove_lead/research_reflection.py`
- `src/evorove_lead/research_runtime.py`
- `src/evorove_lead/reweight_hypotheses.py`
- `src/evorove_lead/run.py`
- `src/evorove_lead/search_planner.py`
- `src/evorove_lead/searches.py`
- `src/evorove_lead/sqlalchemy_models.py`
- `src/evorove_lead/sqlalchemy_warehouse.py`
- `src/evorove_lead/warehouse.py`
- `src/evorove_lead/web_people_search.py`
- `src/evorove_lead/web_search.py`
- `tests/fixtures/intelligence/README.md`
- `tests/intelligence_helpers.py`
- `tests/test_crm_touch.py`
- `tests/test_engine.py`
- `tests/test_evaluation.py`
- `tests/test_intelligence_foundation.py`
- `tests/test_market_intelligence_engine.py`
- `tests/test_mature_feedback.py`
- `tests/test_postgres_research.py`
- `tests/test_qualification_gate.py`
- `tests/test_research_controls.py`
- `tests/test_research_pipeline.py`
- `tests/test_reweight_hypotheses.py`
- `tests/test_run.py`
- `tests/test_selection.py`
