"""Безопасная проверка публичного API сайта ЕАТ в Playwright-сессии."""

from __future__ import annotations

from eat.public_api_collection import PublicEatApiError, run_public_api_probe


def run_api_smoke_test() -> int:
    try:
        result = run_public_api_probe()
    except PublicEatApiError as error:
        diagnostic = error.diagnostic
        print(
            "Публичный API ЕАТ недоступен: "
            f"HTTP {diagnostic.get('http_status')}, "
            f"тип={diagnostic.get('content_type') or 'не указан'}, "
            f"ответ={diagnostic.get('classification')}"
        )
        observed = diagnostic.get("observed_api_responses") or []
        if observed:
            print(f"Безопасно зафиксировано ответов API-доменов: {len(observed)}")
        return 1
    except Exception as error:
        print(f"Проверка API сайта ЕАТ не пройдена: {error}")
        return 1

    print(
        "Публичный API ЕАТ доступен через Playwright APIRequestContext: "
        f"получено {result['returned_count']} из {result['total_count']} закупок; "
        f"источник запроса={result['request_source']}; "
        f"цен вне заданного диапазона={result['prices_outside_range']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_api_smoke_test())
