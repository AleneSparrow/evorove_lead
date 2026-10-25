# Шаг 23 — клиент 0 и первая волна поиска, 25 октября 2026

Работа в `integration/cycle1-research`, исходный HEAD `1b8a30e`. Платные вызовы и `--research-dry-run` не запускались. Выполнено ровно 12 HTTP-запросов к локальному SearxNG; сайты кандидатов не открывались. CRM, outbox и рабочие данные не изменялись. При входе уже были изменения в research_runtime.py и двух тестовых файлах; они сохранены и доведены до проходящего поведения.

## 1. Описание клиента

Было:

```text
Sales automation and lead generation for small local service businesses.
Evorove finds people who fit the business in the open web, puts them on a sales board with a reason, and sells until the deal closes or an appointment is booked.
It also serves independent salons.
It helps local repair shops.
```

Стало:

```text
Evorove automates lead generation and sales for small SaaS and online businesses in the US.
It finds potential customers on the open web, analyzes their fit, and adds them to a CRM board with a grounded reason.
Evorove then handles sales conversations through closing, with progress visible in the CRM.
It offers a 7-day free trial with no demo required, followed by a $199/month subscription.
The goal of the first week is to show results such as replies and bookings.
```

Это 5 предложений. ICP, trial, отсутствие демо, подписка и цель первой недели подтверждены владельцем. Цена подтверждена не новым веб-запросом, а first-party снимком `https://evorove.com`, fetched_at `2026-10-24T13:27:42.540171Z`, source_id `0aded553e3f21b7789d5878ca5c91c14c5cfbbbcd35dd8498bf0cb120f9a6710`: “Seven-day trial. No prompt engineering. $199/mo after trial — instead of a hire.” Первая неделя — цель, не гарантия. README исправлен; fixture чтения owner-materials в test_client_zero.py обновлён. Салоны в остальных синтетических тестах проверяют универсальность движка и не читают настоящий service.txt; их не заменяли на SaaS.

## 2. Кэш и цена нового запуска

В research_runtime.py `dna_key = cache.key(DNA_CACHE_KIND, seed.site_url, self.model, *(s.content_hash for s in sources))`. `DNA_CACHE_KIND = dna-3-provenance`; ResearchCache.key — digest частей через перевод строки, tenant — часть первичного ключа. TTL DNA 30 дней. Новый service.txt меняет content_hash, даёт cache miss и обычно один вызов BusinessDNAEngine.analyze (при невалидном JSON возможен дополнительный repair). Оценка владельца около $0.04 за DNA, это не измеренная цена нового текста.

Рынок также пересоберётся: ключ `market-1-integrated + model + dna.model_dump_json() + hints`, TTL 7 дней. Его старый отчёт нельзя переносить на новый ICP. Снимки отдельных URL имеют отдельный однодневный кэш. Цена по заданным тарифам: `(input_tokens * 3 + output_tokens * 15) / 1000000`; invoice_cost в ledger null, это расчёт, не счёт провайдера.

## 3. Что реально произошло в attempt_570d9384b353470b8b060215de6bedf4

Чтение только disposable Postgres `127.0.0.1:55439/evorove_s12_dryrun`, tenant `client-zero-s18`. Время: 24 октября 2026, 20:46:42.400021–20:47:40.402713 UTC+7. Статус `budget_exhausted`, stop_reason `iterations`, dry_run=true, Cold/qualified/handoffs=0.

Полный сохранённый рыночный отчёт, включая все утверждения, цитаты и снимки источников: [step23-market-report.json](step23-market-report.json). Полные тексты двух сохранённых ICP: [step23-saved-icp.json](step23-saved-icp.json). Это экспорт из базы, не повторный модельный анализ.

Рынок: confidence=0.4, источники Merchant Growth, QuoteIQ, ActiveProspect, Baadigi и Hatch. Приоритеты буквально:

- Test whether salons and repair shops, not just contractors, want outbound prospecting and sales conversation automation
- Compare Evorove's $199/mo flat positioning with per-unlock pricing (QuoteIQ Atlas) and per-lead marketplace costs
- Collect customer results or pilot data on booked appointments per cost
- Map how owners currently handle follow-up and lead response

Неизвестные буквально:

- Whether small local salons and repair shops specifically buy outbound sales automation; sources focus mostly on contractors and home services
- Actual demand, willingness to pay, and conversion results for Evorove's offer
- Whether the private-beta product is available to buyers
- Whether $199/mo covers all usage
- How Evorove compares in effectiveness to the named alternatives
- Buyer roles and decision process at small local businesses (likely the owner, but not evidenced)

Все market claim texts (полные цитаты и связи в JSON):

- `c1`: A Merchant Growth article, a business-authored first-party source, says small businesses are adopting automation and digital tools and that AI is becoming part of everyday operations. This is general and Canada-focused, so it only loosely supports demand in the US.
- `c2`: QuoteIQ says its Atlas map lets users search an area for local businesses and pay per business to unlock contact details, with plans from $29.99/mo. This is a vendor claim about a competing alternative, aimed at contractors.
- `c3`: BaaDigi claims marketplace leads are shared with several contractors and close at 5-20%, which is a competitive alternative and a cost barrier for contractors. The page is a vendor's own comparison.
- `c4`: BaaDigi claims the average contractor takes over 14 hours to call back and that fast response improves qualification. This suggests a trigger for automated conversation, though it is about contractors, not salons or repair shops.
- `c5`: Hatch offers always-on AI for SMS and email conversations and 24/7 call handling aimed at home services and home improvement. It is an adjacent alternative that responds to leads rather than finding prospects.
- `c6`: The retrieved sources concern contractors and home services, or are general Canadian small business trends. None directly evidences demand among independent salons or local repair shops in the US, so segment fit is an inference to be tested.

Сохранённые ICP: (1) small local service businesses/home services, confidence 0.3; (2) local independent repair shops, confidence 0.25. Для третьей гипотезы есть только icp_version `71bb4ca493068706c2240aa3197f0a82088b8c22a7bc1a43be709e5b0abd4113` и ранг; полный ответ HypothesisSet не сохранялся. Восстановить её текст из этой базы невозможно. Новый код сохраняет все предложенные ICP до распределения запросов (`icp_proposed`). Более поздний attempt_7a8ca… не смешан с выбранным прогоном.

Все сохранённые запросы к людям этого attempt:

1. Выполнен:
```text
"Owner may say publicly that marketplace leads are shared, close poorly and cost " "Small local service businesses (including home services) in the US that buy shar" United States
```
2. Подготовлен для следующей гипотезы, но НЕ выполнен из-за iterations:
```text
"Owner may say publicly that work is inconsistent and they cannot spend time on s" "Local independent repair shops in the US (phone, appliance, auto or similar)" United States
```

Для запроса 1 сохранено 0 traces, но это НЕ доказанные 0 поисковых хитов: reflection фиксирует infrastructure_failure=true, run.search_errors=[search_sources_unresponsive]. Число результатов отказавшего провайдера не сохранено. Для запроса 2 число хитов неприменимо — запроса не было. people_queries=1, общий queries=4 (остальные 3 — рынок), pages=11, companies=0, browser_calls=1, iterations=1. Отсева кандидатов в этом attempt не было: rejections={}, qualification не началась. Четыре rejected_traces tenant из более позднего прогона (2 no_sources, 2 no_published_contact) не относятся к этому attempt.

Расход по стадиям, без округления:

| Стадия | Input | Output | USD |
|---|---:|---:|---:|
| DNA | 0 платных токенов, cache hit | 0 | 0 |
| Market plan | 588 | 336 | 0.006804 |
| Market interpret | 12354 | 2066 | 0.068052 |
| ICP generation | 18602 | 1664 | 0.080766 |
| People search / qualification / outreach | 0 | 0 | 0 |
| Итого | 31544 | 4066 | 0.155622 |

Ledger не хранит stage name; строки сопоставлены порядку трёх вызовов и фактическим стадиям runtime. Reserved и completed не складывались как два расхода.

Причинность: неверный ICP испортил цель исследования и рынок ещё до поиска. Даже успешный поиск салонов не проверял бы нужный SaaS ICP. Запросы дополнительно испорчены буквальными длинными цитатами, включая “Owner may say publicly” и обрезанное “shar”: это маловероятные точные совпадения. Но измеренный ноль нельзя разложить на проценты «ICP против кавычек»: был инфраструктурный отказ и остановка после первой гипотезы. Нужен разрешённый следующий прогон; нынешние данные не показывают отсутствия спроса.

## 4. Источники нового ICP

Каждый источник даёт повод исследовать человека, не разрешение на Cold. US подтверждается отдельным фактом о компании/человеке; слово US в запросе этого не доказывает. Имя/контакт должны принадлежать подходящему человеку, не общему support inbox.

| Источник и сигнал | Пример SearxNG | Возможные данные | Свежесть и ограничения |
|---|---|---|---|
| Product Hunt: свежий запуск | `site:producthunt.com SaaS first customers launch 2026` | продукт, maker/основатель, ссылка на сайт; контакт часто отсутствует | дата конкретного запуска <=90 дней; запуск сам по себе не доказывает потребность |
| Вакансия первого SDR/sales | `SaaS startup hiring first SDR United States 2026` | компания, сайт вакансии, иногда hiring manager | дата публикации и незакрытая роль; искать маленькую команду, не корпорацию; найм не равен готовности купить Evorove |
| Indie Hackers: первые клиенты/застой роста | `site:indiehackers.com SaaS first customers struggling 2026` | автор, продукт, точный рассказ о проблеме, иногда сайт | дата самого поста, не комментария или индексирования; отсеять советы/рекламу сервиса |
| Reddit: публичная проблема привлечения | `site:reddit.com/r/startups founder struggling customer acquisition 2026` | ник, цитата проблемы, иногда продукт | дата исходного поста <=90 дней; без доказанной личности, US и опубликованного личного контакта Cold невозможен |
| Seed/pre-seed раунд | `SaaS startup seed funding United States founder 2026` | компания, основатели, сайт, дата раунда | дата события; раунд — контекст роста, не доказанный sales pain; общие funding обзоры отсеять |
| YC каталог + конкретная sales-вакансия | `site:ycombinator.com/companies SaaS United States sales hiring` | имена основателей, сайт, город, команда, вакансии | batch сам по себе не актуальный сигнал; проверить дату конкретной вакансии/запуска |
| Crunchbase, только открытая часть | `site:crunchbase.com SaaS seed funding United States 2026` | профиль, основатели, страна, раунд | сверить дату раунда с открытым объявлением компании/инвестора; без paywall и обхода логина; каталог не lead list |

Результаты ровно 12 запросов ниже. Число — длина массива results, не размер всего индекса. Сохранены первые 5 результатов каждого запроса, URL, заголовок, сниппет и ошибки движков в [step23-searx-results.json](step23-searx-results.json). Даты сниппетов и факты не перепроверялись открытием страниц. Нельзя считать год в запросе подтверждением свежести.

1. `site:producthunt.com SaaS launch founder United States 2026` — results=39.

   - `support.microsoft.com` — [About Get Help | Microsoft Support](https://support.microsoft.com/en-us/support/get-help/about-get-help)
   - `www.producthunt.com` — [Best of Product Hunt: 2026 | Product Hunt](https://www.producthunt.com/leaderboard/yearly/2026)
   - `www.producthunt.com` — [Kelviq: Payments, tax, and billing for SaaS & AI companies | Product Hunt](https://www.producthunt.com/products/kelviq)

   Оценка: Kelviq — повод изучить продукт; US, founder contact и потребность не доказаны. Microsoft Support — явный шум.

2. `site:producthunt.com SaaS first customers launch 2026` — results=28.

   - `www.producthunt.com` — [Rectify: The AI ops hire for your SaaS - Product Hunt](https://www.producthunt.com/products/rectify)
   - `www.producthunt.com` — [SaaS - Product Hunt](https://www.producthunt.com/topics/saas)
   - `www.producthunt.com` — [The best marketing & sales software to use in 2026 - Product Hunt](https://www.producthunt.com/categories/marketing-sales)

   Оценка: Rectify — SaaS-кандидат для изучения, но нет US/контакта/сигнала нужды; категории PH не кандидаты.

3. `SaaS startup hiring first SDR United States 2026` — results=30.

   - `www.ziprecruiter.com` — [$42k-$75k Saas Sdr Jobs (NOW HIRING) Oct 2026 - ZipRecruiter](https://www.ziprecruiter.com/Jobs/Saas-Sdr)
   - `topstartups.io` — [Sales Development Representative Startup Jobs](https://topstartups.io/jobs/?role=Sales+Development+Representative)
   - `www.saas-talent.com` — [SaaS Executive Search & Hiring for Startups - SaaS Talent](https://www.saas-talent.com/talent-recruiting)

   Оценка: Списки вакансий — пути к компаниям, не люди; нужен конкретный работодатель и дата роли.

4. `SaaS founder hiring sales development representative remote USA 2026` — results=38.

   - `detail.chiebukuro.yahoo.co.jp` — [https://poki.jp/ - このサイトについて質問です。このサ... - Yahoo!知恵袋](https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/q11247202096)
   - `www.linkedin.com` — [Sales Development Representative (saas) Jobs in United States ...](https://www.linkedin.com/jobs/sales-development-representative-%28saas%29-jobs)
   - `www.indeed.com` — [Saas Sales Development Representative jobs in Remote](https://www.indeed.com/q-Saas-Sales-Development-Representative-l-Remote-jobs.html)

   Оценка: Выдача загрязнена; LinkedIn не открывался и не используется. Общая выдача вакансий не Cold.

5. `site:indiehackers.com SaaS first customers struggling 2026` — results=20.

   - `www.indiehackers.com` — [Struggling to get my first SaaS users - how did you do it?](https://www.indiehackers.com/post/struggling-to-get-my-first-saas-users-how-did-you-do-it-d5afe6c382)
   - `www.indiehackers.com` — [The single point of failure that kills most indie SaaS startups within ...](https://www.indiehackers.com/post/the-single-point-of-failure-that-kills-most-indie-saas-startups-within-six-months-sOk1OncrWdDJ8n5WunG7)
   - `www.indiehackers.com` — [I Tested Agenmatic for Finding Customers in Communities](https://www.indiehackers.com/post/i-tested-agenmatic-for-finding-customers-in-communities-here-s-what-i-learned-c5fb31ccf7)

   Оценка: Пост о OptiGain релевантен боли, но датирован 2025 и устарел; остальные статьи/продвижение требуют отсева.

6. `site:indiehackers.com SaaS customer acquisition United States` — results=20.

   - `www.indiehackers.com` — [Customer lifetime value (LTV) — SaaS businesses' ingredient for ...](https://www.indiehackers.com/post/customer-lifetime-value-ltv-saas-businesses-ingredient-for-success-4c325c65f2)
   - `www.indiehackers.com` — [Exiting his agency and hitting $10k MRR within 60 days of launching ...](https://www.indiehackers.com/post/tech/exiting-his-agency-and-hitting-10k-mrr-within-60-days-of-launching-his-saas-sTo5m9qfhxXbruvhBIyH)
   - `www.indiehackers.com` — [Buying Chess.com for $56k and growing it to $200M in annual ...](https://www.indiehackers.com/post/creators/buying-chess-com-for-56k-and-growing-it-to-200m-in-annual-revenue-GkkdbA1ibyb3KUxKhFyT)

   Оценка: Lancer — повод изучить автора; рост до $10k MRR не доказывает проблему привлечения. Общие статьи отсеять.

7. `site:reddit.com/r/SaaS first paying customers US 2026` — results=30.

   - `dictionary.cambridge.org` — [FIRST | English meaning - Cambridge Dictionary](https://dictionary.cambridge.org/dictionary/english/first)
   - `firstslide.vn` — [Trang chủ - First Industries](https://firstslide.vn/)
   - `www.reddit.com` — [I spent 4 hours a day on Reddit to get my first 50 customers. Here's ...](https://www.reddit.com/r/SaaS/comments/1rydxcm/i_spent_4_hours_a_day_on_reddit_to_get_my_first/)

   Оценка: Пост о первых 50 клиентах скорее успешный кейс, не текущая потребность; словари и промышленный сайт — шум.

8. `site:reddit.com/r/startups founder struggling customer acquisition 2026` — results=20.

   - `www.reddit.com` — [YC may have already peaked, and the data is starting to show their ...](https://www.reddit.com/r/startups/comments/1vpuexs/yc_may_have_already_peaked_and_the_data_is/)
   - `www.reddit.com` — [I'm tired of searching for a technical cofounder - I will not promote](https://www.reddit.com/r/startups/comments/1oohiou/im_tired_of_searching_for_a_technical_cofounder_i/)
   - `www.reddit.com` — [Founders, this is how I am spending in marketing. Am I burning ...](https://www.reddit.com/r/startups/comments/1x6lhmr/founders_this_is_how_i_am_spending_in_marketing/)

   Оценка: Пост о маркетинговых тратах от 15 октября 2026 — перспективный след; автор, US, SaaS и контакт не установлены.

9. `SaaS startup seed funding United States founder 2026` — results=30.

   - `fundraiseinsider.com` — [List of Funded SaaS Startups (2026) - Fundraise Insider](https://fundraiseinsider.com/blog/recently-funded-saas-startups/)
   - `www.facebook.com` — [Facebook](https://www.facebook.com/)
   - `www.facebook.com` — [Log into Facebook](https://www.facebook.com/login.php/)

   Оценка: Funding list даёт компании, но не личную потребность; Facebook/login не подходят.

10. `site:ycombinator.com/companies SaaS United States sales hiring` — results=20.

   - `www.ycombinator.com` — [SaaS Startups funded by Y Combinator (YC) in New York that are ...](https://www.ycombinator.com/companies/industry/saas/new-york/hiring)
   - `www.ycombinator.com` — [SaaS Startups funded by Y Combinator (YC) in the San Francisco ...](https://www.ycombinator.com/companies/industry/saas/san-francisco-bay-area/hiring)
   - `www.ycombinator.com` — [SaaS Startups funded by Y Combinator (YC) in Los Angeles 2026](https://www.ycombinator.com/companies/industry/saas/los-angeles)

   Оценка: Региональные каталоги помогают US-фильтру; отдельная SDR-вакансия Simple AI (в JSON) — след, размер/fit требуют проверки.

11. `site:crunchbase.com SaaS seed funding United States 2026` — results=20.

   - `news.crunchbase.com` — [These Are Sectors Where Seed Rounds Of $5M To $10M Are ...](https://news.crunchbase.com/seed/startup-funding-trends-2026-proptech-robotics-cancer-space-tech/)
   - `news.crunchbase.com` — [Crunchbase Data: Q3 2026 Posted A Record Count Of Billion-Dollar ...](https://news.crunchbase.com/venture/q3-2026-global-startup-funding-ai-billion-dollar-rounds-exits-data/)
   - `news.crunchbase.com` — [Fintech Funding Fell 21% In Q3 As Deals Grew Scarcer](https://news.crunchbase.com/venture/q3-2026-fintech-startup-funding-deal-count-fell-ai/)

   Оценка: Новости об общих раундах и секторах не персональные кандидаты.

12. `online business founder customer acquisition struggling USA 2026` — results=27.

   - `poki.com` — [Free Online Games at Poki - Play Now!](https://poki.com/?msockid=0dc111e38d716ae03060061e8cc06b4f)
   - `www.crazygames.com` — [Free Online Games on CrazyGames | Play Now!](https://www.crazygames.com/)
   - `randygage.com` — [Entrepreneur Statistics: USA 2026 - Randy Gage](https://randygage.com/entrepreneur-statistics-usa/)

   Оценка: Игры и статистические статьи — нерелевантная выдача, кандидатов среди показанных нет.

Отказы агрегатора встречались у brave (rate limit), qwant (access denied), yahoo (parsing error), позднее duckduckgo (CAPTCHA). Некоторые результаты нарушают site-ограничение по смыслу и домену. Короткие запросы дают материал, но качество конкретного агрегатора и движков нужно учитывать отдельно. Из 12 запросов не получено ни одного проверенного Cold: контакты/география/evidence не проверялись, это разведка выдачи.

## 5. Исправление и проверки

Сначала новый тест `test_queries_follow_hypothesis_signals_and_strategy_for_saas` упал: ни один запрос не содержал hiring first SDR. До исправления существующие незакоммиченные тесты дали 5 failed, 27 passed. Теперь генератор использует segment/potential_problem/signals/search_strategy из ICP, убирает поясняющую прозу и кавычки, ограничивает запрос 120 символами, сохраняет географию; нет отраслевых веток и фиксированных looking-for/need шаблонов. Prompt ICP просит короткие фразы и естественные поисковые стратегии. Это детерминированное сжатие, не новый платный SearchPlanner-вызов. Длинные тексты всё ещё могут терять нюансы — это ограничение, не гарантия качества запросов.

Сохраняются все предложенные ICP и people_search: query, hypothesis_key, hits, error, rejections. iterations трактуется как максимум шагов на одну гипотезу: при 4 гипотезах и iterations=3 возможны 12 шагов; общий счётчик usage показывает сумму. Общие limits запросов, денег, времени, страниц и LLM остаются на весь run. Отдельный тест проверяет, что второй scope не обходит общий query budget. Это исправляет уже существующий падающий тест первой волны, не ослабляя QualificationGate. На каждый ICP runtime начинает с первого запроса; дальнейшие шаги — bounded reflection, а не обещание выполнить весь предложенный набор.

Проверки США, адресности, дубликатов, provenance/evidence и fresh timing не изменены. Цикл 2 не затронут.

## 6. Поле «причина»

QualificationGate выдаёт `reason = link.quote`, `reason_source = source.final_url` для первого reason_claim. Это реальная цитата ситуации кандидата, не слоган Evorove и не summary рынка. Полные claims, source IDs, source.published_at/event_at/fetched_at хранятся в evidence bundle и qualification dossier. Проверки: источник получен <=30 дней назад, подтверждённый сигнал <=90 дней, дата съёма не заменяет дату события.

Однако в самом компактном поле reason нет отдельных signal_date/signal_type: в Cold передаются цитата и URL; дата остаётся в research evidence и может относиться к timing claim, а не к выбранной цитате. Такая пара пригодна как фактическая основа персонализации после чтения dossier, но не как самодостаточный пакет «сигнал + дата + источник + цитата». В выбранном attempt нет лидов и нет сформированного reason. Первое касание не писалось и не изменялось.

## 7. Следующий запуск — только в терминале владельца

Ожидаемая базовая цена с пересборкой: DNA ~$0.04 + прошлые market_plan $0.006804 + market_interpret $0.068052 + ICP $0.080766 = ориентир ~$0.195622 до извлечения кандидатов и reflection. Это сравнение с прошлым количеством токенов, не точный прогноз нового рынка. На весь небольшой прогон разумный ориентир $0.3–$1; жёсткий лимит $2/run и $2/UTC-day, дневной остаток может остановить раньше. Предварительные reservations тоже способны остановить вызов раньше фактического расхода. Точная цена неизвестна до выполнения; тарифы здесь заданы владельцем, актуальные прайсы не запрашивались.

Блок не запускался. Использует только disposable базу и очищает унаследованные CRM-настройки; stage output limits остаются defaults шага 22 (market_interpret=8000). Ключ вводится локально скрыто и удаляется из shell по завершении; агент его не читает.

```zsh
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
(
  unset CRM_BASE_URL RESEARCH_CRM_DELIVERY CRM_CYCLE2_ISOLATED
  unset RESEARCH_MAX_OUTPUT_TOKENS_BY_STAGE
  export DATABASE_URL='postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun'
  export WEB_SEARCH_BASE_URL='http://127.0.0.1:8080'
  export EVOROVE_CLIENT_ZERO_BUSINESS_ID='client-zero-s18'
  export ANTHROPIC_MODEL='claude-sonnet-5-5'
  export RESEARCH_ENABLED=1 RESEARCH_ALLOW_PAID_LLM=1
  export RESEARCH_INPUT_USD_PER_MTOK=3 RESEARCH_OUTPUT_USD_PER_MTOK=15
  export RESEARCH_MAX_OUTPUT_TOKENS=4000
  export RESEARCH_EFFORT=low RESEARCH_EFFORT_QUALIFY=medium
  export RESEARCH_LLM_TIMEOUT_SECONDS=90 LEAD_MAX_SEARCH_REQUESTS=10
  export RESEARCH_BUDGET_JSON='{"queries":10,"pages":60,"companies":5,"llm_calls":16,"iterations":3,"input_tokens":300000,"output_tokens":40000,"browser_calls":2,"seconds":900,"cost_microusd":2000000,"daily_cost_microusd":2000000}'
  read -r -s 'ANTHROPIC_API_KEY?Anthropic API key: '
  print
  export ANTHROPIC_API_KEY
  /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.client_zero --research-dry-run
  run_status=$?
  unset ANTHROPIC_API_KEY
  exit "$run_status"
)
```

Этот следующий запуск читает страницы для рынка и кандидатов по обычному research pipeline. Ограничение текущего шага «не открывать сайты кандидатов» соблюдено; сам следующий запуск требует самостоятельного действия владельца.


## 8. Полный набор тестов

Итог: **469 passed, 1 skipped, 5 warnings in 8.56s** (исходный ориентир владельца: 464 passed, 1 skipped). Прибавилось 5 тестов: 3 уже находились в незакоммиченном worktree, 2 добавлены в этом шаге. Единственный skip: sibling CRM checkout not explicitly selected. Пять предупреждений — deprecation Starlette/Alembic.

Postgres research tests включены; legacy qualification-table test перенаправлен на разрешённую disposable базу в памяти процесса, файл теста не ослаблялся. В первом общем прогоне legacy тест попытался подключиться к 5432 и получил отказ аутентификации; данные и настройки там не менялись. Финальная воспроизводимая команда не обращается к 5432:

```sh
EVOROVE_TEST_POSTGRES=1 /Users/alenakulish/dev/evorove_lead/.venv/bin/python - <<'PYTEST'
import sys, pytest
sys.path.insert(0, 'tests')
import test_postgres_qualifications
test_postgres_qualifications.LOCAL_URL = 'postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun'
raise SystemExit(pytest.main(['-q', '-rs']))
PYTEST
```

При поисковой ошибке новый people_search.hits=null, а не ложный ноль; успешная пустая выдача записывается hits=0.
