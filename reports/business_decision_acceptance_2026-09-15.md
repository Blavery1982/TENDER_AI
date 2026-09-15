# Бизнес-решение EXACT_MODEL и Google Sheets

В рабочей Google-таблице, лист «🟢 Актуальные закупки», ячейка F141 закупки
200909955126100205, записано **🟡 РУЧНАЯ ПРОВЕРКА** с пятью причинами.
Чтение обратно подтвердило полное совпадение текста. Записана только ячейка итога;
колонки, КП, цены, формулы и другие листы не изменялись.

[Открыть решение в Google Sheets](https://docs.google.com/spreadsheets/d/1iMas8iLqWuAECZQ7mQOM-ZDJFWzO0kulevFlEwX9w2w#gid=955424503&range=F141).

## Правила

Подходящий вариант: точная модель подтверждена, актуальная цена подтверждена,
сохранённый экономический показатель не ниже production-порога, товар не
out_of_stock, antifraud не red. Порог читается из
`config/eat_filters.json → calculator.economic_precheck_margin_percent`.
Считаются уникальные домены, включая нормализацию www и IDNA.
Лучший — первый подходящий вариант исходного ранжирования.

- 🟢 ПОДАВАТЬСЯ: минимум три подходящих домена, лучший in_stock и green,
  отсутствуют существующие обязательные предупреждения.
- 🟡 РУЧНАЯ ПРОВЕРКА: один или два подходящих домена, лучший order/unknown,
  yellow/непроверенный antifraud либо существующие предупреждения.
- 🔴 НЕ ПОДАВАТЬСЯ: полный просчёт не содержит подходящих вариантов;
  подтверждённых цен нет, показатель ниже порога, точная модель не подтверждена,
  подходящие товары отсутствуют либо поставщики получили красный статус.

Неполный экономический просчёт без подтверждённо подходящих вариантов не
доказывает убыточность. Для него временно принят безопасный статус ручной
проверки, отмеченный в JSON как `CORE_RULE_CANDIDATE`. Эта оговорка требует
отдельного принятия владельцем и не добавлялась в approved_rules.json.

## Показатель и текущий LIVE-кейс

Допущение: использовать сохранённый предварительный запас из просчёта,
с явной подписью «Предварительный запас до остальных расходов».
Итоговая рентабельность КП — другой показатель; её формула не изменялась.

Текущие КП в таблице: i-teh.com — 24 554 ₽ и micro-line.ru — 25 635 ₽.
Это более поздний результат, чем проверочный кейс с 21 087 ₽.
Сохранённый предварительный запас первой корзины — 13,06%; показатель второй
не сохранён. Расходы на доставку, разгрузку и страхование не рассчитаны.
Поэтому текущая строка требует завершения просчёта и ручной проверки.
Подтверждённых поставщиков — 2; подтверждённо подходящих — 0;
`economics_complete=false`. Исторический вариант не переносился в текущие КП.

Артефакты: `business_decision_pantum_2026-09-15.json` и одноимённый Markdown.
JSON содержит источники сохранённых данных и подтверждение Google Sheets.
Новые web-запросы к продавцам, ЕАТ и Яндексу не выполнялись.

## Проверочный Pantum M6607NW

UNIT-кейс использует явно заданные владельцем значения:
21 087 ₽ / 25,33% / order / yellow; 23 690 ₽ / 16,12%; 24 554 ₽ / 13,06%.
Он подтверждает **🟡 РУЧНАЯ ПРОВЕРКА**, только одного подходящего поставщика
и сохранение рисков наличия и antifraud. Эти тестовые значения не записывались
в рабочие КП или журнал закупок.

Пример структурированного решения этого UNIT-кейса (сокращён):

```json
{
  "business_decision": {
    "status": "manual_review",
    "label": "🟡 РУЧНАЯ ПРОВЕРКА",
    "decision_version": 1,
    "profitability_threshold": 18.0,
    "eligible_supplier_count": 1,
    "confirmed_supplier_count": 3,
    "recommended_supplier": {
      "domain": "first.ru",
      "source_url": "https://first.ru/product/model",
      "confirmed_price": 21087.0,
      "profitability": 25.33,
      "availability_normalized": "order",
      "antifraud_status": "yellow"
    }
  }
}
```

Пример человеческого решения этого UNIT-кейса:

🟡 РУЧНАЯ ПРОВЕРКА

- Лучшая подходящая цена 21 087 ₽; показатель 25,33% при пороге 18%.
- Наличие: под заказ.
- Проверка поставщика: жёлтый статус, требуется ручная проверка.
- Только один уникальный поставщик подходит; нужно минимум три.
- Красных статусов среди подходящих вариантов нет.

## Изменённые файлы

- `calculator/business_decision.py` — engine и представления решения.
- `calculator/result_decision.py` — адаптация сохранённого просчёта.
- `pipeline/single_purchase_test.py` — JSON, Markdown и этап журнала.
- `pipeline/live_e2e.py` — сохранение ранжированного пула и решения в payload.
- `pipeline/mvp_exact_batch.py` — сохранение решения в batch JSON, отчёт и payload.
- `google_sheets/production_upsert.py` — вывод решения и безопасная запись одной ячейки.
- `google_sheets/client.py` — сохранение готового итога обычным сбором.
- `reports/price_search_report.py` — блок решения в начале отчёта из JSON.
- `tests/test_business_decision.py` — профильные UNIT-проверки.
- `README.md`, `AGENTS.md` — описание интеграции и постоянная договорённость вывода.
- `reports/business_decision_pantum_2026-09-15.json`, одноимённый `.md`, этот отчёт.

Поиск цен, Yandex Search API, HTTP → Playwright, ranking, EXACT_MODEL,
antifraud и формулы экономики в рамках этой задачи не изменялись.
Изменения в этих подсистемах, уже присутствовавшие в рабочем дереве до задачи,
сохранены.

## Проверки

Запуск профильных тестов — 28 проверок, OK:

```bash
.venv/bin/python -m unittest tests.test_business_decision tests.test_kp_mapping.KPMappingTests.test_sort_and_price_url_supplier_pair_are_preserved tests.test_kp_mapping.KPMappingTests.test_each_check_follows_its_supplier tests.test_kp_mapping.KPMappingTests.test_repeat_upsert_updates_complete_blocks_and_preserves_manual_expenses tests.test_kp_mapping.KPMappingTests.test_schema_reuses_legacy_columns_and_is_idempotent tests.test_kp_mapping.KPMappingTests.test_general_collection_upsert_preserves_existing_kp_block
```

Проверены требуемые десять сценариев, сохранение входного ранжирования,
конфигурируемый порог, неизвестные данные, предупреждения, Markdown из JSON,
запись контрольного журнала, batch mapping, повторный upsert и отказ от
публикации при изменённых КП. Полный regression suite не запускался.

LIVE-проверка: сверка существующих КП, запись F141 RAW через настроенный
Service Account, чтение обратно. Первый сетевой вызов в sandbox завершился
TransportError; разрешённый сетевой вызов выполнился успешно.
