# Optional AI foundation

TENDER_AI всегда сначала запускает детерминированный CORE. AI вызывается только
для `UNKNOWN`, `AMBIGUOUS`, `LOW_CONFIDENCE` или ошибки локального анализа.

Глобальный режим находится в `config/ai.json`: `AUTO`, `OPENAI`, `YANDEX`,
`OFF`. В `AUTO` порядок эскалации после неуверенного CORE: OpenAI, Yandex AI,
затем безопасный результат `MANUAL_REVIEW_REQUIRED`. Codex не является runtime
provider. `model_search/yandex_provider.py` является веб-поиском и не связан с
Yandex AI.

Сегодня созданы только интерфейсы и безопасные заглушки. SDK, реальные API и
credentials не подключены. Будущие credentials должны находиться только в
отдельных записях macOS Keychain `TENDER_AI_OPENAI` и
`TENDER_AI_YANDEX_AI`; `.env`, логи и checkpoint для них не используются.

## Постепенное подключение skill

Существующий skill сохраняет свою детерминированную функцию как `core_solver`
и обращается к `AIRouter.resolve(...)` только на уже определённой границе
`UNKNOWN`, `AMBIGUOUS` или `LOW_CONFIDENCE`. Уверенный
`CoreDecision(status="RESOLVED", ...)` возвращается немедленно, без проверки
credentials и AI providers. Skill сохраняет только provider, статус, причину
вызова и примерную стоимость — никогда не credential.

Фильтры ЕАТ, документы/OCR, model discovery, поиск цен, проверка поставщиков,
calculator и Google Sheets намеренно пока не подключены к abstraction.
