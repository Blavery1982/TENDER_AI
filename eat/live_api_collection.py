"""Сбор закупок через публичный API сайта в Playwright-сессии."""

from __future__ import annotations

import json
from typing import Any

from eat.public_api_collection import run_public_api_collection_audit


def run_live_api_collection_audit(*, detail_limit: int | None = None) -> dict[str, Any]:
    """Запустить существующий сборщик: Playwright + APIRequestContext той же сессии."""
    report = run_public_api_collection_audit()
    report["api"] = {
        "endpoint_mode": "playwright_api_request_context",
        "references_count": report["pagination"]["unique_count"],
        "details_requested": report["pagination"]["unique_count"],
    }
    return report


if __name__ == "__main__":
    result = run_live_api_collection_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
