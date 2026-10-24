# Готовность цикла 1 к контролируемому пилоту

## Что можно включить без нового кода

1. Postgres цикла 1, не база CRM и не база `evorove`. `alembic upgrade head` (ревизия `0009` добавляет `lead_qualifications`, `lead_feedback`, `llm_usage`).
2. Секрет `INTERNAL_TASK_SECRET`, тот же, что у CRM.
3. `CRM_BASE_URL` — исходящий Cold. Без него поиск живёт, карточка не уходит.
4. Приватный SearxNG (`searxng/Dockerfile`), `WEB_SEARCH_BASE_URL` на внутренний адрес. Не публиковать инстанс.
5. `AI_PROVIDER=anthropic`, ключ и прежний `ANTHROPIC_MODEL`. Модель остаётся Sonnet (`claude-sonnet-5-5`). Неизвестный id блокирует платный вызов.
6. Образ API из `Dockerfile`: миграции и `uvicorn evorove_lead.api:app`. Порт по умолчанию 8002. `GET /health` без секрета.
7. В CRM указать `EVOROVE_LEAD_BASE_URL`. Поиск: `POST /api/v1/internal/searches`. Отзыв: `POST /api/v1/internal/lead-feedback`.
8. Суточный cron: `POST /api/v1/internal/searches/run-due` или `python -m evorove_lead.searches`.

Писем и SMS этот репозиторий не отправляет.

## Чего нет в этой среде

- Доступа к серверу evorove.com нет. Сайт продукта не означает, что цикл 1 задеплоен.
- Docker здесь не использовался, живой SearxNG не проверялся.
- Платный Anthropic не вызывался. Классификация на фикстурах проверена, на живых страницах — нет.
- Поэтому цикл 1 не готов к коммерческому пилоту, пока владелец не прогонит ограниченный живой поиск и не разберёт выборку.

## Проверка после выкладки

- `GET /health` отвечает `status=ok`.
- Поиск без `WEB_SEARCH_BASE_URL` даёт 503, а не выдуманных людей.
- Один бизнес, маленький потолок `LEAD_MAX_USD_PER_RUN`, затем сверка `llm_usage` и сегментов.
- Повтор того же документа не создаёт вторую квалификацию и не шлёт второй Cold (`touch_id`, outbox `crm_deliveries`).
- Чужой `business_id` не читает чужие квалификации.
