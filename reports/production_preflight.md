# Предпусковой аудит production pipeline TENDER_AI

Дата аудита: 09.09.2026. Аудит выполнен статически и на локальных тестах. Полный ЕАТ, live supplier search, Google Sheets и retention не запускались.

## Краткий вывод

Единого массового production pipeline сейчас нет. `main.py` предоставляет отдельные тестовые команды, а запуск без ключей только печатает «Система запущена успешно». `pipeline/orchestrator.py` соединяет часть этапов, но читает четыре фиксированных JSON одной контрольной закупки и не вызывается из `main.py`. Первый полный реальный запуск пока небезопасен.

## Реальный call graph

`python main.py` → печать двух строк → завершение.

Отдельные ключи `main.py` запускают изолированные тесты ЕАТ, фильтра, документов, Google Sheets, procurement audit или model search. Production-команды, которая выполняет EAT → filters → audit → model → suppliers → calculator → Sheets → report, нет.

`pipeline/third_control_test.py` → `pipeline.orchestrator.run_pipeline()` → чтение фиксированных `eat_single_...json`, `procurement_model_search_third_test.json`, `supplier_search_third_test.json`, `supplier_verification_deep_test.json` → сбор результата в памяти. Live-функции этапов при этом не вызываются.

## Матрица готовности

| Этап | Модуль | Существует | Подключён к production | Вход получает автоматически | Выход передаёт автоматически | Fail-safe | Готовность |
|---|---|---:|---:|---:|---:|---:|---|
| Точка входа | `main.py` | Да | Нет единого flow | Нет | Нет | Н/Д | 🔴 НЕ ПОДКЛЮЧЕНО |
| Получение ЕАТ | `eat/browser_session.py`, `stable_test.py`, `filter_pipeline.py` | Да | Только тестовые команды | Требует ручной браузерной сессии | Только JSON теста | Частичный | 🟡 ЧАСТИЧНО ГОТОВО |
| Retention | `google_sheets/retention.py` | Да | Только внутри `test_export()` | Только при тестовой записи | Да | Хороший dry-run | 🔴 НЕ ПОДКЛЮЧЕНО |
| Базовые фильтры | `filters/eat_filters.py` | Да | В `eat/filter_pipeline.py` | Да | Да | На закупку нет изоляции | 🟡 ЧАСТИЧНО ГОТОВО |
| Семантические фильтры v2 | `filters/semantic_bad_words.py` | Да | Нет, только пересчёт файла | Нет | Нет | Н/Д | 🔴 НЕ ПОДКЛЮЧЕНО |
| Закрытые закупки | `eat_filters.py`, `reports/confidential_links.py` | Да | Только тестовые файлы | Да в фильтре | Не доходит автоматически до Sheets | Частичный | 🟡 ЧАСТИЧНО ГОТОВО |
| Карточка одной закупки | `eat/single_purchase.py` | Да | Только ручной тест | Ручной вход | JSON + документы | Ошибка файла может остановить карточку | 🟡 ЧАСТИЧНО ГОТОВО |
| Customer check | `security/customer_check.py` | Да | Только fixture-orchestrator | Карточка/текст сохранённых документов | Да внутри orchestrator | Да, КАД не блокирует | 🟡 ЧАСТИЧНО ГОТОВО |
| Скачивание документов | `eat/contract_analysis.py`, `single_purchase.py` | Да | Только тестовые сценарии | Ручная сессия | Локальные файлы | На файл частичный | 🟡 ЧАСТИЧНО ГОТОВО |
| Text/OCR | `documents/text_extraction.py` | Да | Через `procurement_audit.extract_document_with_evidence`, но не массовый flow | Локальный PDF | Текст + evidence | Постраничный fail-safe отсутствует | 🟡 ЧАСТИЧНО ГОТОВО |
| Procurement audit | `documents/procurement_audit.py` | Да | Только одна жёстко заданная закупка | Фиксированные JSON/файлы | JSON теста | Общий try/except отсутствует | 🔴 НЕ ПОДКЛЮЧЕНО |
| Traceability | `security/traceability.py` | Да | Вызывается fixture-orchestrator один раз на весь lot | Сохранённый lot | Да | `_safe` в orchestrator | 🟡 ЧАСТИЧНО ГОТОВО |
| Model search | `model_search/*` | Да | Только тестовые сценарии, кандидаты заданы кодом | Fixture audit | Fixture JSON | Нет production web adapter | 🔴 НЕ ПОДКЛЮЧЕНО |
| Многопозиционность | `pipeline/orchestrator.py` | Частично | Нет | Берёт только `items[0]` и `positions[0]` | Только одна позиция | Нет | 🔴 НЕ ПОДКЛЮЧЕНО |
| Supplier market search | `suppliers/market_search.py` | Нормализация/ранжирование | Live discovery отсутствует; данные заданы в test script | Fixture offers | JSON теста | Н/Д | 🔴 НЕ ПОДКЛЮЧЕНО |
| Supplier verification | `suppliers/verification.py`, `external_checks.py` | Да | Fixture merge; неизвестные live-сайты автоматически не проходят | Сохранённые evidence | Да | Источники падают безопасно по отдельности | 🟡 ЧАСТИЧНО ГОТОВО |
| Supplier arbitration | `suppliers/arbitration.py` | Да | Общий интерфейс подключён к verification | Нужен подтверждённый ИНН и адаптер | Manual status без адаптера | Да | 🟡 ЧАСТИЧНО ГОТОВО |
| Supplier ranking | `suppliers/market_search.py`, `ranking.py` | Да | Только на fixture offers | Да в тестовом runner | Call lists | Да | 🟡 ЧАСТИЧНО ГОТОВО |
| Calculator | `calculator/*` | Да | Fixture-orchestrator | Частичные данные | Предварительный статус | Да для неизвестной цены/расходов | 🟡 ЧАСТИЧНО ГОТОВО |
| Final decision | `calculator/decision.py`, orchestrator | Да | Только fixture-orchestrator | Частичные данные | Русский статус | Да | 🟡 ЧАСТИЧНО ГОТОВО |
| Google Sheets | `google_sheets/*` | Да | `sync()` намеренно отключён | Только старые тестовые JSON | Test upsert | Сохранение ручных полей протестировано | 🔴 НЕ ПОДКЛЮЧЕНО |
| Report | только тестовая `_report_row()` | Частично | Только `test_export()` | Старые тестовые файлы | Одна строка | Нет production aggregation | 🔴 НЕ ПОДКЛЮЧЕНО |
| Checkpoint/resume | отсутствует | Нет | Нет | Нет | Нет | Нет | 🔴 НЕ ПОДКЛЮЧЕНО |
| Production logging | частично в EAT | Частично | Нет сквозного logger | Нет procurement/item context на всех этапах | Нет | Нет | 🔴 НЕ ПОДКЛЮЧЕНО |

## ЕАТ и пагинация

- Найденный endpoint: `tender-cache-api.agregatoreat.ru/api/TradeLot/list-published-trade-lots`, но production-клиент не закреплён: URL и тело запроса перехватываются из ручной Playwright-сессии.
- Используются обнаруженные поля `page` и `size`, не `skip/take`. Нумерация доказывается пробами 0/1/2.
- `totalCount` извлекается в `stable_test.py`, но цикл ограничивается количеством нужных записей/40 страниц; `filter_pipeline.py` имеет жёсткий предел 80 страниц.
- Есть последовательная загрузка с паузой 0,8 секунды, дедупликация ID/tradeNumber, raw сохраняется.
- Нет retry/backoff для 403/429/5xx, нет checkpoint, нет устойчивого production timeout на API fetch, ошибка страницы способна завершить весь сценарий.
- Живая offset-пагинация при сортировке только по `publishDate` может давать сдвиги/единичные дубли.

## Фактические фильтры

- Цена: 150 000–400 000 ₽.
- 44-ФЗ подтверждается текстом `purchaseTypeTitle`; доказанные типы 1 и 2 перечислены в config.
- Разрешены 53 региона из `config/eat_filters.json`.
- `max_items` фактически равен **10**, а ожидается 15.
- `линолеум` отсутствует в `hard_exclusions` — разрешён.
- `subsidy_substring` сейчас добавляет `contains_subsidy` в rejected — это противоречит актуальному правилу «только особое условие».
- Семантические hard/contextual exclusions v2 существуют, но `main.py --eat-filter-test` вызывает базовый `filter_purchase`, а не `_filter_v2`.
- Явного production-фильтра «не аукцион» и надёжного фильтра ГОЗ по `stateDefenseOrder` в основном call graph не найдено.
- Список hard exclusions в config соответствует текущим категориям; новые категории не добавлялись.

## Закрытые закупки

Двойной признак `isTradeInfoHiddenByPrivacyAgreement=true` + `hideDetailsForUnauthorized=true` корректно даёт `confidential_locked`. Ссылка и дедлайн формируются отдельным report script, но автоматической передачи из будущего batch в Google Sheets пока нет.

## Документы, OCR и audit

`text_extraction.py` корректно выбирает text layer или локальный Tesseract `rus+eng` на 300 DPI, поддерживает смешанные PDF, сохраняет confidence и сомнительные критические значения. Оригинал не изменяется. Однако `contract_analysis.py` всё ещё читает PDF напрямую через `pypdf`, минуя OCR; production downloader и OCR-аудит не соединены. Ошибка Tesseract на одной странице сейчас способна завершить чтение всего PDF.

`procurement_audit.py` извлекает требования, классифицирует документы и особые условия, но `run_test()` привязан к `TEST_TRADE_NUMBER` и фиксированным файлам. Полный набор специальных условий реализован в нескольких тестовых путях, а не в одной production-функции для произвольной закупки.

## Model/supplier search

`model_search/search.py` — воспроизводимый тест с кандидатами и доказательствами, жёстко заданными в Python. Реального поискового адаптера, принимающего произвольное ТЗ, нет. `pipeline/orchestrator.py` не вызывает model_search: он читает его готовый JSON.

`suppliers/market_search.py` реализует exact model, классификацию, дедупликацию, порог и ranking. Live-поиска marketplace/federal/professional/local нет; 13 предложений заданы в `market_search_third_test.py`. Актуальный supplier target здесь правильный: 15%, `customer_unit_price × 0.85`; цена выше порога не удаляется. Но старый fixture-orchestrator продолжает показывать 20% (`×0.8`) и использует старый supplier JSON.

Публичная цена и purchase price разделены корректно. Calculator не считает прибыль без подтверждённой purchase price. Формулы Python: комиссия 3% НМЦК; налоговая база = подача − закупка − операционные расходы; комиссия не уменьшает налоговую базу; налог 15% от положительной базы.

## Google Sheets и retention

- ID таблицы настроен через `.env`, Service Account key существует в `secrets/`; значения/ключ в отчёте не раскрываются.
- `secrets/`, `.env`, логи, credentials/token JSON исключены `.gitignore`.
- Upsert использует `ID закупки + № позиции`, сохраняет ручные колонки; текстовые форматы и dropdown протестированы.
- `sync()` намеренно выбрасывает ошибку. Production write отсутствует.
- Retention корректно планирует удаление всей закупки по ID, защищает lifecycle-статусы, оставляет нераспознанные даты и не читает Report. Но вызывается только `test_export()`, а не production run.

### Сверка колонок

**A. Уже существуют и подходят:** основные реквизиты закупки и позиции, deadline, ссылка, НМЦК, заказчик/ИНН, особые условия, прослеживаемость, дополнительные расходы, КП1–КП4, статус закупки.

**B. Колонки задуманы, но mapping отсутствует:** 11 `PROCUREMENT_AUDIT_HEADERS` возвращаются только `active_headers_with_procurement_audit()` и не используются рабочим `ACTIVE_HEADERS/build()`.

**C. Данные есть, колонок нет:** ПРОВЕРКА ЗАКАЗЧИКА; АРБИТРАЖ ЗАКАЗЧИКА; РИСК ОПЛАТЫ; числа дел по ролям/банкротству; выбранная модель и её production/RF/technical status; лучший supplier; verification status; число найденных/проверенных; suppliers for call; target 15%; checked_at рынка; pipeline warnings.

**D. Устарело/требует пересмотра:** фиксированные КП1–КП4 как единственный supplier interface и старое поле «ЦЕНОВОЙ ПОРОГ ДЛЯ ПОИСКА» без явного разделения model-search ориентира и supplier target 15%. Удалять сейчас ничего не следует.

`📊 Отчёт` не готов к production-метрикам: сейчас собирается преимущественно из старых файлов 200 закупок и не содержит customer/model/supplier/error counters из задания.

## Масштабирование, rate limits и cache

Общего rate limiter, retries/backoff, freshness cache и защиты повторного поиска модели/домена/ИНН нет. В тесте ЕАТ только последовательность и пауза 0,8 с. Supplier external checks имеют timeout 12–15 с, но без повторов и общей квоты.

Без production-реализации точное число запросов неизвестно. Без cache приблизительная верхняя нагрузка для N прошедших закупок и средней 2,5 позиции:

| Закупок | Позиции/model searches | Карточки/наборы документов (оценка 3 документа) | Supplier searches (4 класса на позицию) | Verification calls при 10 доменах/позицию |
|---:|---:|---:|---:|---:|
| 10 | 25 | около 30 | около 100 поисковых серий | до 250 проверок доменов |
| 25 | 63 | около 75 | около 252 поисковых серий | до 630 проверок доменов |
| 50 | 125 | около 150 | около 500 поисковых серий | до 1 250 проверок доменов |

Это показывает, что главные bottleneck — web model discovery, supplier discovery, документы/OCR и повторная domain/legal verification.

Рекомендуемые безопасные пределы после реализации: EAT последовательно с паузой 0,8–1,5 с; document downloads максимум 2 одновременно на один домен; web discovery 1–2 одновременных запроса глобально; exponential backoff для 429/5xx (например 2/5/15 с, максимум 3 попытки); 403/CAPTCHA без повторного штурма; cache exact model 12–24 часа, публичная цена 6–12 часов, domain/WHOIS 30 дней, legal entity/ИНН 7 дней, KAD только подтверждённый результат с датой либо manual; ключ cache включает источник и дату.

## Обновление после устранения blockers №1–4

Этот раздел заменяет прежние выводы отчёта о blockers №1–4.

Создан безопасный production-oriented dry-run: `python main.py --production-dry-run`. Обычный `python main.py` по-прежнему только печатает сообщение и не выполняет live-действий. Dry-run вызывает `pipeline/batch_orchestrator.py`, обрабатывает локальные fixtures последовательно, применяет публичную точку `filter_purchase_v2`, запускает customer check один раз на закупку, читает доступные локальные документы с OCR fallback, обрабатывает все `lotItems[]`, вызывает traceability для каждой позиции и формирует отдельные item results.

Готовность реально исправленных частей:

| Исправленный этап | Готовность | Доказательство |
|---|---|---|
| Безопасная dry-run точка входа и batch | ✅ ГОТОВО | `main.py --production-dry-run`, 3 локальные закупки |
| Checkpoint/resume | ✅ ГОТОВО | атомарный `data/checkpoints/production_dry_run.json`; completed закупки и позиции пропускаются |
| Изоляция ошибок | ✅ ГОТОВО | тесты procurement/item/document; следующая сущность продолжает обработку |
| Актуальные фильтры dry-run | ✅ ГОТОВО | semantic v2, 44-ФЗ, цена, товары, auction/ГОЗ, max 15, subsidy warning, линолеум разрешён |
| Многопозиционность | ✅ ГОТОВО | обработаны закупки с 1, 2 и 4 позициями; всего 7 item results |

Supplier target в новом batch: `supplier_target_discount_percent = 15`, `supplier_target_price = customer_unit_price × 0.85`. `public_price` не преобразуется в `purchase_price`.

Model search и supplier market search не симулируются: при наличии используется сохранённый тестовый результат, иначе записывается соответственно `LIVE MODEL SEARCH ЕЩЁ НЕ ПОДКЛЮЧЁН` или `LIVE SUPPLIER MARKET SEARCH ЕЩЁ НЕ ПОДКЛЮЧЁН`.

Контрольный локальный запуск: 3 закупки, 7 позиций, 3 completed, 0 partial, 0 failed. Повторный запуск с `--resume` пропустил все 3 completed закупки; дубли не появились. Live ЕАТ, supplier web search, external supplier verification, Google Sheets и retention не запускались.

### Оставшиеся blockers — 9

1. Live EAT batch ещё не подключён к batch orchestrator.
2. Live document download → OCR → procurement audit ещё не является общей цепочкой для произвольной закупки.
3. Universal live model discovery отсутствует.
4. Live supplier market discovery отсутствует.
5. Verification/KAD ещё не вызываются автоматически для каждого нового live-поставщика.
6. Google Sheets production sync отключён и mapping новых полей отсутствует.
7. Retention не подключён перед будущим production upsert.
8. Production report не агрегирует новые customer/model/supplier/error показатели.
9. Для будущих live-этапов ещё нет общего rate limiter, freshness cache, retries/backoff и полного сквозного logging.

## Обновление: documents → extraction/OCR → procurement audit закрыт

В локальном production dry-run теперь реально работает единый интерфейс `documents.pipeline.process_procurement_documents()` → `audit_from_extraction()` → structured requirements всех позиций. Saved audit JSON больше не подменяет extraction при наличии исходного локального документа.

- 3 закупки, 7 позиций.
- 4 локальных документа обработаны; 2 PDF потребовали OCR; mixed-документов в этой выборке нет.
- Все 7 позиций получили structured requirements и source/evidence.
- Документный cache использует SHA-256 + версию extraction/OCR + языки.
- Ошибка OCR одной страницы даёт partial document и не останавливает следующие страницы, документы или закупки.
- Checkpoint хранит `documents`, `text_extraction`, `ocr`, `customer_check`, `procurement_audit`, `items_extracted`, `traceability`, а также промежуточные результаты/cache references.
- Resume пропустил 3 завершённые закупки; повторный OCR не запускался.

Готовность локальной цепочки документов/OCR/audit: **✅ ГОТОВО**. Live document source остаётся частью blocker live EAT adapter, но audit/extraction interface переписывать для него не потребуется.

Осталось **8 blockers**:

1. Live EAT batch/document source.
2. Universal live model discovery.
3. Live supplier market discovery.
4. Автоматическая verification/KAD новых live-поставщиков.
5. Google Sheets production mapping/sync.
6. Retention перед production upsert.
7. Production report с полными метриками.
8. Общий rate limiting/freshness cache/retries и полный live logging.

## Обновление: universal live model discovery — частично реализован

Добавлен универсальный интерфейс `model_search.live_discovery.discover_models()` и явный безопасный флаг
`--production-dry-run --model-live-test`. Он принимает автоматически извлечённые requirements, строит до четырёх
запросов, разделяет candidate discovery и строгую проверку всех требований, не смешивает похожие SKU, объединяет
источники точной модели, отдельно хранит российскую доступность, публичную цену и `purchase_price = null`.

Реализованы последовательные запросы, timeout, ограниченные retries с exponential backoff, обработка 403/429/5xx,
изоляция ошибок сайтов/позиций, безопасные логи и SHA-256 cache. Техническая свежесть составляет 30 дней, рыночная —
24 часа; partial/error результат свежим cache-hit не считается. Checkpoint получил стадию `model_discovery`, resume
не повторяет завершённую закупку.

Контрольный live-запуск был ограничен двумя локальными закупками (3 позиции, 9 запросов). Бесплатная RSS-выдача
оказалась недостаточной: по кондиционеру кандидаты не найдены, Royal Clima RC-TWN28HN автоматически не обнаружен;
по телевизору два найденных токена были нерелевантны и не прошли compliance. Поэтому сохранён честный partial-result,
`selected_model` отсутствует, старые fixture-ответы не использовались как доказательство.

Blocker **Universal live model discovery остаётся открытым**: интерфейс и safeguards готовы, но цепочка
`discovery → evidence → 13/13 requirements → selected_model` в реальном live-тесте пока не доказана. Осталось
**8 blockers**; следующий — улучшить/подключить надёжный поисковый источник для model discovery (ключевой API либо
иной разрешённый структурированный web search), после чего повторить тот же ограниченный тест.

## Checkpoint, logging и отказоустойчивость

Checkpoint/resume отсутствует. JSON тестов не является журналом стадий batch. При падении на закупке №37 production batch (которого пока нет) не сможет надёжно продолжить с №38.

`orchestrator._safe` ловит ошибки отдельных стадий одной fixture-закупки. Нет внешней изоляции `try/except` на закупку, позицию, документ и сайт в едином batch. Следовательно, одна ошибка сейчас может остановить соответствующий тестовый сценарий; гарантий продолжения массового run нет.

Логирование есть только у public EAT check и browser test. Нет единого структурированного лога `procurement_id/item_id/stage/status/error`, редактирования секретов на всех этапах и итогового error report.

## Security

Секрет Google хранится отдельно, `.env` и `secrets/` игнорируются. Диагностика браузера удаляет значения Authorization/Cookie/token. Автоплатежей, покупок, сообщений поставщикам, CAPTCHA/SMS bypass нет. Риск остаётся в произвольных supplier URL: production downloader должен разрешать только `http/https`, блокировать локальные/private адреса и ограничивать размер/redirects до появления массового web flow.

## BLOCKERS ПЕРЕД ПЕРВЫМ ПОЛНЫМ ЗАПУСКОМ

1. **Нет production entry point и batch orchestrator.** Изменить `main.py` и добавить минимальный production runner вокруг существующих модулей. Риск изменения высокий; сначала нужен dry-run на сохранённых fixtures.
2. **Нет checkpoint/resume и изоляции ошибок по закупке/позиции.** Добавить persistent state с procurement ID, item ID, stage, status и error; атомарную запись. Риск средний.
3. **Боевые фильтры не соответствуют актуальным правилам и semantic v2 не подключён.** Подключить публичную функцию v2, установить максимум 15, убрать subsidy из reject, добавить доказанные auction/ГОЗ checks. Риск высокий, нужны regression fixtures.
4. **Многопозиционные закупки не поддержаны orchestrator.** Перевести обработку с `[0]` на цикл всех позиций и агрегировать итог. Риск высокий.
5. **Document download → OCR → procurement audit не является общей production-цепочкой.** Выделить функции для произвольной закупки и локализовать ошибки по документу/странице. Риск средний.
6. **Model search не реализован для произвольного ТЗ и существует как fixture.** Нужен реальный ограниченный discovery adapter с evidence, rate limits и cache. Риск высокий.
7. **Supplier market discovery отсутствует; есть только обработка статического списка.** Нужен live adapter для четырёх классов источников с exact-model checks, freshness и safe URL. Риск высокий.
8. **Supplier verification/KAD не вызываются автоматически для каждого нового найденного домена/текущего ИНН.** Связать discovery → verification и добавить cache. Риск средний. Недоступность КАД сама по себе blocker не является.
9. **Google Sheets production sync отключён и новые поля не mapped.** Реализовать dry-run payload, mapping русских колонок, затем upsert после явного подтверждения. Риск высокий из-за внешней записи.
10. **Retention не подключён перед production upsert.** Включать только после preview/подтверждённого production sync. Риск средний.
11. **Нет production report aggregation.** Собирать реальные счётчики текущего run, не старые fixture totals. Риск низкий/средний.
12. **Нет общих rate limits/cache/retries.** Без них массовый model/supplier flow создаст чрезмерную нагрузку и повторные проверки. Риск средний.
13. **Нет сквозного production logging и полной fail-safe границы.** Добавить безопасный структурированный журнал и redaction. Риск средний.

## Первый минимальный следующий шаг

Сначала реализовать **локальный production runner в режиме `--production-dry-run`**, который работает на сохранённом небольшом fixture-наборе, имеет checkpoint/resume и изоляцию ошибок по закупке/позиции, но не делает live web/Sheets mutations. Только после его прохождения подключать реальный EAT source, затем по одному — документы/OCR, model discovery, supplier discovery и Sheets preview.

После устранения всех 13 blockers и повторного preflight можно выполнять первый ограниченный реальный запуск ЕАТ с малым лимитом и без автоматической записи в Sheets. Полный массовый запуск всего ЕАТ до этого не рекомендуется.
