# Переключение контрольного поиска на Yandex Search API

`--product-price-control` получает ссылки через существующий Search API v2.
Карточки читает прежний HTTP/Playwright-сборщик. Цель — до шести предложений
с подтверждением товара и наличия, затем TOP-3. Сниппеты не подтверждают цены.
Массовый pipeline не переключён.

Лимит — 30 платных запросов на запуск; ошибки API не повторяются автоматически.
HTTP-код API сохраняется отдельно от CAPTCHA магазина, без тела ответа и ключей.
До Codex и браузера проверяется доступность credentials в macOS Keychain.

Проверка: 21/21 тест, команда:

```text
.venv/Scripts/python -X utf8 -m unittest tests.test_product_control_yandex tests.test_yandex_search.ProviderTests tests.test_product_price_control.ProductControlTests.test_search_reaches_six_and_selects_cheapest_three tests.test_product_price_control.ProductControlTests.test_unknown_availability_is_reserve_in_control -v
```

`git diff --check` — без ошибок whitespace.

Контроль CLI:

```text
.venv/Scripts/python -X utf8 main.py --product-price-control config/product_price_control.example.json
```

На текущей Windows-машине получен `SEARCH_API_NOT_CONFIGURED`,
`credentials_status=unavailable`, `api_calls=0`. Это проверка раннего завершения,
не успешный LIVE-поиск. Реальные цены и работоспособность credentials требуют
проверки на macOS по `docs/PRODUCT_PRICE_CONTROL_HANDOFF.md`.
