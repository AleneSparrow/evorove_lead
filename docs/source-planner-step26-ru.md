# Шаг 26 — каталог источников и планировщик по анализу клиента

25 октября 2026. Worktree `integration/cycle1-research`. Платный вызов модели не запускался. В CRM ничего не писалось. Рабочий `DATABASE_URL` и `.env` не читались. Внешних запросов разведки — **26**, с паузой и User-Agent `EvoroveLead/0.1 (+https://evorove.com; public cycle-1 research)`.

Миграция `0014` применена только к `evorove_s12_dryrun` на порту 55439: `0013 -> 0014`. Она добавляет `signals.source_id` и таблицу `source_plans`. Прежний сбор не перезапускался: в базе по-прежнему 11 сигналов, 0 людей, 0 контактов.

## Каталог

Запись — `SourceSpec`: id, тип, описание, теги ниши, какие данные даёт, разрешён ли автоматический сбор, ссылка и дата проверки условий, ссылка и заметка по robots.txt, нужен ли вход, нужен ли ключ, лимит частоты, статус enabled/disabled и причина. Планировщик и исполнитель берут источник только из этого списка.

| id | Статус | Почему |
|---|---|---|
| `hn_show`, `hn_ask`, `hn_hiring` | enabled | Algolia API ответил 200. `hn.algolia.com/robots.txt` — 404, файла нет. На странице API запрета этого использования не найдено. Ключ не нужен. Это три потока одного API, не три независимых источника. |
| `sec_form_d` | enabled | [Fair access](https://www.sec.gov/os/accessing-edgar-data) разрешает объявленного агента, не быстрее 10 запросов в секунду. `www.sec.gov/robots.txt` явно разрешает `/Archives/edgar/data`. Дневной индекс Form D за 23 октября 2026 отдался с кодом 200 (664 КБ). `efts.sec.gov/robots.txt` ответил 403, этот хост не вызывается. В индексе нет поля сайта: сайт берётся только если текст самой подачи его содержит. Адрес связанного лица в склад не копируется. |
| `searxng_web` | enabled | Свой SearxNG владельца, без платного поискового API и без ключа. База может быть localhost. Каждый URL из выдачи идёт через `require_public_http_url` / `public_urlopen` и robots.txt сайта. До четырёх страниц: `/`, `/about`, `/contact`, `/team`. |
| `osm_overpass` | disabled | `overpass-api.de/robots.txt` 25 октября 2026: `User-agent: *` / `Disallow: /api/`. Интерпретатор лежит под `/api/`. Wiki и страница copyright не открылись (connection refused) и разрешением не считаются. |
| `industry_rss` | disabled | Лента, которая открывается, не есть разрешение. Условия DEV Community — только личный некоммерческий просмотр. Лента SaaStr ответила 200, robots TechCrunch не закрывает путь ленты; разрешения на автоматическое использование статей как источника лидов не найдено. |
| `yc_directory` | disabled | Условия YC запрещают scraping, data mining и robots. robots.txt перечитан 25 октября 2026: `Disallow: /companies?*`. |
| `indie_hackers` | disabled | Условия перечитаны 25 октября 2026: запрет crawling/scraping и существенного копирования. |
| `reddit` | disabled | robots.txt 25 октября 2026: `Disallow: /`. Нужны вход и ключ. Обхода нет. |
| `product_hunt` | disabled | robots.txt вернул 403 и страницу проверки. Бесключевого доступа нет. |
| `dev_to` | disabled | Условия перечитаны 25 октября 2026: только personal, non-commercial, transitory viewing. |

`github.com`, `github.io`, `huggingface.co` и соседние площадки (GitLab, npm, PyPI, news.ycombinator.com и другие из того же списка) не считаются сайтом компании.

## Планировщик

Вход — бриф, поля DNA/ICP, которые уже записаны в анализ: категории покупателей, география, города, услуги, сегменты, сигналы. Для клиента 0 это подтверждённый текст `owner-materials/service.txt` (small SaaS and online businesses in the US). Старые гипотезы про салоны и ремонт в disposable-базе в план не берутся.

Без модели план детерминированный. Включённый источник попадает в список, если он универсальный (`searxng_web`) или его теги есть в тексте анализа, или ему нужен город и город в анализе указан. Выключенный источник не выбирается никогда. Для каждого выбранного источника план задаёт запросы и фильтры из слов клиента. Для каждого невыбранного — причину.

Один вызов модели, Sonnet, effort `medium`, не больше 800 токенов на выходе. В запрос попадают только enabled-источники. Ответ кэшируется в `source_plans` по хешу анализа и версии каталога. Повтор того же анализа модель не вызывает. Проверка ответа: id есть в каталоге, статус enabled, план не пустой, запросы не длиннее 120 символов и их не больше четырёх. Неизвестный или disabled id отвергает весь план, в кэш он не пишется. Придумывать источник нельзя. Пока кэша нет, сбор идёт по детерминированному плану и модель не вызывает.

Хеш анализа клиента 0: `e81170d4df811c811d5dcbf74797af1498eb91d1af43fb889da1d118c3bf35c3`.

## Что выбрал бы детерминированный план

Платный вызов не делался. Ниже план, по которому пойдёт сбор, пока в кэше нет ответа модели. Модель может переставить enabled-источники; disabled она добавить не может.

**Клиент 0.** Выбран `searxng_web`. Запрос: `small SaaS online businesses site US United States`. Фильтр: United States. Hacker News не выбран: в анализе нет слов hacker news, show hn, ask hn, who is hiring, developer forum. Show HN — это хобби и репозитории, не покупатели этого брифа. SEC Form D не выбран: в анализе нет выпуска бумаг или привлечения капитала. OpenStreetMap не выбран: города нет, и источник выключен robots.txt. RSS, YC, Indie Hackers, Reddit, Product Hunt и DEV выключены по условиям.

**Локальная стоматология в Austin.** Тоже выбран только `searxng_web`, но запрос другой: `local dental practice patients book visits Austin`, фильтр Austin. OpenStreetMap по нише подошёл бы (есть город) и в исключении так и написано: анализ совпал, но автоматический сбор запрещён robots.txt. Hacker News и Form D не подходят по тексту анализа.

**Интернет-магазин товаров для питомцев.** Снова `searxng_web`. Запрос: `online store pet supplies shoppers comparing United States`. Города нет, поэтому OpenStreetMap даже как совпадение ниши не отмечается. Hacker News не выбирается.

Если анализ прямо говорит о Form D или capital raise, детерминированный план ставит `sec_form_d` перед SearxNG. Для трёх клиентов выше таких слов нет, поэтому SEC остаётся в каталоге и не входит в их сбор.

## Диагностика прежнего сбора

Сбор заново не запускался.

**Почему «Who is hiring» дал 0.** Адаптер запрашивал 10 свежих комментариев со словом hiring по всему HN и оставлял только те, у которых заголовок треда содержит «who is hiring». Свежие комментарии со словом hiring почти никогда не из этого месячного треда, поэтому фильтр выбрасывал все. Сохранённый `tests/fixtures/source_core/hn_hiring.json` — как раз такой ответ, и разбор даёт 0. Старый тест проверял `all(...)` на пустом кортеже, а это в Python истина. В базе: 10 `show_hn_launch`, 1 `ask_hn_need`, 0 `hn_hiring`. Исправление ищет текущий пост «Ask HN: Who is hiring?» и читает комментарии только его. Тест проверяет, что второй запрос содержит `story_<id>`, и что комментарий этого треда со ссылкой на сайт компании даёт один сигнал, а не ноль.

**Почему нет имён и контактов.** Ник автора HN в имя не записывается. Человек и контакт появляются только если на сайте компании имя стоит рядом с ролью founder/owner/ceo и там же есть необщий email на домене компании, у которого есть MX. В 13 сохранённых страницах такой пары нет. У bellweather.dev есть email и нет роли. У adrs.systems есть роль и нет email. У остальных нет ни того, ни другого. 4 сигнала ушли на github.com и 2 на huggingface.co: это площадки, не сайты компаний, и их страницы не содержат основателя. Теперь такой домен отклоняется до записи компании.

## Проверки

`EVOROVE_TEST_POSTGRES=1`: **480 passed, 2 skipped, 5 warnings**. Было 474 passed, 1 skipped. Семь новых тестов. Прежний skip — контракт соседней CRM без выбранного checkout. Второй skip — `test_postgres_has_qualification_tables_after_migrate`: Postgres на порту 5432 отклонил роль `evorove_lead` с паролем, который записан в самом тесте. Этот тест и эта база не менялись.

## Блок для терминала владельца

Один платный вызов — план источников. Потом сбор по этому плану, без модели. Потом бесплатный разбор уже сохранённых строк правилами. Ключ вводится скрыто и в конце снимается. Рабочий `DATABASE_URL` не задаётся. Если свой SearxNG слушает не `127.0.0.1:8080`, подставьте его адрес.

```zsh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
(
  export PYTHONPATH=src
  export SOURCE_DATABASE_URL='postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun'
  export EVOROVE_CLIENT_ZERO_BUSINESS_ID='client-zero-s18'
  export WEB_SEARCH_BASE_URL='http://127.0.0.1:8080'
  export SOURCE_MAX_REQUESTS=40 SOURCE_SITE_TTL_DAYS=7
  export SOURCE_ANALYZE_TOP_N=5 SOURCE_MAX_COST_MICROUSD=300000
  read -r -s 'ANTHROPIC_API_KEY?Anthropic API key: '
  print
  export ANTHROPIC_API_KEY ANTHROPIC_MODEL='claude-sonnet-5-5' SOURCE_ALLOW_PAID_LLM=1
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --source-dry-run --plan-sources
  unset ANTHROPIC_API_KEY SOURCE_ALLOW_PAID_LLM
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --source-dry-run --collect-only
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --source-dry-run --analyze-only
)
```

Оценка одного планового вызова — около $0.02: вход по каталогу из пяти enabled-источников и не больше 800 токенов ответа при тех же ставках, что уже стоят в разборе ($3 и $15 за миллион). Это потолок оценки, не измеренный счёт. Повтор того же брифа берётся из `source_plans` и модель не вызывает.
