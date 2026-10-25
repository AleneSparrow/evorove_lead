# Шаг 24 — отдельное ядро «собрать один раз, анализировать из своей базы»

25 октября 2026. Worktree `integration/cycle1-research`. Старый `--research-dry-run` и его таблицы не менялись. Новая точка входа — `python -m evorove_lead.client_zero --source-dry-run`; установленный editable пакет указывает на другой checkout, поэтому запускать из этого worktree с `PYTHONPATH=src`. Класс `SourceCollector` получает только публичные HTTP-страницы; `SourceAnalyzer` получает только SQLAlchemy engine и порт модели. В текущем шаге было **40 внешних HTTP-запросов** разведки с паузами; полный сбор сайтов компаний и платные вызовы не запускались.

## Разведка источников

| Источник | Без входа, robots, условия | Данные и решение |
|---|---|---|
| [HN Algolia API](https://hn.algolia.com/api) | JSON API ответил 200 без входа; `/robots.txt` на `hn.algolia.com` ответил 404. API открыто документирован; отдельного запрета в доступной API-странице не обнаружено. Robots `news.ycombinator.com` требует crawl-delay 30, но этот домен парсер не опрашивает. | **Выбран**: три потока Show HN, Ask HN и комментарии к Who is hiring. JSON даёт `created_at`, заголовок/текст, авторский ник и часто внешний URL продукта. Имя реального основателя и email обычно отсутствуют — извлекаются только с сайта компании. Ник не выдаётся за имя. Потоки делят один источник и одну точку отказа. Дата HN-поста — дата сигнала. |
| [YC directory и jobs](https://www.ycombinator.com/companies) | Профиль и JobPosting доступны без входа; JobPosting имеет `datePosted`, `hiringOrganization`, сайт, текст вакансии. [robots.txt](https://www.ycombinator.com/robots.txt) запрещает `/companies?*`. [Terms of Use](https://www.ycombinator.com/legal#tou) запрещают scraping/data mining/robots. | **Не подключён**. Профиль может дать компанию, основателей, сайт, страну; вакансия — дату и цитату. Автоматический сбор нарушал бы условия. Во время разведки один URL `/companies?batch=F25` был открыт после чтения robots; дальнейшего обхода этого пути нет. |
| [Indie Hackers](https://www.indiehackers.com/) | Публичный пост доступен без входа, JSON-LD даёт автора и `datePublished`; robots не запрещает сам пост. [Terms](https://www.indiehackers.com/terms) прямо запрещают crawling/scraping и значительное копирование контента. | **Не подключён**. Пост мог бы дать имя и цитату, но нельзя строить на нём свой парсер по этим условиям. |
| [Reddit](https://www.reddit.com/robots.txt) | robots доступен; публичный `/r/SaaS/new.json` ответил 403. | **Не подключён**: без входа/ключа JSON недоступен в этой проверке; обхода блокировки нет. |
| Product Hunt | `/robots.txt` и главная ответили 403. | **Не подключён**. Нет проверенного разрешённого бесплатного машинного формата. |
| DEV Community | API статей ответил 200; [Terms](https://dev.to/terms) ограничивают использование материалов личным некоммерческим просмотром. | **Не подключён** для коммерческого поиска. |

Из пяти предложенных семейств только HN прошёл одновременно доступность и ограничения, которые удалось проверить. Требование 2–3 **независимых** источника пока не выполнено. В коде три HN-потока; это не три независимых провайдера. Полный запуск может дать мало сигналов с сайтом и почти не дать проверенных личных email. Добавлять запрещённые парсеры ради количества нельзя.

## Сбор

`SourceAdapter.fetch() -> tuple[RawSignal, ...]`: company_name, person_name, site_url, signal_type, signal_date, source_url, quote. Дополнительные поля хранят весь текст источника, происхождение и тип контекста. Все три HN-потока используют один адаптер с разными режимами; отсутствующий внешний URL или дата дают отказ, а не выдуманный сайт/возраст. Образцы реальных ответов сохранены в `tests/fixtures/source_core/` и проверяются без сети.

`PublicSourceHTTP` применяет `require_public_http_url` и `public_urlopen`, честный User-Agent, robots перед чтением, максимум запросов и интервал не меньше 1 секунды на host. HTTP 404 для robots трактуется как отсутствие файла; другая ошибка закрывает доступ. `SourceCollector` не ходит на сайт Evorove за кандидатами. Для каждого домена он резервирует TTL до HTTP, затем читает не более четырёх страниц: `/`, `/about`, `/contact`, `/team`. При следующем сигнале с этого домена до истечения TTL страницы берутся из базы. Сохраняются нормализованный текст, URL, время, SHA-256 и expires_at. Отказ чтения тоже кэшируется на TTL, чтобы не бить сайт повторно.

Контакт извлекает существующий `heuristic_decision_maker` без модели: имя рядом с ролью основателя/руководителя и личный email, похожий на имя, на собственном домене компании. Общий inbox и неверный синтаксис не проходят. MX проверяется через DNS во время сбора и хранится как `mx_verified`; отказ DNS закрывает кандидата. Письма не отправляются. Дубли компаний/людей/контактов определяются первичными ключами; при анализе одинаковый email на разных доменах получает отдельный отсев `duplicate_person`. `suppress_and_erase` удаляет сырые страницы, сигналы и PII, оставляя tombstone домена со `suppressed=true`, чтобы он не собрался снова.

## Схема

Миграция `0013` добавляет пять таблиц, все с tenant `business_id`:

| Таблица | Ключ и смысл |
|---|---|
| `companies` | `(business_id, domain)`; name, site_url, collected_at, expires_at, suppressed |
| `people` | `(business_id, domain, name_key)`; имя, роль, suppressed |
| `contacts` | `(business_id, domain, email)`; ссылка на person, mx_verified, evidence_url, suppressed |
| `raw_pages` | `(business_id, domain, url)`; text, fetched_at, expires_at, sha256 |
| `signals` | `(business_id, source_url)`; domain, source, signal_type/date, quote, source_text, fetched_at, context_only, is_advertisement |

FK привязывают записи к компании/человеку внутри tenant. Миграция применена **только** к `evorove_s12_dryrun` на 55439: `0012 -> 0013`. Рабочий `DATABASE_URL` не читался и не менялся.

## Анализ и граница Cold

Правила из сохранённых полей отсеивают: suppression, повтор компании, рекламу, событие старше 30 дней (контекст старше 90), отсутствие свежих страниц, личного email с MX, документированной US-локации на сайте компании, признака SaaS/онлайн-бизнеса и точной цитаты в тексте источника. Затем рейтинг: заявленная нужда выше найма sales, найм выше запуска; внутри группы свежее событие выше. Отбор берёт первые `SOURCE_ANALYZE_TOP_N` (по умолчанию 5).

При `SOURCE_ALLOW_PAID_LLM=1` и явно настроенном `claude-sonnet-5-5` модель получает **только сохранённые поля**. Effort `medium`, max output 400 токенов. Она решает fit и выбирает одно предложение причины буквально из исходной цитаты. Причина принимается только как точная подстрока сохранённой цитаты; иначе отказ. Бюджет `SOURCE_MAX_COST_MICROUSD`, по умолчанию 300000 ($0.30), резервируется перед вызовом. Отчёт даёт точный рассчитанный расход по возвращённым токенам, средний расход на рассмотренного кандидата и на Cold-ready лид. Цель $0.02/лид — цель измерения, без измеренного реального прогона она не доказана. Ноль лидов даёт null для цены на лид.

Результат формируется через существующие `Candidate`, `Cycle1Handoff`, `assembled_touch`. В payload есть `signal_type`, `signal_date`, `source_url`, `quote`, `reason`, `evidence_fetched_at` (время съёма цитируемого сигнала). `--source-dry-run` сообщает `cold_ready`; фактическое поле `cold=0`, CRM не вызывается. Анализ не вызывает источник, сайт или DNS; единственная разрешённая внешняя операция на этом этапе — явно включённый вызов модели. Тест запрещает socket.connect при анализе SQLite и проверяет Cold-ready payload.

## Проверки и запуск владельца

Полный набор: **474 passed, 1 skipped, 5 warnings** (было 469 passed, 1 skipped). Skip — контракт с sibling CRM без выбранного checkout. Миграционные тесты, сохранённые образцы HN, robots, интервал, TTL, MX, фильтры, модельный ответ и отсутствие сети в анализе прошли. `--analyze-only` на пустой disposable базе вернул 0 сигналов, 0 Cold-ready и $0. Платных диагностических вызовов сделано **0**: ключ не читался и не задавался агенту.

Следующий блок — для терминала владельца. Он не устанавливает рабочий `DATABASE_URL` и не задаёт CRM-флаги. `SOURCE_MAX_REQUESTS=40` — отдельный потолок будущего запуска, не часть уже выполненной разведки. Сначала можно запустить только сбор; затем, при желании, скрыто ввести API key и отдельно запустить анализ. Полный совмещённый запуск `--source-dry-run` выполняет оба шага, но будет заново опрашивать HN; сайты компаний в пределах TTL из базы повторно не читаются.

```zsh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
(
  export PYTHONPATH=src
  export SOURCE_DATABASE_URL='postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun'
  export EVOROVE_CLIENT_ZERO_BUSINESS_ID='client-zero-s18'
  export SOURCE_MAX_REQUESTS=40 SOURCE_SITE_TTL_DAYS=7
  export SOURCE_ANALYZE_TOP_N=5 SOURCE_MAX_COST_MICROUSD=300000
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --source-dry-run --collect-only
  read -r -s 'ANTHROPIC_API_KEY?Anthropic API key: '
  print
  export ANTHROPIC_API_KEY ANTHROPIC_MODEL='claude-sonnet-5-5' SOURCE_ALLOW_PAID_LLM=1
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --source-dry-run --analyze-only
  unset ANTHROPIC_API_KEY
)
```

До следующего допуска к CRM нужна отдельная интеграция записи готовых payload и проверка независимого второго источника. Текущий шаг заканчивается на dry-run и отчёте.
