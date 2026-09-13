"""Ручная авторизация в ЕАТ и чтение закупок из текущей сессии браузера."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, Response, sync_playwright
from eat.browser_policy import open_authorized_eat_browser


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "eat_authorized_test.json"
DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "eat_network_diagnostics.json"
LOG_PATH = PROJECT_ROOT / "logs" / "eat_browser.log"
START_URL = "https://agregatoreat.ru/"
EAT_DOMAINS = ("agregatoreat.ru",)

SECRET_MARKERS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "csrf",
    "privatekey",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "number", "purchaseid", "ordernumber", "orderid", "registrynumber"),
    "number": (
        "tradenumber",
        "externaltradenumber",
        "eistradenumber",
        "number",
        "ordernumber",
        "registrynumber",
    ),
    "name": ("name", "title", "subject", "purchasename", "ordername"),
    "amount": ("amount", "price", "startprice", "initialprice", "maxprice", "total"),
    "customer": ("organizerinfo", "customer", "customername", "organization", "organisation"),
    "publication_date": ("publicationdate", "publishdate", "createdate", "createdat"),
    "end_date": (
        "applicationfillingenddate",
        "enddate",
        "deadline",
        "applicationenddate",
        "finishdate",
    ),
    "delivery_place_or_region": ("deliveryplace", "deliveryaddress", "region", "location"),
    "law_or_basis": ("law", "basis", "purchasebasis", "regulation"),
    "status": ("lotstate", "status", "statename", "orderstatus", "purchasestatus"),
    "url": ("url", "link", "href", "purchaseurl", "orderurl"),
}


def _logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tender_ai.eat.browser")
    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _is_eat_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in EAT_DOMAINS)


def _safe_url(url: str) -> str:
    """Оставить имена query-параметров, но удалить все их значения."""
    parts = urlsplit(url)
    safe_query = urlencode(
        [(name, "<redacted>") for name, _ in parse_qsl(parts.query)]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))


def _is_secret_key(key: object) -> bool:
    normalized = re.sub(r"[^a-zа-я0-9]", "", str(key).lower())
    return any(marker in normalized for marker in SECRET_MARKERS)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if _is_secret_key(key) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _json_shape(value: Any, depth: int = 0) -> Any:
    """Описать структуру JSON без сохранения значений."""
    if depth >= 5:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): "redacted" if _is_secret_key(key) else _json_shape(item, depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "item": _json_shape(value[0], depth + 1) if value else None,
        }
    return type(value).__name__


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", str(key).lower())


def _looks_like_purchase(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {_normalized_key(key) for key in item}
    matches = sum(
        bool(keys.intersection(aliases)) for aliases in FIELD_ALIASES.values()
    )
    return matches >= 4 or (
        matches >= 3
        and any(
            marker in key
            for key in keys
            for marker in ("purchase", "order", "trade", "tender", "закуп")
        )
    )


def _find_purchase_lists(value: Any) -> list[list[dict[str, Any]]]:
    found: list[list[dict[str, Any]]] = []
    if isinstance(value, list):
        objects = [item for item in value if isinstance(item, dict)]
        if objects and sum(_looks_like_purchase(item) for item in objects) >= max(1, len(objects) // 2):
            found.append(objects)
        for item in value[:50]:
            found.extend(_find_purchase_lists(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_find_purchase_lists(item))
    return found


def _find_alias_value(value: Any, aliases: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalized_key(key) in aliases and item not in (None, "", [], {}):
                return item
        for item in value.values():
            result = _find_alias_value(item, aliases)
            if result not in (None, "", [], {}):
                return result
    elif isinstance(value, list):
        for item in value[:20]:
            result = _find_alias_value(item, aliases)
            if result not in (None, "", [], {}):
                return result
    return None


def _prepare_purchase(item: dict[str, Any], source_url: str, page_url: str) -> dict[str, Any]:
    clean = _sanitize(item)
    normalized = {
        field: _find_alias_value(clean, aliases)
        for field, aliases in FIELD_ALIASES.items()
    }
    purchase_id = normalized.get("id")
    if purchase_id:
        normalized["url"] = (
            f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"
        )
    elif not normalized["url"]:
        normalized["url"] = page_url
    return {
        **normalized,
        "source_api_url": _safe_url(source_url),
        "source_data": clean,
    }


def _auth_problem(page: Page) -> str | None:
    url = page.url.lower()
    if "login.agregatoreat.ru" in url or "/account/login" in url:
        return "Открыта страница входа, авторизация ещё не завершена."
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return None
    if "are you sure you’re not a robot" in text or "вы не робот" in text:
        return "На странице всё ещё отображается CAPTCHA."
    return None


def run_browser_test(limit: int = 20) -> int:
    """Запустить видимый браузер и использовать только его текущую сессию."""
    logger = _logger()
    diagnostics: list[dict[str, Any]] = []
    purchase_batches: list[tuple[str, list[dict[str, Any]]]] = []
    _save(OUTPUT_PATH, [])

    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page

        def inspect_response(response: Response) -> None:
            request = response.request
            if request.resource_type not in {"xhr", "fetch"} or not _is_eat_url(response.url):
                return
            content_type = response.headers.get("content-type", "")
            entry: dict[str, Any] = {
                "url": _safe_url(response.url),
                "method": request.method,
                "status": response.status,
                "content_type": content_type,
                "request_header_names": sorted(request.headers.keys()),
                "response_header_names": sorted(response.headers.keys()),
                "json_structure": None,
            }
            if "json" in content_type.lower():
                try:
                    payload = response.json()
                    entry["json_structure"] = _json_shape(payload)
                    for batch in _find_purchase_lists(payload):
                        purchase_batches.append((response.url, batch))
                except Exception as error:
                    entry["json_read_error"] = type(error).__name__
            diagnostics.append(entry)

        page.on("response", inspect_response)
        try:
            problem = _auth_problem(page)
            if problem:
                logger.error(problem)
                print(problem)
            else:
                print("Проверяю сетевые запросы страницы закупок…")
                page.reload(wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(12_000)

            purchases: list[dict[str, Any]] = []
            seen: set[str] = set()
            for source_url, batch in purchase_batches:
                for item in batch:
                    prepared = _prepare_purchase(item, source_url, page.url)
                    identity = json.dumps(
                        [prepared.get("id"), prepared.get("name"), prepared.get("url")],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if identity not in seen:
                        seen.add(identity)
                        purchases.append(prepared)
                    if len(purchases) >= limit:
                        break
                if len(purchases) >= limit:
                    break

            _save(OUTPUT_PATH, purchases)
            if purchases:
                print(f"Получено закупок: {len(purchases)}")
                print(f"Результат: {OUTPUT_PATH}")
            else:
                _save(DIAGNOSTICS_PATH, diagnostics)
                print("Закупки не найдены.")
                print(f"Безопасная диагностика: {DIAGNOSTICS_PATH}")

            return 0 if purchases else 1
        except KeyboardInterrupt:
            logger.info("Тест остановлен пользователем.")
            return 130
        except Exception as error:
            logger.exception("Ошибка браузерного теста: %s", error)
            _save(DIAGNOSTICS_PATH, diagnostics)
            print(f"Ошибка браузерного теста: {type(error).__name__}: {error}")
            print(f"Безопасная диагностика: {DIAGNOSTICS_PATH}")
            return 1
        finally:
            session.close()
