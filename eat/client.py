"""Безопасная проверка публичного доступа к закупкам ЕАТ."""

from __future__ import annotations

from eat.auth_client import EatClient

import json
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "eat_test.json"
LOG_PATH = PROJECT_ROOT / "logs" / "eat.log"

PUBLIC_PURCHASES_URL = "https://agregatoreat.ru/purchases"
OFFICIAL_ORDER_LIST_API = (
    "https://agregatoreat.ru/integration/ecom/rest/api/order/requestOrderList"
)


class EatAccessError(RuntimeError):
    """ЕАТ не предоставил публичный список закупок без авторизации."""


def _configure_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tender_ai.eat")
    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _save_result(purchases: list[dict[str, object]]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(purchases, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_test_purchases(limit: int = 20) -> list[dict[str, object]]:
    """Получить до ``limit`` закупок, только если ЕАТ отдаёт их публично.

    Официальный интеграционный API проверяется первым. Токены, cookies и
    автоматическое прохождение CAPTCHA намеренно не используются.
    """

    logger = _configure_logger()
    _save_result([])

    request = Request(
        OFFICIAL_ORDER_LIST_API,
        data=b"<requestOrderList />",
        headers={
            "Accept": "application/xml, application/json",
            "Content-Type": "application/xml",
            "User-Agent": "TENDER_AI/0.1 (public-access-check)",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(1000).decode("utf-8", errors="replace")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        message = (
            "Официальный API списка закупочных сессий ЕАТ недоступен без "
            f"авторизации: HTTP {error.code}. "
            f"Адрес: {OFFICIAL_ORDER_LIST_API}."
        )
        logger.error(message)
        raise EatAccessError(message) from None
    except URLError as error:
        message = (
            "Не удалось подключиться к официальному API ЕАТ: "
            f"Адрес: {OFFICIAL_ORDER_LIST_API}"
        )
        logger.error(message)
        raise EatAccessError(message) from None

    message = (
        "ЕАТ вернул неожиданный ответ вместо публичного списка закупок. "
        f"Адрес: {OFFICIAL_ORDER_LIST_API}."
    )
    logger.error(message)
    raise EatAccessError(message)

