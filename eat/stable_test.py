"""Контроль пагинации ЕАТ и поиск признака федерального закона."""

from __future__ import annotations

import copy
import json
import re
import time
from collections import Counter
from typing import Any

from playwright.sync_api import Request, Response, sync_playwright

from eat.browser_session import PROJECT_ROOT, START_URL, _auth_problem, _sanitize, _save
from eat.browser_policy import open_authorized_eat_browser
from eat.pagination_test import (
    ENDPOINT_PART,
    PAGE_KEYS,
    SIZE_KEYS,
    _find_numeric_fields,
    _largest_purchase_batch,
    _normalized,
    _set_path,
    _total_from,
)


OUTPUT_PATH = PROJECT_ROOT / "data" / "eat_100_stable_test.json"
REPORT_PATH = PROJECT_ROOT / "data" / "eat_pagination_report.json"
LAW_PATTERN = re.compile(r"44\s*[-‐‑–—]?\s*фз|223\s*[-‐‑–—]?\s*фз|federal.?law|purchase.?law|law", re.I)


def _numbers(batch: list[dict[str, Any]], edge: str) -> list[Any]:
    values = [item.get("tradeNumber") or item.get("externalTradeNumber") for item in batch]
    return values[:3] if edge == "first" else values[-3:]


def _page_row(requested_page: int, batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requested_page": requested_page,
        "returned_count": len(batch),
        "first_3_trade_numbers": _numbers(batch, "first"),
        "last_3_trade_numbers": _numbers(batch, "last"),
        "first_publish_date": batch[0].get("publishDate") if batch else None,
        "last_publish_date": batch[-1].get("publishDate") if batch else None,
        "first_3_ids": [item.get("id") for item in batch[:3]],
        "last_3_ids": [item.get("id") for item in batch[-3:]],
    }


def _walk_law_candidates(value: Any, path: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if LAW_PATTERN.search(str(key)) or (
                isinstance(item, str) and LAW_PATTERN.search(item)
            ):
                found.append({"field": child, "value": _sanitize(item)})
            found.extend(_walk_law_candidates(item, child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_law_candidates(item, f"{path}[]"))
    return found


def run_stable_test(limit: int = 100, pause_seconds: float = 0.8) -> int:
    captured: dict[str, Any] = {}
    card_payloads: list[tuple[str, Any]] = []
    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page

        def capture(response: Response) -> None:
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
                    "payload": payload,
                })
            elif "tender" in response.url.lower() or "tradelot" in response.url.lower():
                card_payloads.append((response.url.split("?", 1)[0], payload))

        page.on("response", capture)
        try:
            problem = _auth_problem(page)
            if problem:
                print(problem)
                return 1
            page.reload(wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(10_000)
            body = captured.get("post_data")
            if not isinstance(body, dict):
                print("Исходный POST list-published-trade-lots не перехвачен.")
                return 1
            page_fields = _find_numeric_fields(body, PAGE_KEYS)
            size_fields = _find_numeric_fields(body, SIZE_KEYS)
            if len(page_fields) != 1 or len(size_fields) != 1:
                print("Не найдено ровно по одному параметру page и size.")
                return 1
            page_path, captured_page = page_fields[0]
            size_path, page_size = size_fields[0]
            headers = {
                key: value for key, value in captured["headers"].items()
                if key.lower() not in {"host", "content-length", "cookie"}
            }

            def fetch_page(requested_page: int) -> tuple[Any, list[dict[str, Any]]]:
                request_body = copy.deepcopy(body)
                _set_path(request_body, page_path, requested_page)
                response = context.request.fetch(
                    captured["url"], method=captured["method"], headers=headers, data=request_body
                )
                if not response.ok:
                    raise RuntimeError(f"page={requested_page}: HTTP {response.status}")
                payload = response.json()
                return payload, _largest_purchase_batch(payload)

            probes: dict[int, tuple[Any, list[dict[str, Any]]]] = {}
            for probe in (0, 1, 2):
                probes[probe] = fetch_page(probe)
                time.sleep(pause_seconds)
            ids0 = [item.get("id") for item in probes[0][1]]
            ids1 = [item.get("id") for item in probes[1][1]]
            ids2 = [item.get("id") for item in probes[2][1]]
            if ids0 and ids0 == ids1 and ids1 != ids2:
                base_page = 1
                numbering_evidence = "page=0 совпал с page=1; page=2 вернул следующий диапазон"
            elif ids0 and ids0 != ids1 and ids1 != ids2:
                base_page = 0
                numbering_evidence = "page=0, page=1 и page=2 вернули разные диапазоны"
            elif not ids0 and ids1:
                base_page = 1
                numbering_evidence = "page=0 пуст, page=1 содержит данные"
            else:
                base_page = captured_page
                numbering_evidence = "нумерация не доказана однозначно"

            rows: list[dict[str, Any]] = []
            raw_items: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            seen_numbers: set[str] = set()
            raw_count = 0
            duplicate_ids = 0
            repeated_full_pages = 0
            previous_ids: list[Any] | None = None
            total = None
            total_field = None
            requested_page = base_page
            while len(seen_numbers) < limit and len(rows) < 40:
                payload, batch = probes.get(requested_page) or fetch_page(requested_page)
                row = _page_row(requested_page, batch)
                rows.append(row)
                current_ids = [item.get("id") for item in batch]
                if previous_ids == current_ids and current_ids:
                    repeated_full_pages += 1
                previous_ids = current_ids
                if total is None:
                    total_field, total = _total_from(payload, len(batch))
                if not batch:
                    break
                raw_count += len(batch)
                for item in batch:
                    item_id = item.get("id")
                    number = item.get("tradeNumber")
                    if item_id in seen_ids:
                        duplicate_ids += 1
                        continue
                    if not item_id:
                        continue
                    seen_ids.add(item_id)
                    if number and number not in seen_numbers:
                        seen_numbers.add(number)
                        raw_items.append(item)
                    elif number in seen_numbers:
                        duplicate_ids += 1
                requested_page += 1
                time.sleep(pause_seconds)

            raw_items = raw_items[:limit]

            # Открываем 3 карточки только для чтения и наблюдаем их JSON.
            for item in raw_items[:3]:
                page.goto(
                    f"https://agregatoreat.ru/purchases/announcement/{item['id']}/info",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                page.wait_for_timeout(4_000)
            law_candidates: list[dict[str, Any]] = []
            for source_url, payload in card_payloads:
                for candidate in _walk_law_candidates(payload):
                    candidate["source"] = source_url
                    if candidate not in law_candidates:
                        law_candidates.append(candidate)

            boundary_repeats = []
            for previous, current in zip(rows, rows[1:]):
                previous_last = previous["last_3_ids"][-1] if previous["last_3_ids"] else None
                current_first = current["first_3_ids"][0] if current["first_3_ids"] else None
                if previous_last and previous_last == current_first:
                    boundary_repeats.append({
                        "previous_page": previous["requested_page"],
                        "next_page": current["requested_page"],
                        "repeated_id": previous_last,
                    })
            purchase_type_titles = [
                candidate
                for candidate in law_candidates
                if candidate["field"].endswith("purchaseTypeTitle")
            ]

            result = [{"normalized": _normalized(item), "raw": item} for item in raw_items]
            report = {
                "endpoint": captured["url"].split("?", 1)[0],
                "captured_request": {
                    "method": captured["method"],
                    "page_path": ".".join(map(str, page_path)),
                    "captured_page": captured_page,
                    "size_path": ".".join(map(str, size_path)),
                    "size": page_size,
                    "sort": body.get("sort"),
                    "other_request_field_names": sorted(body.keys()),
                },
                "numbering": {"base_page": base_page, "evidence": numbering_evidence},
                "page_diagnostics": rows,
                "total_field": total_field,
                "total": total,
                "pages_read": len(rows),
                "raw_records": raw_count,
                "unique_ids_seen": len(seen_ids),
                "unique_trade_numbers_saved": len(raw_items),
                "duplicate_ids": duplicate_ids,
                "repeated_full_pages": repeated_full_pages,
                "pagination_analysis": {
                    "page_zero_behavior": numbering_evidence,
                    "boundary_repeats": boundary_repeats,
                    "cause": (
                        "Живая лента изменилась между offset-страницами при сортировке "
                        "только по publishDate; отдельные записи сдвинулись через границу страниц."
                    ),
                    "previous_report_problem": (
                        "Старый счётчик ошибочно считал исключённые записи дублями."
                    ),
                },
                "law_search": {
                    "cards_opened": min(3, len(raw_items)),
                    "candidate_fields": law_candidates,
                    "conclusion": (
                        "В API карточки найден прямой текст закона в trade.purchaseTypeTitle."
                        if purchase_type_titles
                        else "Прямой признак закона не найден."
                    ),
                    "proven_field": "trade.purchaseTypeTitle" if purchase_type_titles else None,
                    "example": purchase_type_titles[0]["value"] if purchase_type_titles else None,
                    "confidence": "высокая" if purchase_type_titles else "не определено",
                },
            }
            _save(OUTPUT_PATH, result)
            _save(REPORT_PATH, report)
            print(f"Страниц: {len(rows)}, сырых записей: {raw_count}, уникальных tradeNumber: {len(raw_items)}, дублей ID: {duplicate_ids}")
            return 0 if len(raw_items) >= limit else 1
        finally:
            session.close()
