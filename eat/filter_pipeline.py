"""Получение 200 закупок ЕАТ и применение настраиваемых бизнес-фильтров."""

from __future__ import annotations

import copy
import json
import time
from urllib.parse import urlsplit
from collections import Counter
from typing import Any

from playwright.sync_api import Request, Response, sync_playwright

from eat.browser_session import PROJECT_ROOT, _sanitize, _save
from eat.session_state import save_eat_session
from eat.browser_policy import open_authorized_eat_browser
from eat.browser_auth import BatchBrowserAuth, BrowserAuthError, PURCHASES_URL
from eat.pagination_test import (
    ENDPOINT_PART,
    PAGE_KEYS,
    SIZE_KEYS,
    _find_numeric_fields,
    _largest_purchase_batch,
    _normalized,
    _set_path,
)
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2


FILTERED_PATH = PROJECT_ROOT / "data" / "eat_filtered_test.json"
GREEN_PATH = PROJECT_ROOT / "data" / "eat_green_test.json"
PURCHASE_TYPES_URL = "https://tender-api.agregatoreat.ru/api/Trade/filter-purchase-types"


def _purchase_type_map(payload: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(payload, dict):
        identifier = (
            payload.get("id")
            if payload.get("id") is not None
            else payload.get(
                "value",
                payload.get(
                    "key",
                    payload.get(
                        "code",
                        payload.get("purchaseType", payload.get("purchaseTypeId")),
                    ),
                ),
            )
        )
        title = payload.get("title") or payload.get("name")
        if identifier is not None and isinstance(title, str):
            result[str(identifier)] = title
        for value in payload.values():
            result.update(_purchase_type_map(value))
    elif isinstance(payload, list):
        for value in payload:
            result.update(_purchase_type_map(value))
    return result


def _green_record(raw: dict[str, Any], filtered: dict[str, Any]) -> dict[str, Any]:
    organizer = raw.get("organizerInfo")
    return {
        "id": raw.get("id"),
        "tradeNumber": raw.get("tradeNumber"),
        "subject": raw.get("subject"),
        "price": raw.get("price"),
        "organizer": organizer,
        "normalized_regions": filtered["normalized_regions"],
        "deliveryAddress": filtered["delivery_addresses"],
        "publishDate": raw.get("publishDate"),
        "applicationFillingEndDate": raw.get("applicationFillingEndDate"),
        "purchaseTypeTitle": filtered["purchaseTypeTitle"],
        "items_count": filtered["items_count"],
        "URL": f"https://agregatoreat.ru/purchases/announcement/{raw.get('id')}/info",
        "filter_result": "passed",
    }


def run_filter_test(limit: int = 200, pause_seconds: float = 0.8) -> int:
    config = load_config()
    captured: dict[str, Any] = {}
    captured_types: dict[str, str] = dict(config["proven_purchase_type_titles"])
    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page
        auth = BatchBrowserAuth(page)
        auth.authenticated = True

        def login():
            auth.authenticate()
            save_eat_session(context)

        def refresh_capture():
            captured.clear()
            page.goto(PURCHASES_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(10_000)
            if not captured:
                raise BrowserAuthError("Не удалось обновить сессию массового поиска ЕАТ")

        def current_headers():
            return {key: value for key, value in captured.get("headers", {}).items()
                    if key.lower() not in {"host", "content-length", "cookie"}}


        def capture(response: Response) -> None:
            if urlsplit(response.url).hostname != "tender-api.agregatoreat.ru":
                return
            request: Request = response.request
            content_type = response.headers.get("content-type", "").lower()
            if response.status != 200 or "json" not in content_type:
                return
            try:
                payload = response.json()
            except Exception:
                return
            if ENDPOINT_PART in response.url and _largest_purchase_batch(payload):
                captured.update({
                    "url": response.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data_json if request.post_data else None,
                })
            if "filter-purchase-types" in response.url:
                captured_types.update(_purchase_type_map(payload))

        page.on("response", capture)
        try:
            login()
            refresh_capture()
            body = captured.get("post_data")
            if not isinstance(body, dict):
                print("Не удалось перехватить POST списка закупок.")
                return 1
            page_fields = _find_numeric_fields(body, PAGE_KEYS)
            size_fields = _find_numeric_fields(body, SIZE_KEYS)
            if len(page_fields) != 1 or len(size_fields) != 1:
                print("Не удалось однозначно определить page/size.")
                return 1
            page_path, _ = page_fields[0]
            size_path, _ = size_fields[0]
            if not captured_types:
                response = auth.fetch(lambda: context.request.get(PURCHASE_TYPES_URL, headers=current_headers(), max_redirects=0),
                                      refresh=refresh_capture)
                if response.ok:
                    captured_types.update(_purchase_type_map(response.json()))

            unique: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            seen_numbers: set[str] = set()
            raw_count = 0
            duplicate_count = 0
            requested_page = 1
            while len(unique) < limit and requested_page <= 80:
                request_body = copy.deepcopy(body)
                _set_path(request_body, page_path, requested_page)
                _set_path(request_body, size_path, 10)
                response = auth.fetch(
                    lambda: context.request.fetch(captured["url"], method=captured["method"],
                                                  headers=current_headers(), data=request_body, max_redirects=0),
                    refresh=refresh_capture,
                )
                if not response.ok:
                    raise RuntimeError(f"page={requested_page}: HTTP {response.status}")
                batch = _largest_purchase_batch(response.json())
                if not batch:
                    break
                raw_count += len(batch)
                for raw in batch:
                    item_id = raw.get("id")
                    number = raw.get("tradeNumber")
                    duplicate = bool(
                        (item_id and item_id in seen_ids)
                        or (number and number in seen_numbers)
                    )
                    if duplicate:
                        duplicate_count += 1
                        continue
                    if not item_id:
                        continue
                    seen_ids.add(item_id)
                    if number:
                        seen_numbers.add(number)
                    unique.append(raw)
                    if len(unique) >= limit:
                        break
                requested_page += 1
                time.sleep(pause_seconds)

            # No cards (including confidential purchases) are opened during batch search.
            filtered_records: list[dict[str, Any]] = []
            for raw in unique:
                title = captured_types.get(str(raw.get("purchaseTypeId")))
                decision = filter_purchase_v2(raw, title, config)
                filtered_records.append({
                    "raw_id": raw.get("id"),
                    "normalized": _normalized(raw),
                    **decision,
                    "raw": _sanitize(raw),
                })

            green = [
                _green_record(record["raw"], record)
                for record in filtered_records
                if record["filter_result"] == "passed"
            ]
            independent = Counter()
            results = Counter()
            reasons = Counter()
            unmatched = Counter()
            for record in filtered_records:
                results[record["filter_result"]] += 1
                for rule, passed in record["rule_checks"].items():
                    if passed:
                        independent[rule] += 1
                reasons.update(record["rejection_reasons"])
                unmatched.update(record["unmatched_regions"])

            metadata = {
                "unique_checked": len(filtered_records),
                "raw_records": raw_count,
                "live_feed_duplicates": duplicate_count,
                "api_pages_read": requested_page - 1,
                "independent_statistics": dict(independent),
                "results": dict(results),
                "top_15_reasons": reasons.most_common(15),
                "unmatched_region_names": dict(unmatched),
                "purchase_type_dictionary_size": len(captured_types),
                "confidential_locked": results["confidential_locked"],
                "real_manual_check": results["manual_check"],
                "parser_errors": sum(1 for raw in unique if not raw.get("id")),
            }
            _save(FILTERED_PATH, {"metadata": metadata, "purchases": filtered_records})
            _save(GREEN_PATH, {"metadata": metadata, "purchases": green})
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            return 0 if len(filtered_records) >= limit else 1
        except BrowserAuthError as exc:
            print(str(exc))
            return 1
        finally:
            captured.clear()
            session.close()
