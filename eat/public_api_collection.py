"""Public-first сбор ЕАТ через APIRequestContext той же Playwright-сессии.

Браузер открывает только публичный список и даёт сайту сформировать штатный
POST. Дальнейшая пагинация выполняется APIRequestContext. Сохранённая сессия,
логин/пароль и EAT_API_TOKEN в этом модуле не используются.
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import APIResponse, Request, Response, sync_playwright

from eat.browser_auth import PURCHASES_URL
from eat.browser_session import PROJECT_ROOT
from eat.filter_pipeline import PURCHASE_TYPES_URL, _purchase_type_map
from eat.live_collection import (
    AUDIT_PATH,
    COLLECTION_PATH,
    DEFAULT_PAGE_SIZE,
    _atomic_json,
    _load_already_processed_ids,
    _request_headers,
    _utc_now,
    audit_unique_records,
    collect_all_pages,
)
from eat.pagination_test import (
    ENDPOINT_PART,
    PAGE_KEYS,
    SIZE_KEYS,
    _find_numeric_fields,
    _set_path,
)
from filters.eat_filters import load_config


DIAGNOSTIC_PATH = PROJECT_ROOT / "data" / "eat_public_api_diagnostic.json"
API_HOSTS = {"tender-api.agregatoreat.ru", "tender-cache-api.agregatoreat.ru"}
PUBLIC_LIST_API_URL = (
    "https://tender-cache-api.agregatoreat.ru"
    "/api/TradeLot/list-published-trade-lots"
)
LOGIN_MARKERS = ("/account/login", "type=\"password\"", "type='password'")
CAPTCHA_RE = re.compile(
    r"captcha|g-recaptcha|h-captcha|вы не робот|not a robot|провер.{0,20}человек",
    re.I,
)


class PublicEatApiError(RuntimeError):
    def __init__(self, diagnostic: dict[str, Any]):
        self.diagnostic = diagnostic
        super().__init__(
            f"{diagnostic.get('classification')}: HTTP {diagnostic.get('http_status')}, "
            f"content-type={diagnostic.get('content_type') or 'не указан'}"
        )


def _header(headers: dict[str, str], name: str) -> str:
    wanted = name.casefold()
    return next((str(value) for key, value in headers.items()
                 if str(key).casefold() == wanted), "")


def classify_response(response: APIResponse | Response) -> dict[str, Any]:
    """Классифицировать ответ, не сохраняя URL-параметры, headers или body."""
    status = int(response.status)
    headers = dict(response.headers)
    content_type = _header(headers, "content-type").split(";", 1)[0].strip().casefold()
    location = _header(headers, "location")
    response_url = str(response.url)
    target_url = urljoin(response_url, location) if location else response_url
    target = urlsplit(target_url)

    text = ""
    if "html" in content_type or status >= 400:
        try:
            text = response.text()[:200_000]
        except Exception:
            text = ""
    folded = text.casefold()

    if CAPTCHA_RE.search(text):
        classification = "captcha"
    elif (
        target.hostname == "login.agregatoreat.ru"
        or any(marker in target.path.casefold() or marker in folded for marker in LOGIN_MARKERS)
    ):
        classification = "login_page"
    elif status >= 400:
        classification = "api_error"
    elif content_type in {"application/json", "text/json"} or "json" in content_type:
        classification = "json_api"
    elif "html" in content_type:
        classification = "html_page"
    else:
        classification = "unexpected_response"

    return {
        "http_status": status,
        "content_type": content_type or None,
        "classification": classification,
        "redirect_to_login": classification == "login_page",
        "captcha_detected": classification == "captcha",
    }


def _page_diagnostic(
    page: Any,
    navigation_response: Response | None = None,
    observed_api_responses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    url = urlsplit(str(page.url))
    text = ""
    try:
        text = page.locator("body").inner_text(timeout=2_000)[:200_000]
    except Exception:
        pass
    if CAPTCHA_RE.search(text):
        classification = "captcha"
    elif url.hostname == "login.agregatoreat.ru" or "/account/login" in url.path.casefold():
        classification = "login_page"
    elif navigation_response is not None and navigation_response.status >= 400:
        classification = "api_error"
    else:
        classification = "api_request_not_observed"
    return {
        "http_status": int(navigation_response.status) if navigation_response else None,
        "content_type": (
            _header(dict(navigation_response.headers), "content-type").split(";", 1)[0]
            if navigation_response else "text/html"
        ),
        "classification": classification,
        "redirect_to_login": classification == "login_page",
        "captcha_detected": classification == "captcha",
        "page_host": url.hostname,
        "page_path": url.path,
        "observed_api_responses": observed_api_responses or [],
    }


def _save_diagnostic(diagnostic: dict[str, Any]) -> None:
    _atomic_json(DIAGNOSTIC_PATH, {"timestamp": _utc_now(), **diagnostic})


def _program_request_body(config: dict[str, Any]) -> dict[str, Any]:
    """Минимальный публичный контракт API с настройками нашей программы."""
    price = config.get("price") or {}
    return {
        "page": 1,
        "size": DEFAULT_PAGE_SIZE,
        "sort": [{"fieldName": "publishDate", "direction": 2}],
        "priceStart": price.get("min"),
        "priceEnd": price.get("max"),
    }


def _public_headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://agregatoreat.ru",
        "referer": PURCHASES_URL,
    }


def _apply_program_api_filters(
    body: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Перенести в API только фильтры с однозначным контрактом ЕАТ.

    Закон, регионы, число позиций и смысловые исключения продолжают
    проверяться локально: для них API либо ждёт внутренние идентификаторы,
    либо вообще не имеет эквивалентного поля.
    """
    result = copy.deepcopy(body)
    price = config.get("price") or {}
    result["priceStart"] = price.get("min")
    result["priceEnd"] = price.get("max")
    return result


def _capture_public_request(
    page: Any,
    config: dict[str, Any],
    *,
    timeout_seconds: float = 5,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    last_diagnostic: dict[str, Any] | None = None
    observed_api_responses: list[dict[str, Any]] = []

    def capture(response: Response) -> None:
        nonlocal last_diagnostic
        parsed = urlsplit(response.url)
        if parsed.hostname in API_HOSTS and len(observed_api_responses) < 50:
            content_type = _header(dict(response.headers), "content-type").split(";", 1)[0]
            observed_api_responses.append({
                "host": parsed.hostname,
                "path": parsed.path,
                "http_status": int(response.status),
                "content_type": content_type or None,
            })
        if (ENDPOINT_PART not in response.url
                or parsed.hostname not in API_HOSTS):
            return
        last_diagnostic = classify_response(response)
        request: Request = response.request
        body = None
        try:
            body = request.post_data_json if request.post_data else None
        except Exception:
            body = None
        if response.status == 200 and "json" in (last_diagnostic["content_type"] or ""):
            captured.update({
                "url": response.url,
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": _apply_program_api_filters(body, config)
                if isinstance(body, dict) else body,
                "request_source": "browser_observed",
            })

    page.on("response", capture)
    navigation_response = page.goto(
        PURCHASES_URL, wait_until="domcontentloaded", timeout=60_000
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not captured and last_diagnostic is None:
        page.wait_for_timeout(250)
    if captured:
        return captured
    diagnostic = last_diagnostic or _page_diagnostic(
        page, navigation_response, observed_api_responses
    )
    if diagnostic["classification"] in {"login_page", "captcha", "api_error"}:
        _save_diagnostic(diagnostic)
        raise PublicEatApiError(diagnostic)

    # Новый SPA ЕАТ иногда загружает пустую оболочку и не инициирует список в
    # headless-браузере. Сам endpoint при этом остаётся публичным. Используем
    # подтверждённый контракт POST и всё равно выполняем его APIRequestContext
    # именно этого browser context, то есть с cookies той же сессии.
    return {
        "url": PUBLIC_LIST_API_URL,
        "method": "POST",
        "headers": _public_headers(),
        "post_data": _program_request_body(config),
        "request_source": "program_template_after_public_page",
        "page_diagnostic": diagnostic,
    }


def _pagination_paths(body: Any) -> tuple[tuple[Any, ...], int, tuple[Any, ...]]:
    if not isinstance(body, dict):
        raise PublicEatApiError({
            "http_status": 200,
            "content_type": "application/json",
            "classification": "request_body_not_json_object",
            "redirect_to_login": False,
            "captcha_detected": False,
        })
    page_fields = _find_numeric_fields(body, PAGE_KEYS)
    size_fields = _find_numeric_fields(body, SIZE_KEYS)
    if len(page_fields) != 1 or len(size_fields) != 1:
        raise PublicEatApiError({
            "http_status": 200,
            "content_type": "application/json",
            "classification": "pagination_fields_ambiguous",
            "redirect_to_login": False,
            "captcha_detected": False,
        })
    page_path, initial_page = page_fields[0]
    return page_path, initial_page, size_fields[0][0]


def _request_body(
    captured_body: dict[str, Any],
    page_path: tuple[Any, ...],
    initial_page: int,
    size_path: tuple[Any, ...],
    logical_page: int,
    page_size: int,
) -> dict[str, Any]:
    body = copy.deepcopy(captured_body)
    _set_path(body, page_path, initial_page + logical_page - 1)
    _set_path(body, size_path, page_size)
    return body


def _fetch_json(
    context: Any,
    captured: dict[str, Any],
    body: dict[str, Any],
    *,
    max_attempts: int = 4,
) -> Any:
    """Получить JSON, повторяя только временные сетевые/серверные ошибки."""
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_diagnostic: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = context.request.fetch(
                captured["url"],
                method=captured["method"],
                headers=_request_headers(captured["headers"]),
                data=body,
                max_redirects=0,
                timeout=60_000,
            )
        except Exception as error:
            last_diagnostic = {
                "http_status": None,
                "content_type": None,
                "classification": "transport_error",
                "transport_error_type": type(error).__name__,
                "attempts": attempt,
                "redirect_to_login": False,
                "captcha_detected": False,
            }
            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue
            break

        diagnostic = classify_response(response)
        diagnostic["attempts"] = attempt
        if not response.ok or diagnostic["classification"] != "json_api":
            last_diagnostic = diagnostic
            if response.status in retryable_statuses and attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue
            break
        try:
            return response.json()
        except Exception:
            diagnostic["classification"] = "invalid_json"
            last_diagnostic = diagnostic
            break

    assert last_diagnostic is not None
    _save_diagnostic(last_diagnostic)
    raise PublicEatApiError(last_diagnostic)


def _new_public_context(playwright: Any) -> tuple[Any, Any, Any]:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(locale="ru-RU", service_workers="block")
    return browser, context, context.new_page()


def run_public_api_probe() -> dict[str, Any]:
    """Проверить публичный API одним явным APIRequestContext-запросом."""
    with sync_playwright() as playwright:
        browser, context, page = _new_public_context(playwright)
        try:
            config = load_config()
            captured = _capture_public_request(page, config)
            page_path, initial_page, size_path = _pagination_paths(captured["post_data"])
            body = _request_body(
                captured["post_data"], page_path, initial_page, size_path,
                logical_page=1, page_size=min(DEFAULT_PAGE_SIZE, 100),
            )
            payload = _fetch_json(context, captured, body)
            from eat.live_collection import _purchase_batch, _total_count

            batch = _purchase_batch(payload)
            price_config = config.get("price") or {}
            minimum = price_config.get("min")
            maximum = price_config.get("max")
            prices = [
                item.get("price") for item in batch
                if isinstance(item.get("price"), (int, float))
                and not isinstance(item.get("price"), bool)
            ]
            prices_outside_range = sum(
                (minimum is not None and price < minimum)
                or (maximum is not None and price > maximum)
                for price in prices
            )
            diagnostic = {
                "http_status": 200,
                "content_type": "application/json",
                "classification": "json_api",
                "redirect_to_login": False,
                "captcha_detected": False,
                "returned_count": len(batch),
                "total_count": _total_count(payload),
                "request_source": captured["request_source"],
                "api_filter_fields": ["priceStart", "priceEnd"],
                "configured_price_range": {"min": minimum, "max": maximum},
                "priced_records_checked": len(prices),
                "prices_outside_range": prices_outside_range,
            }
            _save_diagnostic(diagnostic)
            return diagnostic
        finally:
            context.close()
            browser.close()


def run_public_api_collection_audit(*, pause_seconds: float = 0.1) -> dict[str, Any]:
    """Вся публичная выдача API → dedup → существующие фильтры программы."""
    started = time.monotonic()
    started_at = _utc_now()
    config = load_config()
    purchase_types = dict(config["proven_purchase_type_titles"])

    with sync_playwright() as playwright:
        browser, context, page = _new_public_context(playwright)
        try:
            captured = _capture_public_request(page, config)
            page_path, initial_page, size_path = _pagination_paths(captured["post_data"])

            def fetch_page(logical_page: int, page_size: int) -> Any:
                return _fetch_json(
                    context,
                    captured,
                    _request_body(
                        captured["post_data"], page_path, initial_page, size_path,
                        logical_page, page_size,
                    ),
                )

            try:
                response = context.request.get(
                    PURCHASE_TYPES_URL,
                    headers=_request_headers(captured["headers"]),
                    max_redirects=0,
                    timeout=10_000,
                )
                if response.ok and "json" in _header(
                    dict(response.headers), "content-type"
                ).casefold():
                    purchase_types.update(_purchase_type_map(response.json()))
            except Exception:
                pass

            def save_progress(snapshot: dict[str, Any]) -> None:
                _atomic_json(AUDIT_PATH, {
                    "run": {"started_at": started_at, "status": snapshot["status"],
                            "access_mode": "public_playwright_api_request_context"},
                    "pagination": snapshot,
                    "filter_audit": None,
                })

            pagination = collect_all_pages(
                fetch_page,
                endpoint=captured["url"],
                page_size=DEFAULT_PAGE_SIZE,
                pause_seconds=pause_seconds,
                progress=save_progress,
            )
            if not pagination["end_proven"]:
                raise RuntimeError(pagination["diagnostic_error"] or "Конец выдачи не доказан")

            audit = audit_unique_records(
                pagination["unique_records"],
                purchase_types,
                already_processed_ids=_load_already_processed_ids(),
            )
            pagination_report = {
                key: value for key, value in pagination.items()
                if key not in {"raw_records", "unique_records"}
            }
            report = {
                "run": {
                    "started_at": started_at,
                    "finished_at": _utc_now(),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "status": "completed",
                    "access_mode": "public_playwright_api_request_context",
                    "request_source": captured["request_source"],
                    "api_filter_fields": ["priceStart", "priceEnd"],
                    "documents_downloaded": False,
                    "google_sheets_written": False,
                },
                "pagination": pagination_report,
                "filter_audit": audit["counts"],
            }
            _atomic_json(AUDIT_PATH, report)
            _atomic_json(COLLECTION_PATH, {"metadata": report, "purchases": audit["purchases"]})
            return report
        except PublicEatApiError:
            raise
        finally:
            context.close()
            browser.close()
