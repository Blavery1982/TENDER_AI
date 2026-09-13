"""Последовательное чтение страниц опубликованных закупок ЕАТ."""

from __future__ import annotations

import copy
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Request, Response, sync_playwright

from eat.browser_session import (
    FIELD_ALIASES,
    PROJECT_ROOT,
    START_URL,
    _auth_problem,
    _find_alias_value,
    _find_purchase_lists,
    _is_eat_url,
    _normalized_key,
    _sanitize,
    _save,
)
from eat.browser_policy import open_authorized_eat_browser


OUTPUT_PATH = PROJECT_ROOT / "data" / "eat_100_test.json"
FIELDS_PATH = PROJECT_ROOT / "data" / "eat_fields_report.json"
ENDPOINT_PART = "/api/TradeLot/list-published-trade-lots"
PAGE_KEYS = {"page", "pagenumber", "pageindex", "currentpage"}
SIZE_KEYS = {"size", "pagesize", "limit", "take"}
OFFSET_KEYS = {"offset", "skip"}
TOTAL_KEYS = {"total", "totalcount", "itemscount", "recordscount", "totalitems"}


def _find_numeric_fields(value: Any, wanted: set[str], path: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], int]]:
    found: list[tuple[tuple[Any, ...], int]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            new_path = (*path, key)
            if _normalized_key(key) in wanted and isinstance(item, int) and not isinstance(item, bool):
                found.append((new_path, item))
            found.extend(_find_numeric_fields(item, wanted, new_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_numeric_fields(item, wanted, (*path, index)))
    return found


def _set_path(value: Any, path: tuple[Any, ...], replacement: int) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


def _largest_purchase_batch(payload: Any) -> list[dict[str, Any]]:
    batches = _find_purchase_lists(payload)
    verified = [
        batch
        for batch in batches
        if sum(
            bool(item.get("id"))
            and bool(item.get("subject"))
            for item in batch
        )
        >= max(1, len(batch) // 2)
    ]
    return max(verified, key=len) if verified else []


def _total_from(payload: Any, minimum: int) -> tuple[str | None, int | None]:
    candidates = _find_numeric_fields(payload, TOTAL_KEYS)
    candidates = [(path, value) for path, value in candidates if value >= minimum]
    if not candidates:
        return None, None
    path, value = max(candidates, key=lambda pair: pair[1])
    return ".".join(map(str, path)), value


def _safe_raw(item: dict[str, Any]) -> dict[str, Any]:
    # В объектах закупок обычно нет реквизитов сессии; страховочно удаляем только
    # ключи, которые действительно обозначают секреты, не сокращая прочие поля ЕАТ.
    return _sanitize(item)


def _normalized(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        field: _find_alias_value(item, aliases)
        for field, aliases in FIELD_ALIASES.items()
    }
    purchase_id = result.get("id")
    result["url"] = (
        f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"
        if purchase_id
        else None
    )
    result["items_count"] = len(item.get("lotItems") or []) if isinstance(item.get("lotItems"), list) else None
    return result


def _filled(value: Any) -> bool:
    return value not in (None, "", [], {})


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _flatten(value: Any, prefix: str = "") -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = defaultdict(list)
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result[path].append(item)
            nested = _flatten(item, path)
            for nested_path, values in nested.items():
                result[nested_path].extend(values)
    elif isinstance(value, list):
        path = f"{prefix}[]"
        result[path].append(value)
        for item in value:
            nested = _flatten(item, path)
            for nested_path, values in nested.items():
                result[nested_path].extend(values)
    return result


def _fields_report(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        flattened = _flatten(item)
        for path, values in flattened.items():
            entry = aggregate.setdefault(path, {"types": set(), "example": None, "filled_in_purchases": 0})
            nonempty = [value for value in values if _filled(value)]
            entry["types"].update(_type_name(value) for value in values)
            if nonempty:
                entry["filled_in_purchases"] += 1
                if entry["example"] is None:
                    example = nonempty[0]
                    entry["example"] = example if not isinstance(example, (dict, list)) else _sanitize(example)
    return [
        {
            "field": path,
            "example": entry["example"],
            "data_types": sorted(entry["types"]),
            "filled_in_purchases": entry["filled_in_purchases"],
        }
        for path, entry in sorted(aggregate.items())
    ]


def _mapping_examples(items: list[dict[str, Any]]) -> dict[str, Any]:
    first = items[0] if items else {}
    def first_value(path: tuple[str, ...]) -> Any:
        for item in items:
            value: Any = item
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            if _filled(value):
                return value
        return None

    delivery_address = None
    for item in items:
        delivery_infos = item.get("deliveryInfos")
        if isinstance(delivery_infos, list):
            for info in delivery_infos:
                candidate = info.get("deliveryAddress") if isinstance(info, dict) else None
                if isinstance(candidate, dict) and (
                    _filled(candidate.get("formattedFullInfo"))
                    or _filled(candidate.get("regionName"))
                ):
                    delivery_address = candidate
                    break
        if delivery_address:
            break
    return {
        "purchase_number": {"field": "tradeNumber", "example": first_value(("tradeNumber",)), "confidence": "высокая"},
        "name": {"field": "subject", "example": first_value(("subject",)), "confidence": "высокая"},
        "price": {"field": "price", "example": first_value(("price",)), "confidence": "высокая"},
        "customer": {"field": "organizerInfo.name", "example": first_value(("organizerInfo", "name")), "confidence": "высокая"},
        "delivery_place": {"field": "deliveryInfos[].deliveryAddress.formattedFullInfo", "example": delivery_address.get("formattedFullInfo") if delivery_address else None, "confidence": "высокая"},
        "region": {"field": "deliveryInfos[].deliveryAddress.regionName", "example": delivery_address.get("regionName") if delivery_address else None, "confidence": "высокая"},
        "law_44_fz": {"field": "не определено", "example": None, "confidence": "не определено"},
        "description": {"field": "lotItems[].description", "example": ((first.get("lotItems") or [{}])[0].get("description") if first.get("lotItems") else None), "confidence": "высокая"},
        "publication_date": {"field": "publishDate", "example": first.get("publishDate"), "confidence": "высокая"},
        "application_end": {"field": "applicationFillingEndDate", "example": first.get("applicationFillingEndDate"), "confidence": "высокая"},
        "status": {"field": "lotState", "example": first.get("lotState"), "confidence": "средняя: значение является кодом"},
        "items_count": {"field": "lotItems (длина массива)", "example": len(first.get("lotItems") or []), "confidence": "высокая"},
    }


def run_pagination_test(limit: int = 100, pause_seconds: float = 0.8) -> int:
    captured: dict[str, Any] = {}
    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page

        def capture(response: Response) -> None:
            if ENDPOINT_PART not in response.url or response.status != 200:
                return
            try:
                payload = response.json()
            except Exception:
                return
            if not _largest_purchase_batch(payload):
                return
            request: Request = response.request
            captured.update({
                "url": response.url,
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": request.post_data_json if request.post_data else None,
                "payload": payload,
            })

        page.on("response", capture)
        try:
            problem = _auth_problem(page)
            if problem:
                print(problem)
                return 1
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(12_000)
            if not captured:
                print("Запрос list-published-trade-lots не найден.")
                return 1

            first_payload = captured["payload"]
            first_batch = _largest_purchase_batch(first_payload)
            post_data = captured["post_data"]
            if not isinstance(post_data, dict):
                print("Пагинация не определена: запрос не содержит JSON-объект.")
                return 1

            page_fields = _find_numeric_fields(post_data, PAGE_KEYS)
            size_fields = _find_numeric_fields(post_data, SIZE_KEYS)
            offset_fields = _find_numeric_fields(post_data, OFFSET_KEYS)
            if not size_fields or not (page_fields or offset_fields):
                print(f"Пагинация не определена. JSON запроса: {json.dumps(post_data, ensure_ascii=False)}")
                return 1

            page_size = size_fields[0][1]
            total_field, total = _total_from(first_payload, len(first_batch))
            raw_items: list[dict[str, Any]] = []
            seen: set[Any] = set()
            pages_read = 0
            received_items = 0

            headers = {
                key: value for key, value in captured["headers"].items()
                if key.lower() not in {"host", "content-length", "cookie"}
            }
            current_payload = first_payload
            while len(raw_items) < limit:
                batch = _largest_purchase_batch(current_payload)
                pages_read += 1
                if not batch:
                    break
                received_items += len(batch)
                for item in batch:
                    item_id = item.get("id") or item.get("tradeNumber")
                    if not item_id or not item.get("subject"):
                        continue
                    if item_id not in seen:
                        seen.add(item_id)
                        raw_items.append(_safe_raw(item))
                    if len(raw_items) >= limit:
                        break
                if len(raw_items) >= limit or (total is not None and len(seen) >= total):
                    break

                next_body = copy.deepcopy(post_data)
                if page_fields:
                    path, initial = page_fields[0]
                    _set_path(next_body, path, initial + pages_read)
                else:
                    path, initial = offset_fields[0]
                    _set_path(next_body, path, initial + pages_read * page_size)
                time.sleep(pause_seconds)
                api_response = context.request.fetch(
                    captured["url"], method=captured["method"], headers=headers, data=next_body
                )
                if not api_response.ok:
                    print(f"Страница {pages_read + 1}: HTTP {api_response.status}")
                    break
                current_payload = api_response.json()

            output = [{"normalized": _normalized(item), "raw": item} for item in raw_items]
            _save(OUTPUT_PATH, output)
            report = {
                "purchases_analyzed": len(raw_items),
                "all_fields": _fields_report(raw_items),
                "identified_mappings": _mapping_examples(raw_items),
                "pagination": {
                    "page_field": ".".join(map(str, page_fields[0][0])) if page_fields else None,
                    "initial_page": page_fields[0][1] if page_fields else None,
                    "offset_field": ".".join(map(str, offset_fields[0][0])) if offset_fields else None,
                    "page_size_field": ".".join(map(str, size_fields[0][0])),
                    "page_size": page_size,
                    "total_field": total_field,
                    "total": total,
                    "pages_read": pages_read,
                    "duplicates_removed": received_items - len(raw_items),
                },
            }
            _save(FIELDS_PATH, report)
            print(f"Получено закупок: {len(raw_items)}")
            print(f"Прочитано страниц: {pages_read}")
            print(f"Пагинация: page={report['pagination']['page_field']}, size={report['pagination']['page_size_field']} ({page_size})")
            print(f"Всего по данным API: {total if total is not None else 'не сообщено'}")
            return 0 if raw_items else 1
        finally:
            session.close()
