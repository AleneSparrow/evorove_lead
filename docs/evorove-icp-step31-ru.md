# Шаг 31. Очки вместо одновременных условий

Проверено 26 октября 2026. Плейбук `evorove_icp`, версия `2026-10-26.2`. Файл очков `src/evorove_lead/data/qualification_score.json`, версия `2026-10-26.1`. База CRM не записывалась. Одноразовая база читалась только на выборку. Платный вызов модели не запускался: `ANTHROPIC_API_KEY` в среде агента нет.

## Ворота

Жёсткий отказ только у явного дисквалификатора: конкурент, B2C, местная услуга частным лицам, регулируемая область, schema.org страна не US, явное не-US место вроде United Kingdom, цена from ниже $5000 в год, домен платформы или агрегатора. Нет признака США — это не отказ, у Fit меньше очков. Нет имени, пути контакта или события — тоже не отказ: падает Reach или Intent.

Fit 0–50: B2B-услуга или B2B-софт 18 (вместе не складываются), сильный признак США 14 или слабый 6 (вместе не складываются), размер 8, признак сделки от $5000 в год 10. Intent 0–30: витрина с book a call или request a quote 8, открытая роль продаж или маркетинга 6, рост команды 5, новый запуск 4, кейсы 4, свежая дата на странице 3. Reach 0–20: именной руководитель 10, адрес с MX 6, форма 4. A — это 70 и выше, в Cold. B — 50–69, очередь обогащения. C — ниже 50, хранится и считается, в Cold не идёт. 69 — это B, 70 — A, 49 — это C, 50 — B. Температура hot, warm, cold по-прежнему от даты события.

В payload уровня A пишутся fit, intent, reach, total, level, temperature и gaps. Очередь B читает уже связанные страницы about, team, contact, leadership, careers, затем JSON-LD, затем модель по сохранённому тексту, затем формы. Новых страниц на компанию не больше 3. Адрес из имени не становится путём письма: это подсказка, `verified` false, `mail_path` false, и только если у домена есть MX.

Модель для полей: `claude-haiku-5-5`, effort low, не больше 300 выходных токенов, один вызов на компанию, кэш по SHA-256 текста, потолок `QUALIFICATION_EXTRACT_MAX_USD`, по умолчанию $0.50 за запуск. Цитата остаётся только если она подстрока сохранённого текста и не длиннее 280 знаков. Неизвестная цена модели вызов блокирует. Замер на 50 компаний не запускался: ключа нет, а в одноразовой базе 7 доменов компаний, не 50.

## Разведка источников

39 внешних HTTP-запросов. Повтор заблокированного сайта не делался.

Принято одно новое хранилище. `https://www.sec.gov/files/company_tickers.json`: robots `www.sec.gov` путь `/files/` не запрещает. Файл скачан в проверке, 798155 байт, это меньше 50 МБ. В файле 8023 уникальных названия, поля CIK, тикер и title. Сайта и кода отрасли нет. По названию к портрету подошли 12 из 8023. В воронку A/B/C они не входят: страницы нет, обход сайтов не делался. Форма D в репозитории уже была, в этот проход заново не читалась.

Отклонено:

- Wikidata Query Service. `https://query.wikidata.org/robots.txt` содержит `Disallow: /sparql`. Один запрос к `/sparql` ушёл до того, как запрет был применён. Адаптера нет, повторного запроса нет. `www.wikidata.org` запрещает `/wiki/Special:`. Один запрос EntityData тоже ушёл до применения запрета и не повторялся.
- Дамп Wikidata `https://dumps.wikimedia.org/wikidatawiki/entities/20261005/wikidata-20261005-all.json.bz2`: 103337856107 байт. Это больше 50 МБ. Файл не скачивался.
- Colorado `data.colorado.gov` и `www.sos.state.co.us`: 403. Повтора нет.
- New York `data.ny.gov`: 403.
- Florida `dos.fl.gov`: страница проверки. `dos.sunbiz.org/robots.txt`: `User-agent: *` и `Disallow: /`.
- Texas `data.texas.gov`: 403. У `www.sos.texas.gov/robots.txt` пустое тело, готового массового файла нет.
- California каталог Socrata: 403.
- Washington `data.wa.gov`: 403. У `www.sos.wa.gov` robots запрещает `/search/`, массового файла в этом проходе нет.
- Massachusetts `corp.sec.state.ma.us/robots.txt`: 404 и страница проверки. Повтора нет.
- SAM.gov Entity API без ключа: `https://api.sam.gov/entity-information/v3/entities` и тот же путь с `ueiSAM` ответили 404, тела с компанией нет. Ключ не читался. API не считается рабочим.

## Проверка сохранённых компаний

В одноразовой базе `evorove_s12_dryrun` 7 доменов компаний, все у `client-zero-s18`. Это меньше 100, выдуманых строк нет, посчитаны все 7. Новых обходов сайтов нет. Запрос проверки к сети: 1, это файл тикеров SEC на 798155 байт.

Воронка: A 0, B 0, C 5, дисквалификация platform 2. Очередь B пустая, потому что уровня B нет. В Cold никто не ставился, CRM не писалась.

Семи кандидатов, не десяти. Ни одного в рассылку.

1. adrs.systems — ADRs for AI. Чтец имени выдал «Ten Real», роль founder, из фразы «Ten minutes, from zero Real commands, the flow the founder actually runs». Это не человек. Почты и формы нет, цитаты о покупателе нет. Fit 0, intent 3, reach 10, total 13, уровень C, температура cold. В рассылку не кладу: имени нет, сделки нет.
2. bellweather.dev — Bell Weather, сравнение погоды. Слабый признак США, fit 6, intent 0, reach 0, total 6, уровень C, температура cold. В рассылку не кладу: это приложение про погоду, не B2B-покупатель.
3. aisenatus.com — AI Senate. Fit 0, intent 0, reach 0, total 0, уровень C, температура cold. Человека, страны, цитаты и пути контакта нет. В рассылку не кладу.
4. github.com — домен платформы. Отказ platform, очков нет. В рассылку не кладу.
5. huggingface.co — домен платформы. Отказ platform, очков нет. В рассылку не кладу.
6. moodwriter.app — Mood Writer. Сохранённой страницы нет, есть только сигнал. Fit 0, intent 0, reach 0, total 0, уровень C. В рассылку не кладу.
7. signpath.org — SignPath Foundation, подпись кода для open source. Fit 0, intent 0, reach 0, total 0, уровень C. В рассылку не кладу.

Ожидаемые лиды A в месяц с текущих источников: 0. Основание: из 7 сохранённых доменов уровень A у 0, на шаге 30 из 25 обойденных доменов в Cold было 0, а разрешённый массовый файл SEC не содержит сайт. Городской поиск не запускался и в эту оценку не подмешан.

## Поиск, который не запускался

Форма запроса: термин активного подсегмента и город. Активных терминов 10, городов 10, всего 100 строк. Подсегменты со статусом needs_research в список не входят. SearxNG: 30 запросов в сутки, курсор `next_index` продолжается на следующий день, в тот же день повтор не идёт. Официальный Google Programmable Search: 100 бесплатных запросов в сутки, этого хватает на весь список за один день. Массовый прогон не запускался.

Тесты: 511 passed, 2 skipped. Пропущены прежние два: репозиторий CRM не задан, и Postgres на 127.0.0.1:5432.

## Хеши

Файл очков: `0aadb4e37ce374c12a18288bb70c89aa36aefe51a08d2d0928c582944b41926d`.

Плейбук: `a2d1bbcbfbf021043ae47f5a30d9101fdf2cb99dbe49aa805e177af6bd593590`.

## Команды

Без ключа, только чтение сохранённых строк и разбор уже известного файла тикеров не повторяется:

```
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
env -u DATABASE_URL -u DATABASE_PUBLIC_URL -u SOURCE_DATABASE_URL -u ANTHROPIC_API_KEY \
  PYTHONPATH=src /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.qualification_check \
  --database-url 'postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun' \
  --skip-sec
```

Отдельная платная команда. Агент её не запускал. Ключ подставляет владелец в своём терминале. Модель `claude-haiku-5-5`, effort low, не больше 300 токенов, один вызов на компанию, кэш по хешу текста, потолок $0.50:

```
cd /Users/alenakulish/.codex/worktrees/cycle1-research-integration/evorove_lead
env -u DATABASE_URL -u DATABASE_PUBLIC_URL -u SOURCE_DATABASE_URL \
  QUALIFICATION_EXTRACT_MAX_USD=0.50 \
  PYTHONPATH=src /Users/alenakulish/dev/evorove_lead/.venv/bin/python -m evorove_lead.field_extraction \
  --database-url 'postgresql+psycopg://evorove_test@127.0.0.1:55439/evorove_s12_dryrun' \
  --limit 50
```

В этой базе страницы есть у 6 доменов. Лимит 50 их не умножает.
