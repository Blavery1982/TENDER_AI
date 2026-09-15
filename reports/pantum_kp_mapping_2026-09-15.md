# КП Pantum M6607NW — проверка mapping

Основная Google Sheets: старые колонки поставщиков переиспользованы в блоках КП1–КП3.
До и после: 80 колонок. Сохранены 139 строк, 9 452 ячейки со значениями и 1 668 формул.
Цены ниже получены новым LIVE-поиском Яндекс (8 запросов, 41 уникальная ссылка).
Финально повторно открыто 8 найденных карточек существующим inspector; нового поиска не было.
Пример подготовлен локально, результаты и UNIT в основную таблицу не записывались.

Закупка №200909955126100205; Pantum M6607NW; 3.0 шт.; НМЦК 87,390.00 ₽; комиссия 2,665.40 ₽.

Ниже показаны поля одной строки основной Sheets, развёрнутые по блокам КП.

| Поле | КП1 | КП2 | КП3 |
|---|---|---|---|
| Поставщик | i-teh.com | micro-line.ru |  |
| Цена за единицу, ₽ | 24554.0 | 25635.0 |  |
| Рентабельность |  |  |  |
| Ссылка на товар | [Карточка товара](https://i-teh.com/catalog/lazernye_mfu/pantum_m6607nw_mfu_lazernoe) | [Карточка товара](https://micro-line.ru/mfu-lazernyy-pantum-m6607nw.html) |  |
| Ссылка на счёт |  |  |  |
| Проверка поставщика | 🟡 РУЧНАЯ ПРОВЕРКА — не найден ИНН на сайте | 🟡 РУЧНАЯ ПРОВЕРКА — не найден ИНН на сайте |  |

Рентабельность пуста: доставка и другие обязательные расходы не подтверждены. Формулы каждого КП используют свою цену, количество, комиссию и введённые расходы, действующие налоговую формулу и запас подачи 10%; резерв 18% сохраняется в экономическом предаудите.
Ссылки на счёт пусты: счета не получены и не созданы.
Подробные свидетельства antifraud и ошибки источников сохранены в соседних локальных JSON.

Запись выполняет `google_sheets/production_upsert.py:row_values()` и `upsert_live_payload()`.
Поля: `supplier_search.confirmed_offers[]` → `supplier_name`, `public_price`, `product_url`, `invoice_url`, `verification_status`; требуются `price_run_id`, `checked_at`, `exact_model_match`, `product_page_available`, `price_confirmed_on_product_page`.
Рентабельность пишет `google_sheets/workbook.py:quote_formulas()`; данные КП обновляются целиком.

Проверка: `.venv/bin/python -m unittest tests.test_kp_mapping tests.test_google_sheets_formulas` — 25 тестов, OK.
`git diff --check` и синтаксический разбор 12 изменённых Python-файлов — OK. Полный suite не запускался.
Новые формулы КП не проверялись в движке Google Sheets: примерные/UNIT-строки туда не записывались.

Предварительные КП; обязательные расходы не подтверждены; Найдено только 2 актуальных подтверждённых предложения
Nix исключён из финальных КП: наличие не подтверждено, в текущем пуле есть более трёх надёжных актуальных карточек. Более дорогие надёжные предложения не проходят существующий экономический допуск (скидка для порога больше 20%). КП3 оставлен пустым. Решение пересчитано по свежим LIVE-свидетельствам без повторного поиска и сетевых проверок.

## Изменённые файлы

- `google_sheets/production_upsert.py`
- `google_sheets/kp_schema.py`
- `google_sheets/workbook.py`
- `google_sheets/client.py`
- `suppliers/price_search_flow.py`
- `suppliers/exact_model_flow.py`
- `model_search/live_price_search.py`
- `model_search/playwright_provider.py`
- `model_search/product_evidence.py`
- `pipeline/mvp_exact_batch.py`
- `pipeline/live_e2e.py`
- `tests/test_kp_mapping.py`
- `README.md`
- `AGENTS.md`

Ранее существовавшие незакоммиченные изменения сохранены; коммит не создавался.

До исправления `row_values()` ожидал `public_price`, `product_url`, `supplier_name`, `verification_status`, `risk_flags` в `supplier_search.confirmed_offers`. В КП записывались только цена и товарная ссылка. Поставщик и antifraud писались в отдельные старые колонки, рентабельность КП отсутствовала. Поэлементное сохранение старых цены/ссылки при обновлении поставщика могло смешать предложения.
