# Контрольный режим поиска, 16.09.2026

Статус: локальный прототип; массовое включение и приёмка пяти товаров не выполнены.

Исправлено полное обозначение трёх позиций Gonsin, запрет DIN/стандартов
как модели и повторного угадывания при выгрузке/синхронизации. Исходное имя
передаётся в существующую колонку названия позиции.

Новые модули: `ai/codex_intake.py`, `model_search/google_provider.py`,
`model_search/deferred_search.py`, `pipeline/product_price_control.py`.
Изменены: `model_search/customer_model.py`, `model_search/price_readiness.py`,
`model_search/playwright_provider.py`, `pipeline/mvp_exact_batch.py`, `main.py`,
`requirements.txt`, `AGENTS.md`, `README.md`, `docs/TENDER_AI_MASTER_SPEC.md`.
Регрессии: `tests/test_product_price_control.py`.

Codex CLI получает текст одной позиции и возвращает JSON с цитатами.
Кандидат модели — gpt-5.6-luna, запуск отдельный, текущая сессия не копируется.
Адаптер проверен с подставным процессом, реальная доступность модели и её
точность не подтверждены. Подбор кандидатов и чтение карточек пока используют
существующие детерминированные проверки; AI не выполняет полноценную
проверку произвольных характеристик карточек продавцов.

Контрольный режим ищет через Google, читает максимум 100 URL, ограничивает
время поиска 180 секундами (текущий сетевой запрос может завершиться позже),
собирает до шести подходящих предложений и выбирает три минимальных.
Неизвестное наличие/неподтверждённые требования сохраняются как резерв.
Временные сетевые ошибки повторяются один раз после других заданий.
CAPTCHA и блокировки не повторяются автоматически.

## Проверки

18 новых профильных проверок выполнялись по мере изменений; все прошли:

```powershell
.venv/Scripts/python -X utf8 -m unittest tests.test_product_price_control -v
```

Последние добавленные тесты также запускались отдельно:

```powershell
.venv/Scripts/python -X utf8 -m unittest tests.test_product_price_control.ProductControlTests.test_sync_does_not_restore_old_guessed_model -v
.venv/Scripts/python -X utf8 -m unittest tests.test_product_price_control.ProductControlTests.test_unknown_availability_is_reserve_in_control tests.test_product_price_control.ProductControlTests.test_search_reaches_six_and_selects_cheapest_three -v
```

Ещё пять существующих проверок — OK:

```powershell
.venv/Scripts/python -X utf8 -m unittest tests.test_requirements_first.RequirementsFirstTests.test_pump_from_green_field tests.test_requirements_first.RequirementsFirstTests.test_collector_from_label_in_green_field tests.test_requirements_first.RequirementsFirstTests.test_bb_battery_spaced_series_without_analog_selection tests.test_requirements_first.RequirementsFirstTests.test_requirements_collected_from_card_and_contract_before_branch tests.test_playwright_provider.PlaywrightProviderTests.test_product_page_price_wins_over_search_snippet -v
```

`main.py --help`, compileall изменённых модулей и `git diff --check` — OK.
Полный regression suite не запускался.
Создано Windows-окружение `.venv`, установлены requirements и Chromium.
Для ZoneInfo на Windows добавлена зависимость tzdata.

LIVE: первый запрос Google по Gonsin TL-VDC4200 остановлен защитой.
Артефакт: `data/product_price_control/google_probe.json`,
`status=requires_manual_check`, `error=PermissionError`. Цены не подтверждены.

Sheets: прочитаны I8:K8 и BW8. Попытка точечной коррекции отклонена Google
с 403 PERMISSION_DENIED; таблица не изменена. В BW8 обнаружена ошибочная
валидация «статус закупки» при заголовке «МОДЕЛЬ ЗАКАЗЧИКА»; её исправление
также не выполнено из-за отсутствия прав записи.

## Осталось

- Подтвердить доступность/качество Luna на реальных требованиях и сравнить
  сложные случаи с Terra; контроль расходов не является измерением лимита аккаунта.
- Восстановить обычный доступ Google вручную; прогнать пять товаров.
- Проверить AI-подбор и соответствие карточек на неизвестных категориях:
  текущая детерминированная проверка не гарантирует разбор произвольного ТЗ.
- Получить разрешённый доступ к редактированию Sheets и исправить строку 8.
- Только после приёмки подключить новый режим к массовому pipeline,
  выбранным поставщикам и экономике; до этого новый режим не пишет в Sheets.
