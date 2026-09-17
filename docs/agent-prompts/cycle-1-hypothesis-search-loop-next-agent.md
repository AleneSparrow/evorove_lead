# Prompt: цикл 1 — гипотезы, парсинг, обратная связь (12 сентября 2026)

Якорь — `/Users/alenakulish/dev/evorove/FOUNDATION.md` и этот репозиторий, `docs/cycle-1-contract.md`. Этот файл их не отменяет, только раскладывает на шаги. Цикл 1 **не пишет** человеку, **не бронирует**, **не берёт лидов с сайта клиента**. Он решает только: можно ли положить человека на вкладку Cold.

Порядок жёсткий. Следующая фаза не начинается, пока у предыдущей нет «готово, когда».

```text
Context to read first
You are working inside the `evorove_lead` repository. All paths below are
relative to this repo's root, except the first one (a different repo).
- /Users/alenakulish/dev/evorove/FOUNDATION.md  (sister repo — anchor doc, read-only)
- docs/cycle-1-contract.md
- src/evorove_lead/business.py
- src/evorove_lead/offer.py
- src/evorove_lead/offer_reader.py
- src/evorove_lead/candidate.py
- src/evorove_lead/search.py
- src/evorove_lead/presence.py
- src/evorove_lead/crm_touch.py
- src/evorove_lead/handoff.py
- src/evorove_lead/policy.py
- README.md

Hard constraints (do not cross)
- No messaging, no booking, no writing to the person. Cycle 1 only decides Cold-eligibility.
- The client's own site/visitors are never a people source — only the brief.
- No LinkedIn scraping, no paywalled sources, no purchased contact lists, no Microsoft Ad
  Library as a US people source (brief only, EEA impressions).
- git push: never. Secrets (.env, DATABASE_URL, API keys): never read, never request.
- Do not weaken existing tests to fit new code.
```

---

## Фаза 0 — Postgres склада анализа

Сейчас `evorove_lead` без своей БД: следы и отказы негде хранить отдельно от CRM (контракт уже требует это, физически не сделано).

**Задачи:**
1. Добавить Alembic (или аналогичный уже принятый в других репо инструмент) + `docker-compose` сервис Postgres для `evorove_lead`, по образцу `evorove-crm/migrations/`.
2. Схема, tenant-scoped везде (`business_id`):
   - `briefs` — снимок `OfferUnderstanding` по времени (не перезаписывать, копить историю брифа).
   - `hypotheses` — `id, business_id, brief_id, audience_segment, channel, query_template, intent_trigger, status (live|dead|paused), fit_score, evidence_score, reach_estimate, created_at`.
   - `traces` — `id, hypothesis_id, url, fetched_at, raw_text, query_used, language, geo_hint, source_channel`.
   - `rejected_traces` — `trace_id (nullable FK), reason, rejected_at` (тема/гео/открытость/свежесть — как в контракте).
   - `candidates` — `id, hypothesis_id, trace_id, identity, email, phone, channel, reason, reason_source, fit, evidence, addressable, decision (cold|rejected), decided_at`.
   - `hypothesis_outcomes` — `hypothesis_id, case_id (из evorove-crm), outcome (done|dropped|offer_made|in_progress), recorded_at` — заполняется фазой 3.
3. Репозитории/протоколы по аналогии с существующими портами (`PresenceSource`, `PeopleSearch`) — не ORM напрямую в движке.

**Готово когда:** есть миграции, тесты на запись/чтение каждой таблицы с tenant-скоупом, `engine.py` может писать сырые следы и отказы без похода в CRM.

---

## Фаза 1 — `HypothesisSet` вокруг триггера намерения

Сейчас `OfferUnderstanding` — статичный портрет (услуга/аудитория/гео/что нельзя обещать). Нужен промежуточный слой, который превращает его в несколько проверяемых гипотез.

**Задачи:**
1. Новый модуль `evorove_lead/hypothesis.py`:
   - `@dataclass IntentTrigger`: `kind` (`public_ask` | `competitor_complaint` | `need_statement` | `demographic_fit`), `description`.
   - `@dataclass Hypothesis`: `audience_segment, channel, query_template, intent_trigger: IntentTrigger, geo_radius`.
   - `build_hypotheses(offer: OfferUnderstanding, geo_radius: GeoRadius) -> tuple[Hypothesis, ...]` — генерирует несколько гипотез: минимум одну по демографии (как раньше) и минимум одну-две вокруг триггера намерения (человек публично спрашивает/жалуется на конкурента/описывает потребность).
2. **Лёгкая проверка (`verify_hypothesis`)** — 1–2 пробных запроса на канал через тот же `PeopleSearch`-протокол, без полного парсинга. Считает `reach_estimate` (сколько публичных следов отозвалось) и помечает гипотезу `dead`, если пусто или явный мусор (используя те же критерии «тема/гео/открытость» из `candidate.py`, но в облегчённом виде).
3. Приоритизация живых гипотез по `fit_score`/`evidence_score` перед передачей в фазу 2 — никаких магических весов, логика read-only и тестируемая.

**Готово когда:** из одного `OfferUnderstanding` получается список гипотез с полем `intent_trigger`, мёртвые гипотезы отфильтрованы `verify_hypothesis` автоматически, юнит-тесты покрывают и демографическую, и триггерную ветку.

---

## Фаза 2 — один коннектор, пилотный гео-радиус, путь до Cold

**Задачи:**
1. Выбрать **один** источник для первого сквозного прогона — публичный веб-поиск через то API, которое владелец одобрит (не LinkedIn, не платное закрытое). Реализовать как `PeopleSearch`-конкретный класс (по аналогии с `UnconnectedPeopleSearch`, но реально подключённый), принимающий `Hypothesis` и возвращающий `PeopleHit`/`trace`.
2. Каждый сырой след обязательно идёт в `traces` (фаза 0) с `query_used` и `hypothesis_id` — без этого нечем будет проверить источник на шаге повторного анализа.
3. `geo_radius` по умолчанию — **город/зона клиента из брифа**, не весь рынок США. Параметр явный, не хардкод.
4. Прогнать существующую логику `candidate.py` (повторный анализ) по следам нового коннектора без изменения самой логики отбора — только подать ей реальные данные вместо JSONL-заглушки.
5. Принятые кандидаты уходят в CRM Cold через существующий `crm_touch.py`/`handoff.py` — без изменений контракта хэндоффа.

**Готово когда:** минимум один реальный источник проходит путь «гипотеза → запрос → след → повторный анализ → запись на вкладке Cold» без ручной подкладки JSONL, с трассировкой каждого шага в складе анализа.

---

## Фаза 3 — исход из цикла 2 возвращается в цикл 1

**Эта фаза затрагивает два других репозитория** (`/Users/alenakulish/dev/evorove` и `/Users/alenakulish/dev/evorove-crm`), не только `evorove_lead`. Если вы работаете в отдельной сессии, ограниченной одним репозиторием, задачу 1 ниже нужно выполнять в соответствующей сессии для `evorove`/`evorove-crm`, задачу 2 — в сессии для `evorove_lead`. Сообщите об этом владельцу, а не пытайтесь открыть чужой репозиторий из текущей сессии.

**Задачи:**
1. В `evorove-crm`/`evorove` (на стороне, где лид проходит `SalesStage`) добавить событие на переходах `Done` и явный отвал (`dropped`/STOP/таймаут) — **не PII, только `hypothesis_id → outcome`**. `hypothesis_id` должен доехать до Cold-записи в handoff-объекте (поле уже предусмотрено контрактом как метаданные, не придумывать новое хранилище персональных данных).
2. В `evorove_lead` — эндпоинт/задача, принимающая такие события и пишущая в `hypothesis_outcomes` (фаза 0).
3. Никаких PII не пересекает границу репозиториев в эту сторону — только агрегируемый исход.

**Готово когда:** закрытие или отвал конкретного лида в цикле 2/3 отражается в `hypothesis_outcomes` цикла 1 без передачи персональных данных обратно.

---

## Фаза 4 — переоценка бюджета гипотез + библиотека паттернов

**Задачи:**
1. Job/скрипт `reweight_hypotheses.py`: считает `accept_rate` (доля dossiers, дошедших до Cold) и `close_rate` (доля из Cold, дошедших до Done) по каждой гипотезе; гипотезы с низким `close_rate` за N прогонов — `paused`, с высоким — получают больше бюджета следующего прогона (параметр частоты/охвата запросов).
2. Новая системная (не тенантская!) таблица/файл `hypothesis_pattern_library`: абстрактные шаблоны `business_archetype → channel_family → query_pattern → observed_close_rate_band`, **без** контактов, текстов следов или идентификации конкретного бизнеса/человека. Явно зафиксировать в коде и в докстроке эту границу.
3. При создании брифа для нового бизнеса — `build_hypotheses` сначала смотрит в библиотеку паттернов по архетипу бизнеса, чтобы не стартовать с нуля.

**Готово когда:** есть автоматический пересчёт бюджета гипотез по исходам, и библиотека паттернов заполняется/используется без утечки тенантских данных между бизнесами.

---

## Фаза 5 — расширение гео и второй источник — по метрике

**Условие входа:** фаза 2–4 дали стабильный `accept_rate`/`close_rate` выше согласованного с владельцем порога на пилотном гео-радиусе.

**Задачи:**
1. Расширение `geo_radius` кольцами (город → область обслуживания → штат), каждое расширение — отдельный прогон с собственной метрикой, не «включить всё сразу».
2. Второй источник добавляется только после того, как первый стабильно отдаёт лидов с нужным качеством — как отдельная реализация `PeopleSearch`, с тем же набором ограничений (не LinkedIn/пейвол/купленные базы).

**Готово когда:** расширение зафиксировано как решение на основе метрик из фазы 4, а не как план «на всякий случай».

---

## Не делать на всём этом треке

- Не тащить письмо/SMS/GREET в `evorove_lead`.
- Не класть сырые следы/HTML в CRM.
- Не считать посетителей сайта клиента лидами.
- Не передавать персональные данные из цикла 2 обратно в библиотеку паттернов — только агрегаты.
- Не включать расширение гео или второй источник без метрики из фазы 4.
