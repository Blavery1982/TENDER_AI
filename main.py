import argparse


def _eat_collection_backend() -> str:
    """Прочитать переключатель без зависимости от python-dotenv."""
    import os
    from eat.integration_client import _dotenv_values

    return os.environ.get("EAT_COLLECTION_BACKEND", _dotenv_values().get(
        "EAT_COLLECTION_BACKEND", "browser"
    )).strip().casefold()


def run_eat_test() -> int:
    from eat.client import EatAccessError, OUTPUT_PATH, fetch_test_purchases

    try:
        purchases = fetch_test_purchases(limit=20)
    except EatAccessError as error:
        print(f"Тест ЕАТ завершён без данных: {error}")
        print(f"Результат сохранён: {OUTPUT_PATH}")
        return 1

    print(f"Получено закупок: {len(purchases)}")
    print(f"Результат сохранён: {OUTPUT_PATH}")
    return 0


def run_eat_browser_test() -> int:
    from eat.browser_session import run_browser_test

    return run_browser_test(limit=20)


def run_eat_100_test() -> int:
    from eat.pagination_test import run_pagination_test

    return run_pagination_test(limit=100)


def run_eat_stable_test() -> int:
    from eat.stable_test import run_stable_test

    return run_stable_test(limit=100)


def run_eat_filter_test() -> int:
    from eat.filter_pipeline import run_filter_test

    return run_filter_test(limit=200)


def run_eat_live_collection_audit() -> int:
    from eat.live_collection import run_live_collection_audit

    result = run_live_collection_audit()
    pagination = result["pagination"]
    audit = result["filter_audit"]
    print(
        f"Полная выдача ЕАТ собрана: {pagination['unique_count']} уникальных "
        f"из {pagination['raw_count']} записей; прошло filter_purchase_v2: "
        f"{audit['passed_filter_v2']}"
    )
    return 0


def run_eat_collection_entrypoint() -> int:
    if _eat_collection_backend() == "api":
        return run_eat_live_api_collection_audit()
    return run_eat_live_collection_audit()


def run_eat_api_smoke_test() -> int:
    from eat.api_smoke_test import run_api_smoke_test
    return run_api_smoke_test()


def run_eat_live_api_collection_audit(detail_limit: int | None = None) -> int:
    from eat.live_api_collection import run_live_api_collection_audit

    result = run_live_api_collection_audit(detail_limit=detail_limit)
    print(
        f"API-сбор ЕАТ завершён: закупок {result['api']['references_count']}, "
        f"карточек {result['api']['details_requested']}; "
        f"прошло filter_purchase_v2: {result['filter_audit']['passed_filter_v2']}"
    )
    return 0


def run_eat_api_google_sheets_export(detail_limit: int | None = None) -> int:
    from google_sheets.eat_api_export import run_api_google_sheets_export

    result = run_api_google_sheets_export(detail_limit=detail_limit)
    print(f"Google-таблица обновлена: {result['spreadsheet_url']}")
    print(
        f"API-закупок: {result['references_count']}, карточек: {result['details_requested']}, "
        f"строк актуальных: {result['active_rows_written']}, "
        f"строк ручной проверки: {result['manual_rows_written']}"
    )
    return 0


def run_eat_contract_test() -> int:
    from eat.contract_analysis import run_contract_test
    return run_contract_test()

def run_google_sheets_sync() -> int:
    from google_sheets.client import sync
    print(f"Таблица обновлена: {sync()}")
    return 0

def run_google_sheets_setup() -> int:
    from google_sheets.client import setup_structure
    url,columns=setup_structure(); print(f"Структура создана: {url}"); print(columns); return 0

def run_google_sheets_test_export() -> int:
    from google_sheets.client import test_export
    print(test_export()); return 0

def run_google_sheets_fix_text() -> int:
    from google_sheets.client import fix_text_formatting
    print(fix_text_formatting()); return 0

def run_procurement_audit_test() -> int:
    from documents.procurement_audit import run_test
    result = run_test()
    print(result["pre_supplier_check_status_ru"])
    return 0


def run_tender_archive_audit() -> int:
    from documents.audit_tender_archive import audit_archive
    result = audit_archive()
    print(f"Архив тендеров проверен: всего {result['tenders']}; "
          f"марка найдена {result['brands_found']}; "
          f"не найдена {result['brands_not_found']}; "
          f"ручная проверка {result['review_required']}")
    print(f"Списки: {result['output_dir']}")
    return 0


def run_active_tender_download() -> int:
    from eat.batch_tender_archive import download_active_tenders
    result = download_active_tenders()
    print(f"Скачивание завершено: запрошено {result['requested']}; "
          f"успешно {result['downloaded']}; ошибок {result['failed']}")
    print(f"Архив: {result['archive_root']}")
    return 0

def run_model_search_test() -> int:
    from model_search.search import run_test
    result = run_test()
    print(f"Кандидатов: {sum(len(x['candidate_models']) for x in result['positions'])}")
    return 0

def run_price_search_test(model: str) -> int:
    from model_search.live_price_search import search_exact_model_prices
    result=search_exact_model_prices(model)
    print(f"Найдено предложений: {result['offers_found']}")
    print(f"Минимальная цена: {result['minimum_price'] if result['minimum_price'] is not None else 'Нет данных'}")
    return 0


def run_production_dry_run(resume: bool = False, model_live_test: bool = False) -> int:
    from pipeline.batch_orchestrator import run_local_dry_run
    result = run_local_dry_run(resume=resume, model_live_test=model_live_test)
    print(f"Локальный dry-run завершён: закупок {result['summary']['total_procurements']}, "
          f"позиций {result['summary']['total_items']}")
    print(f"Checkpoint: {result['checkpoint_path']}")
    print(f"Отчёт: {result['summary_path']}")
    return 0


def run_mvp_exact_batch(resume: bool = False) -> int:
    from pipeline.mvp_exact_batch import run
    result = run(resume=resume)
    summary = result["summary"]
    print(f"MVP завершён: exact-model позиций {summary['E. Позиции с точной моделью']}; "
          f"просчитано {summary['H. Exact-model позиций просчитано']}")
    print(f"Google Sheets: записано строк {summary['Google Sheets записано']}")
    if result.get("google_sheets_receipts"):
        print(f"Таблица: {result['google_sheets_receipts'][0]['spreadsheet_url']}")
    print(f"Отчёт: {result['artifacts']['report']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальная система TENDER_AI")
    parser.add_argument(
        "--eat-test",
        action="store_true",
        help="проверить получение первых 20 публичных закупок ЕАТ",
    )
    parser.add_argument(
        "--eat-browser-test",
        action="store_true",
        help="открыть браузер для ручного входа и получить до 20 закупок ЕАТ",
    )
    parser.add_argument(
        "--eat-100-test",
        action="store_true",
        help="получить до 100 закупок через текущую ручную сессию ЕАТ",
    )
    parser.add_argument(
        "--eat-stable-test",
        action="store_true",
        help="проверить стабильную пагинацию и получить 100 закупок ЕАТ",
    )
    parser.add_argument(
        "--eat-filter-test",
        action="store_true",
        help="получить 200 закупок ЕАТ и применить бизнес-фильтры",
    )
    parser.add_argument(
        "--eat-contract-test",
        action="store_true",
        help="проверить документы только текущих прошедших закупок",
    )
    parser.add_argument("--google-sheets-sync", action="store_true", help="обновить русский рабочий интерфейс Google Sheets")
    parser.add_argument("--google-sheets-setup", action="store_true", help="создать структуру новой Google-таблицы без загрузки закупок")
    parser.add_argument("--google-sheets-test-export", action="store_true", help="тестовый upsert сохранённых 200 закупок")
    parser.add_argument("--google-sheets-fix-text", action="store_true", help="исправить текстовый формат кодов, ссылок и контактов")
    parser.add_argument("--procurement-audit-test", action="store_true", help="предаудит документов одной сохранённой закупки")
    parser.add_argument("--audit-tender-archive", action="store_true", help="проверить локальный архив тендеров на марки и бренды")
    parser.add_argument("--download-active-tenders", action="store_true", help="скачать документы 139 актуальных тендеров из листа ACTIVE")
    parser.add_argument("--model-search-test", action="store_true", help="технический поиск моделей для одной контрольной закупки")
    parser.add_argument("--price-search-test", metavar="MODEL", help="ограниченный live-поиск цен точной модели")
    parser.add_argument("--production-dry-run", action="store_true", help="безопасный batch только на локальных fixtures")
    parser.add_argument("--resume", action="store_true", help="продолжить локальный dry-run с checkpoint")
    parser.add_argument("--model-live-test", action="store_true", help="ограниченный live-поиск моделей только для контрольных fixtures")
    parser.add_argument("--mvp-exact-batch", action="store_true", help="live MVP точных моделей, без Google Sheets; supplier search максимум 10 позиций")
    parser.add_argument("--batch-procurement-search", action="store_true", help="полный production-сбор ЕАТ и аудит filter_purchase_v2")
    parser.add_argument("--eat-live-collection-audit", action="store_true", help="полный production-сбор ЕАТ без документов, цен и Google Sheets")
    parser.add_argument("--eat-api-smoke-test", action="store_true", help="проверить публичный API сайта ЕАТ через Playwright-сессию")
    parser.add_argument("--eat-live-api-collection", action="store_true", help="собрать закупки через API сайта ЕАТ и применить текущие фильтры")
    parser.add_argument("--eat-api-detail-limit", type=int, default=None, help="ограничить число карточек в официальном API-сборе")
    parser.add_argument("--eat-api-google-sheets", action="store_true", help="получить список через API ЕАТ и выгрузить его в Google Sheets")
    parser.add_argument("--eat-login-check", action="store_true", help="только браузерный вход ЕАТ, без массового поиска")
    parser.add_argument("--eat-save-session", action="store_true", help="вручную подтвердить и безопасно сохранить сессию ЕАТ")
    args = parser.parse_args()

    if args.eat_save_session:
        from eat.session_state import run_manual_session_save
        return run_manual_session_save()
    if args.eat_login_check:
        from eat.browser_auth import run_login_check
        return run_login_check()
    if args.batch_procurement_search or args.eat_live_collection_audit:
        return run_eat_collection_entrypoint()
    if args.eat_api_smoke_test:
        return run_eat_api_smoke_test()
    if args.eat_live_api_collection:
        return run_eat_live_api_collection_audit(detail_limit=args.eat_api_detail_limit)
    if args.eat_api_google_sheets:
        return run_eat_api_google_sheets_export(detail_limit=args.eat_api_detail_limit)
    if args.mvp_exact_batch:
        return run_mvp_exact_batch(resume=args.resume)


    if args.eat_test:
        return run_eat_test()
    if args.eat_browser_test:
        return run_eat_browser_test()
    if args.eat_100_test:
        return run_eat_100_test()
    if args.eat_stable_test:
        return run_eat_stable_test()
    if args.eat_filter_test:
        return run_eat_filter_test()
    if args.eat_contract_test:
        return run_eat_contract_test()
    if args.google_sheets_sync:
        return run_google_sheets_sync()
    if args.google_sheets_setup:
        return run_google_sheets_setup()
    if args.google_sheets_test_export:
        return run_google_sheets_test_export()
    if args.google_sheets_fix_text:
        return run_google_sheets_fix_text()
    if args.procurement_audit_test:
        return run_procurement_audit_test()
    if args.audit_tender_archive:
        return run_tender_archive_audit()
    if args.download_active_tenders:
        return run_active_tender_download()
    if args.model_search_test:
        return run_model_search_test()
    if args.price_search_test:
        return run_price_search_test(args.price_search_test)
    if args.production_dry_run:
        return run_production_dry_run(resume=args.resume, model_live_test=args.model_live_test)
    if args.model_live_test:
        parser.error("--model-live-test используется только вместе с --production-dry-run")
    if args.resume:
        parser.error("--resume используется только вместе с --production-dry-run или --mvp-exact-batch")

    print("TENDER_AI")
    print("Система запущена успешно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
