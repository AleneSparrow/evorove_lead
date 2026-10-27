# Контролируемый Client 0 и последующий deployment

Рабочая директория: `/Users/alenakulish/dev/evorove_lead`.
Production и рассылки этим документом не разрешаются. Полный
[аудит](production-readiness-2026-10-10-ru.md) содержит результаты и ограничения.

## Локальная подготовка без платных вызовов

Команды не читают `.env`; `--env-file /dev/null` отключает автоматическую загрузку
файла Compose. Новый Compose project изолирован от работающего `system` sandbox.

```sh
docker build -f deploy/Dockerfile -t evorove-lead-intelligence:pilot .
docker compose --env-file /dev/null -f deploy/compose.pilot.yml config --quiet
docker compose --env-file /dev/null -f deploy/compose.pilot.yml up -d postgres
docker compose --env-file /dev/null -f deploy/compose.pilot.yml run --rm pilot alembic upgrade head
docker compose --env-file /dev/null -f deploy/compose.pilot.yml run --rm pilot python -m evorove_lead.client_zero --preflight --probe-search
```

Preflight exit 2 при paid gate=0 ожидаем. `results_received` подтверждает только
retrieval, не пригодность найденных компаний. Local SearxNG должен уже слушать
8080. В этой конфигурации PostgreSQL не имеет публичного порта; app не получает
CRM URL, internal secret или mailbox credentials.

## Один live run — только после разрешения владельца на расход до $1

Владелец предоставляет Anthropic credential через уже настроенное защищённое
окружение. Не вставлять ключ в командную строку, чат, `.env` или отчёт. Передача
`-e ANTHROPIC_API_KEY` ниже передаёт существующую переменную, а не её текст.
Если она не настроена, остановиться; не извлекать её из production в лог/файл.

```sh
docker compose --env-file /dev/null -f deploy/compose.pilot.yml run --rm \
  -e ANTHROPIC_API_KEY \
  -e RESEARCH_ALLOW_PAID_LLM=1 \
  -e RESEARCH_INPUT_USD_PER_MTOK=3 \
  -e RESEARCH_OUTPUT_USD_PER_MTOK=15 \
  pilot python -m evorove_lead.client_zero --research-dry-run
```

Сохранить `research_run_id` из JSON summary. Лимиты — максимум 20 компаний,
3 iterations, 10 queries, 60 pages, 24 LLM calls, 60k input/10k output, 900 s,
$1/run и $3/tenant/day. Они не гарантируют обработку всех 20 компаний.
Модель остаётся `claude-sonnet-4-5`; API unavailable/truncated не заменяется
фиктивными данными. Dry run всё равно расходует LLM-токены, но не создаёт и
не flush-ит CRM deliveries. Дополнительно здесь нет CRM-конфигурации.

Бизнес — реальный Evorove; tenant ID `evorove-client-zero-local-pilot` намеренно
локальный. Реальный сайт анализируется вместе с явно помеченным owner brief.
Никакой заранее сформированный список компаний в образ не включён.

## Экспорт и ручная оценка

```sh
mkdir -p pilot-reports
chmod 700 pilot-reports
docker compose --env-file /dev/null -f deploy/compose.pilot.yml run --rm \
  -v "$PWD/pilot-reports:/reports" \
  pilot python -m evorove_lead.pilot_report \
    --business-id evorove-client-zero-local-pilot \
    --run-id '<research_run_id из summary>' \
    --output /reports/client-zero-review.json
```

Файл создаётся с правами 0600 и не перезаписывается. Если он уже существует,
выбрать другое имя. В нём есть market/DNA, hypotheses attribution, поисковые
discovery URLs и причины ранних отказов, reflection, qualification, claims,
source timestamps/URLs, contact, opposing evidence и priority. Название компании
остаётся null, если в evidence нет подтверждённого name claim; домен сохраняется.
Это файл с контактными данными для владельца, не публичный артефакт.

Проверить каждую qualification вручную по исходным страницам. Отдельно отметить:
неподкреплённые claims, недоступные источники, неправильный субъект/US-географию,
отсутствие даты timing, generic inbox, противоречия, дубли. Не называть вакансии
или расширение сами по себе доказанным намерением купить Evorove.
Результат этого пилота — preview report; Cold в production должен остаться без
изменений. Сохранить null для precision/conversion без достаточной разметки.

## Локальная репетиция резервного копирования

Эти команды предназначены исключительно для созданного выше pilot project:

```sh
docker compose --env-file /dev/null -f deploy/compose.pilot.yml exec -T postgres pg_dump -U pilot -d pilot -Fc -f /tmp/pilot-before.dump
docker compose --env-file /dev/null -f deploy/compose.pilot.yml exec -T postgres createdb -U pilot pilot_restore_check
docker compose --env-file /dev/null -f deploy/compose.pilot.yml exec -T postgres pg_restore -U pilot -d pilot_restore_check --exit-on-error /tmp/pilot-before.dump
docker compose --env-file /dev/null -f deploy/compose.pilot.yml exec -T postgres psql -U pilot -d pilot_restore_check -Atc 'SELECT version_num FROM alembic_version'
```

Ожидается 0011. Если restore DB существует от прошлого теста, выбрать новое имя,
не удалять неизвестные БД. `/tmp` dump — временная репетиция, не durable backup.
Для сохранения данных production использовать проверенное защищённое backup
хранилище/volume backup и отдельный restore drill.

Завершить только этот sandbox, сохранив volume:

```sh
docker compose --env-file /dev/null -f deploy/compose.pilot.yml down
```

Не использовать `down -v`, если нужно сохранить отчёты/cache/историю пилота.

## Production: отдельное одобрение после live review

1. Владелец фиксирует одобренный commit в Lead и сама выполняет push. Учитывать,
   что GitHub auto-deploy может быть включён: публикация до выбора правильного
   config способна запустить старый Dockerfile с автоматическими миграциями.
   Сначала согласовать управление auto-deploy, не полагаться на порядок случайно.
2. По Railway reference подтвердить, какой PostgreSQL обслуживает Lead. Не
   выбирать по похожему имени. Проверить текущую revision 0008.
3. В Railway backup UI создать backup именно этого volume, сохранить backup ID,
   timestamp и retention. Восстановить в отдельную DB, проверить baseline counts,
   tenant FK, candidates и pending outbox. Затем применить 0009–0011 к копии и
   сравнить старые rows/counts. Эти production backup/clone действия ещё не выполнены.
4. После отдельного разрешения на migration+deployment выбрать для Lead config
   path `/deploy/railway.toml`, build root repository root, новый Dockerfile.
   Для этого одобренного release задать pre-deploy **`alembic upgrade 0011`**.
   Команда должна выполняться новым образом один раз, до traffic switch; не
   добавлять её обратно в server CMD. Существующие секретные references сохранить.
5. Для первого release установить `RESEARCH_ENABLED=0`,
   `RESEARCH_ALLOW_PAID_LLM=0`, `RESEARCH_CRM_DELIVERY=0`. Удалить только
   конфликтующий start override, если он есть; не перезаписывать другие settings.
   Согласовать существующие market-signal/n8n triggers и общий cost control.
6. Deployment после одобрения и настройки указанного config path:

   ```sh
   railway up --service evorove-lead --environment production
   ```

   Это production write. В ходе аудита команда не выполнялась. Предпочтительно
   deploy конкретного одобренного commit через имеющуюся GitHub-интеграцию;
   локальный `railway up` включает текущий working tree и требует его review.
7. Проверить healthy release, OpenAPI 200, internal без auth 401, DB revision0011,
   сохранность candidate/outbox counts, read-only health из Lead до CRM/search.
   Не вызывать `/searches`, `/run-due`, `/market-signals/run-due` или `/flush` как
   health checks: у этих маршрутов есть побочные эффекты/стоимость.
8. Production research включать только после отдельного решения о tenant,
   бюджете и schedule. Для первого запуска использовать CLI `--research-dry-run`,
   но помнить: он сохраняет research data в подключённую БД. Для запрета любых
   production writes использовать отдельную pilot DB, как в первом разделе.

[Railway config path](https://docs.railway.com/config-as-code/reference),
[pre-deploy semantics](https://docs.railway.com/deployments/pre-deploy-command),
[backup/restore guide](https://docs.railway.com/guides/postgres-backups-restores).

## CRM-проверка и rollback

Production CRM Cold автоматически создаёт `cold_assigned` для Sales. Даже
одна тестовая карточка может попасть в отправку. До отдельного согласования
использовать isolated CRM/Sales без mailbox; при необходимости tenant pause в
соседнем репозитории представить отдельный точный change request.

Запись в CRM выключена по умолчанию. Для репетиции включить
`RESEARCH_CRM_DELIVERY=1` и `CRM_CYCLE2_ISOLATED=1` только после проверки,
что CRM не доставляет команду в цикл 2. Для боевой CRM использовать
`RESEARCH_CRM_DELIVERY=1` и `CRM_LIVE_WRITE_ACK=1`: последний флаг явно
подтверждает, что запись Cold может вызвать автоматическое первое письмо
цикла 2. Не включать оба режима одновременно; это блокирует доставку.

После разрешённой доставки владелец открывает **Cold**, карточку и timeline:
имя/адрес, причина, source URL, segment, hypothesis/person/touch ID согласованы.
Через существующий authenticated board API проверить, что повтор того же
touch_id не создал вторую карточку. Не отправлять новый touch_id ради повторного
просмотра. Status endpoint Lead показывает pending; его flush — реальная доставка.

Rollback после одобрения: отключить research triggers и новые paid/delivery flags,
вернуть предыдущий release Lead `ef1e11d…` в Railway. Предпочтительно оставить
добавочные research-таблицы: rollback кода не требует удаления audit trail.
Предварительно проверить старый код на копии БД 0011. Не запускать `alembic
downgrade 0008` в production без отдельного решения — он удаляет новые данные.
Restore backup может потерять записи после backup timestamp и требует плана
сверки outbox с CRM. Уже отправленные письма восстановлением БД не отменяются.
