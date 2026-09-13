# Подготовка Yandex Search API — 2026-09-11

Платных запросов не выполнялось. Ядро MODEL_SEARCH не изменено.

## Сохранённое состояние

- `security/yandex_credentials.py`: отдельная запись macOS Keychain, service `TENDER_AI_YANDEX_SEARCH`, account `search_api`; команды setup/status/delete.
- `model_search/yandex_provider.py`: синхронный адаптер SearchProvider (search/fetch); платный доступ выключен по умолчанию, максимум три вызова, без повторов и redirect.
- Добавлены `model_search/yandex_control.py` и `tests/test_yandex_search.py`.

## API и стоимость

Официальное название: Yandex Search API, Web Search API v2, REST WebSearch.Search.
Endpoint: `POST https://searchapi.api.cloud.yandex.net/v2/web/search`.
Авторизация: `Authorization: Api-Key <секретный ключ сервисного аккаунта>`.
Параметры: SEARCH_TYPE_RU, region=225 (Россия), LOCALIZATION_RU (язык уведомлений),
FORMAT_XML, page=0, отключённое исправление опечаток, до шести результатов на запрос.
Ответ: JSON с rawData (Base64 XML); title/passages нужны только для discovery.
Регион влияет на ранжирование; российская доступность модели не доказывается настройкой поиска.

Синхронные квоты по документации: 10 запросов/секунду, 10 000/час.
Отложенные: 10/секунду, 35 000/час. Лимит текста: 400 символов, 40 слов.

Для первого контроля выбран synchronous: два основных запроса, третий резервный,
не более восьми кандидатов. Никаких генеративных ответов или smart snippets.
Тариф днём: 488 ₽/1000 запросов с НДС. Два вызова — 0,976 ₽;
три — 1,464 ₽ (ориентир максимум 1,47 ₽). Ночью — 366 ₽/1000.
Оценка относится к рублёвому тарифу на дату проверки, без индивидуальных скидок.

Для будущей массовой обработки предпочтителен deferred: 30,5 ₽/1000 днём,
25,41 ₽/1000 ночью. Дневной тариф в 16 раз ниже синхронного.
Минимальная обработка — 5 минут, хранение результатов — 12 часов.
Он совместим с SearchProvider через адаптер submit/poll; эффективная массовая
обработка потребует очереди операций и возобновления по operation ID без хранения
секретов в checkpoint. Сейчас deferred не реализован: это следующий отдельный этап,
не повод менять ядро перед первым контрольным тестом.

## Настройка пользователем

1. Войти в Yandex AI Studio, выбрать облако и каталог; привязать активный платёжный аккаунт.
2. Нажать «Создать API-ключ», выбрать срок действия. По quickstart вместе с ключом
   создаётся сервисный аккаунт с правами Search API.
3. Для вручную созданного сервисного аккаунта: роль `search-api.webSearch.user`
   на нужный каталог; область действия ключа `yc.search-api.execute`.
4. Скопировать folder ID нужного каталога и секретное значение ключа (не ID ключа).
5. В локальном Terminal выполнить:

```bash
cd /Users/lubov/Documents/TENDER_AI
.venv/bin/python -m security.yandex_credentials setup
```

Folder ID вводится обычным вводом, ключ — скрытым getpass. Данные сохраняются
только в отдельной записи Keychain. Не вставлять секрет в чат, командную строку или файл.

Проверка наличия записи (без проверки ключа запросом к API):

```bash
.venv/bin/python -m security.yandex_credentials status
```

Удаление:

```bash
.venv/bin/python -m security.yandex_credentials delete
```

## Контрольный тест

Локальный preview без Keychain и сети:

```bash
.venv/bin/python -m model_search.yandex_control
```

Платный запуск возможен только после отдельного разрешения пользователя.
Не выполнялся. Опция `--allow-paid-live-test` явно включает его.

Используется только закупка 100205573126100053, одна позиция и 13 сохранённых
требований. Предустановленная модель запрещена. Штатный planner решает, нужен ли
третий запрос. Сниппеты не передаются в compliance как доказательства.
Существующий загрузчик читает до 12 найденных URL (это отдельные обращения к сайтам,
не дополнительные поисковые вызовы Yandex API), без универсального краулинга.
Ключ Yandex не передаётся сайтам товаров. Приоритет официального источника
используется только при подтверждённой классификации; неизвестные сайты не объявляются официальными.

Будущие результаты: `data/yandex_model_search_control/<UTC timestamp>/report.json`.
Отдельный каталог кэша исключает подмену live-результатов старым Bing/fixture кэшем.
Отчёт включает фактические запросы, количество результатов, SKU, источники,
13 проверок каждой модели и итоговые статусы. Заголовки авторизации не сохраняются.

## Официальная документация

- [REST WebSearch.Search](https://aistudio.yandex.ru/ru/docs/search-api/api-ref/WebSearch/search)
- [Начало работы и создание ключа](https://aistudio.yandex.ru/ru/docs/search-api/quickstart/)
- [Аутентификация](https://aistudio.yandex.ru/ru/docs/search-api/api-ref/authentication)
- [Отложенный поиск](https://aistudio.yandex.ru/ru/docs/search-api/operations/web-search)
- [Квоты и лимиты](https://aistudio.yandex.ru/ru/docs/search-api/concepts/limits)
- [Тарифы](https://aistudio.yandex.ru/ru/docs/search-api/pricing)
