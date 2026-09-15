# Production-КАД: количество дел ответчика

Пользователь утвердил замену прежней оценки судебной истории:
`актуальный ИНН → поиск → сопоставление роли → число дел ответчика → краткий итог`.

Production не открывает карточки, решения или документы судебных дел.
HTTP-поиск либо имеющийся Playwright-контекст делает один поиск и получает
только необходимые страницы выдачи с паузой между ними. Нет повторного поиска
или смены транспорта после технической ошибки. Количество ответчиков определяется
по точному ИНН в обозначенной группе участников/столбце роли в строке выдачи.
Другой ответчик в том же деле не считается нашим. Истец, третье лицо и иная роль
не увеличивают счётчик. Одновременная роль ответчика и истца считается ответчиком.
Дубли строк устраняются только в памяти, идентификаторы не сохраняются.
Если роль/ИНН нельзя установить в выдаче, результат — непроверенность, без запроса к карточке.

Результат содержит ровно шесть полей, например (это пример формата, не LIVE):

```json
{
  "searched_inn": "1234567890",
  "checked_at": "2026-09-15T12:00:00+00:00",
  "technical_status": "KAD_CHECKED",
  "defendant_cases_count": 2,
  "kad_status": "YELLOW",
  "reason": "Есть судебные дела в качестве ответчика: 2. Требуется вручную проверить судебные иски и добросовестность поставщика."
}
```

- Подтверждённый 0: КАД сам по себе не создаёт предупреждения; общий antifraud
  продолжает применять остальные правила поставщика.
- Любое N > 0: YELLOW, ручная проверка исков/добросовестности. Автоматической
  оценки значимости/обоснованности исков нет; из КАД RED не создаётся.
- CAPTCHA/challenge/451/timeout/ошибка/неполнота/неопределённая роль:
  `KAD_REQUIRES_MANUAL_CHECK`, YELLOW, count=null и явная причина.
  Проверка действующего установленного продавца не становится полноценным GREEN,
  в том числе на доверенном домене. Другие подтверждённые RED-сигналы сохраняются.
- Неустановленный продавец: YELLOW, `KAD_CURRENT_SELLER_UNDETERMINED`, КАД не запускается.

Банкротство, свежесть, категории, решения, прежние пороги 3/2/730 и балльная
оценка КАД больше не используются. Число дел не становится оценкой качества исков.

## Хранение и совместимость

Production возвращает и сохраняет только эти шесть полей.
`data/cache/kad/<ИНН>/<время>.json` хранит их внутри result; служебные поля
schema_version=2/run_id используются только для кэша. Ни номеров, ни ссылок,
ни списков, ни полей отдельных дел в новом кэше нет.
`data/suppliers/_kad/<хэш-домена>/<время>.json` содержит непосредственно шесть полей.
Данные текущих реквизитов/юрлица остаются в обычной проверке поставщика отдельно.

Старый подробный кэш schema_version=1 не используется; старые рабочие файлы
не удалялись и не мигрировали. Подробный legacy-результат не подтверждает новую
проверку. Входящий kad_check очищается до нового формата перед antifraud/отчётом.
По умолчанию кэш переиспользуется только внутри текущего запуска. Срок между
запусками не назначен; явный cache_max_age_hours действует только для успешных
результатов и сохраняет исходную дату.

CLI сохранён:

```bash
.venv/bin/python -m suppliers.kad_client <актуальный_ИНН>
```

`suppliers/kad_diagnostic.py` остаётся подробным инструментом разработки, но
получил собственный диагностический check: он не вызывает production-assessor
и не использует production-кэш. Его подробный результат не является новым допуском.

Правила и применение синхронизированы в навыке supplier-check, версия 2026-09-15.3,
и AGENTS.md. SUP-012 сохранён как отключённое правило, SUP-013 — новый счётчик.
Остальные antifraud-правила поставщика и runtime без LLM сохранены. Потребитель
общего check_kad в security/customer_check.py адаптирован к шести полям: он
не читает удалённые признаки банкротства/свежести и не переносит списки дел.
Старые интерфейсные колонки общего количества/истцов/банкротства показывают
неизвестность (None), а не выдуманный нулевой результат.

LIVE после прежнего 451 не выполнялся. Реальная разметка роли/ИНН в выдаче КАД
остаётся неподтверждённой; при неподдерживаемой разметке production возвращает
ручную проверку. Профильные тесты — синтетические HTML и mock Playwright.

## Изменённые файлы и проверки

- Production: suppliers/arbitration.py, suppliers/kad_client.py,
  suppliers/verification.py, suppliers/live_verification.py, config/kad.json.
- Потребители: suppliers/market_search.py, suppliers/market_search_third_test.py,
  security/customer_check.py.
- Разработка: suppliers/kad_diagnostic.py, reports/kad_local_diagnostic.md,
  reports/kad_minimal_production.md.
- Тесты: tests/test_supplier_arbitration.py, tests/test_kad_integration.py,
  tests/test_kad_diagnostic.py, tests/test_supplier_search.py, tests/test_customer_check.py.
- Правила: AGENTS.md, skills/supplier-check/SKILL.md,
  skills/supplier-check/references/rules.json, skills/supplier-check/references/assessment.md.

Проверки без сети:

```bash
.venv/bin/python -m unittest tests.test_supplier_arbitration tests.test_kad_integration tests.test_kad_diagnostic
```

29 тестов OK. Дополнительно выбранные связанные проверки:

```bash
.venv/bin/python -m unittest tests.test_supplier_search.SupplierSearchTest.test_consistent_legal_entity_and_old_domain_can_pass_without_cms_ip tests.test_supplier_search.SupplierSearchTest.test_new_current_inn_registered_long_ago_can_pass
.venv/bin/python -m unittest tests.test_customer_check.CustomerCheckTests.test_04_shared_arbitration_shape tests.test_customer_check.CustomerCheckTests.test_09_bankruptcy tests.test_customer_check.CustomerCheckTests.test_20_customer_inn_is_passed_to_kad
```

2 + 3 теста OK. CLI kad_client/kad_diagnostic --help, quick_validate навыка,
JSON и git diff --check прошли. Полный regression suite, LIVE и запись UNIT
в Google Sheets не запускались.
