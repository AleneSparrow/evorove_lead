# Шаг 5: защита загрузчиков

Проверено 24 октября 2026. Worktree:
`/Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead`.
Ветка `codex/cycle1-research-integration`, HEAD `c60f6f0`. Commit, push и
deployment не делались. Незакоммиченные шаги 2–4 сохранены.

## Почему это нужно

Evidence fetcher уже отвергал private redirect. Сайт владельца и
`fetch_page_text` проверяли только исходный хост и схему финального URL:
промежуточный hop на loopback или metadata мог пройти. Playwright открывал
любой URL, включая `file://` и `169.254.169.254`, и следовал редиректам
браузера без той же проверки.

## Механизм

Общий `require_public_http_url` + `PublicRedirectHandler` в `presence.py`.
Запрещены non-http(s), credentials, localhost, `.local`, `.internal`,
известные metadata-имена, literal private/link-local/reserved IP и любой
хост, у которого `getaddrinfo` вернул private адрес. Пустой DNS — отказ.

Применяют:

- `HttpPresenceSource` — старт, каждый redirect, финальный URL;
- `fetch_page_text` и `fetch_source_snapshot`;
- Playwright: проверка до launch, `page.route` на каждый запрос, финальный
  `page.url`.

`WEB_SEARCH_BASE_URL` по-прежнему может быть localhost: это owner-configured
endpoint, не discovered URL.

## Проверки

```sh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
P=/Users/alenakulish/dev/evorove_lead/.venv/bin/python
export PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1 EVOROVE_CRM_REPO=/Users/alenakulish/dev/evorove-crm
"$P" -m pytest -q tests/test_loader_ssrf.py tests/test_rendering.py tests/test_presence.py tests/test_presence_render_fallback.py tests/test_web_search.py
```

Загрузчики: **28 passed**. Полный набор вместе с шагами 6–7: **388 passed, 0 failed**.
Прежний отказ Chromium/`file://` снят: `file://` отвергается до запуска браузера.

## Файлы шага 5

- `src/evorove_lead/presence.py`
- `src/evorove_lead/rendering.py`
- `src/evorove_lead/web_search.py`
- `tests/test_loader_ssrf.py`
- `tests/test_rendering.py`
- `tests/test_presence.py`
- этот отчёт

Миграция не нужна. Sonnet, тарифы, qualification и CRM payload не менялись.

## Границы

Уже отправленный запрос не отменяется. Abort hop в Chromium — не sandbox и
не exactly-once. JS на уже загруженной публичной странице теоретически может
пытаться ходить внутрь; такие запросы abort-ятся, но это не изоляция процесса.
