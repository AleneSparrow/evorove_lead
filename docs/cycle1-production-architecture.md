# Цикл 1 — архитектура квалификации

Цикл 1 по-прежнему заканчивается человеком с причиной на CRM Cold. Новые шаги не заменяют `LeadGenerationEngine`, `WebSearchPeopleSearch`, склад анализа и outbox `crm_deliveries`.

```text
бриф (presence + offer_reader / marketing_analysis)
  -> гипотезы (hypothesis.build_hypotheses, опционально augment_search_plan)
  -> SearxNG (web_search.HttpSearxngWebSearchClient)
  -> свой fetch (page_extract.fetch_extracted_page)
  -> детерминированный prefilter
  -> Claude Haiku, только если AI_PROVIDER=anthropic и цена модели известна
  -> проверка цитаты (evidence_check)
  -> сегмент: opportunity | prospect | review | rejected
  -> Cold только для opportunity, который прошёл прежний отбор кандидата
  -> отзыв (lead_feedback) двигает бюджет одной гипотезы на 1
```

`qualifier=None` (по умолчанию и при `AI_PROVIDER=deterministic`) оставляет прежний путь без вызова модели.

Сегменты лежат в `lead_qualifications`, не в решении кандидата `cold|rejected`. Исторические строки кандидатов не переписываются.

ICP_MATCH — prospect и не уходит в CRM. EXPLICIT_INTENT и INFERRED_OPPORTUNITY становятся opportunity только после проверки цитаты. Письмо по-прежнему цикл 2.
