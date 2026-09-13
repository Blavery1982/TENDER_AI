"""Полный production-сбор актуальной выдачи ЕАТ без тестовых лимитов.

Модуль читает только список закупок. Карточки, документы, поиск моделей,
поставщиков и Google Sheets здесь намеренно не вызываются.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from playwright.sync_api import Request, Response, sync_playwright

from eat.browser_auth import PURCHASES_URL
from eat.browser_policy import open_authorized_eat_browser
from eat.browser_session import PROJECT_ROOT, _find_purchase_lists, _sanitize
from eat.filter_pipeline import PURCHASE_TYPES_URL, _purchase_type_map
from eat.pagination_test import (
    ENDPOINT_PART,
    PAGE_KEYS,
    SIZE_KEYS,
    _find_numeric_fields,
    _largest_purchase_batch,
    _normalized_key,
    _set_path,
)
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2
from pipeline.batch_orchestrator import procurement_queue
from reports.confidential_links import _parse_eat_datetime


COLLECTION_PATH = PROJECT_ROOT / "data" / "eat_live_full_collection.json"
AUDIT_PATH = PROJECT_ROOT / "data" / "eat_live_collection_audit.json"
# Endpoint принимает размер страницы из JSON-запроса. 100 заметно сокращает
# число round-trip: полная выдача 10 000 записей читается примерно за 100 POST.
DEFAULT_PAGE_SIZE = 100

TOTAL_KEYS = {"totalcount", "total", "totalitems", "itemscount", "recordscount"}
LAST_KEYS = {"islast", "islastpage", "lastpage"}
HAS_NEXT_KEYS = {"hasnext", "hasnextpage"}
TOTAL_PAGES_KEYS = {"totalpages", "pagecount", "pagescount"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _walk_named(value: Any, names: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if _normalized_key(str(key)) in names:
                found.append(child)
            found.extend(_walk_named(child, names))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_named(child, names))
    return found


def _total_count(payload: Any) -> int | None:
    # ``total`` встречается и у денежных значений. Сначала берём точный
    # контрактный totalCount, остальные имена являются лишь fallback.
    exact = [value for value in _walk_named(payload, {"totalcount"})
             if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    if exact:
        return max(exact)
    values = [value for value in _walk_named(payload, TOTAL_KEYS - {"totalcount", "total"})
              if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    return max(values) if values else None


def _purchase_batch(payload: Any) -> list[dict[str, Any]]:
    """Не терять страницы, на которых много confidential_locked объектов."""
    batches = _find_purchase_lists(payload)
    candidates = [
        batch for batch in batches
        if sum(bool(item.get("id")) for item in batch) >= max(1, len(batch) // 2)
    ]
    return max(candidates, key=len) if candidates else []


def _explicit_last_page(payload: Any, requested_page: int) -> tuple[bool, str | None]:
    if any(value is True for value in _walk_named(payload, LAST_KEYS)):
        return True, "api_is_last_page"
    if any(value is False for value in _walk_named(payload, HAS_NEXT_KEYS)):
        return True, "api_has_next_false"
    page_counts = [value for value in _walk_named(payload, TOTAL_PAGES_KEYS)
                   if isinstance(value, int) and not isinstance(value, bool) and value >= 0]
    if page_counts and requested_page >= max(page_counts):
        return True, "api_total_pages_reached"
    return False, None


def _record_key(item: dict[str, Any]) -> str:
    if item.get("id"):
        return f"id:{item['id']}"
    if item.get("tradeNumber"):
        return f"trade:{item['tradeNumber']}"
    encoded = json.dumps(_sanitize(item), ensure_ascii=False, sort_keys=True, default=str)
    return "raw:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _page_signature(batch: list[dict[str, Any]]) -> str:
    keys = [_record_key(item) for item in batch]
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def collect_all_pages(
    fetch_page: Callable[[int, int], Any],
    *,
    endpoint: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    pause_seconds: float = 0.25,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Читать страницы до доказанного конца, без ограничения их количества."""
    page = 1
    raw_records: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    page_signatures: dict[str, int] = {}
    diagnostics: list[dict[str, Any]] = []
    observed_totals: list[int] = []
    stop_reason: str | None = None
    end_proven = False
    diagnostic_error: str | None = None

    while True:
        payload = fetch_page(page, page_size)
        batch = _purchase_batch(payload)
        reported_total = _total_count(payload)
        if reported_total is not None:
            observed_totals.append(reported_total)
        target_total = max(observed_totals) if observed_totals else None
        explicit_last, explicit_reason = _explicit_last_page(payload, page)

        previous_unique = len(unique)
        raw_records.extend(batch)
        for item in batch:
            unique.setdefault(_record_key(item), item)
        new_unique = len(unique) - previous_unique

        row = {
            "endpoint": endpoint,
            "page": page,
            "size": page_size,
            "returned_count": len(batch),
            "new_unique_count": new_unique,
            "totalCount": reported_total,
            "cumulative_raw_count": len(raw_records),
            "cumulative_unique_count": len(unique),
            "timestamp": _utc_now(),
            "stop_reason": None,
        }

        if batch:
            signature = _page_signature(batch)
            repeated_from = page_signatures.get(signature)
            if repeated_from is not None:
                diagnostic_error = f"repeated_page: page {page} repeats page {repeated_from}"
                stop_reason = "pagination_not_advancing_repeated_page"
            elif new_unique == 0:
                diagnostic_error = f"no_new_unique_ids_on_page_{page}"
                stop_reason = "pagination_not_advancing_no_new_ids"
            else:
                page_signatures[signature] = page

        if stop_reason is None and target_total is not None:
            if len(raw_records) == target_total:
                stop_reason = "total_count_reached"
                end_proven = True
            elif len(raw_records) > target_total:
                diagnostic_error = (
                    f"cumulative_raw_count_{len(raw_records)}_exceeds_totalCount_{target_total}"
                )
                stop_reason = "total_count_inconsistent"

        if stop_reason is None and explicit_last:
            stop_reason = explicit_reason
            end_proven = True

        if stop_reason is None and not batch:
            if target_total is not None:
                diagnostic_error = (
                    f"empty_page_{page}_before_totalCount: "
                    f"raw={len(raw_records)}, totalCount={target_total}"
                )
                stop_reason = "empty_page_before_total_count"
            else:
                diagnostic_error = f"empty_page_{page}_without_terminal_metadata"
                stop_reason = "empty_page_without_proven_end"

        row["stop_reason"] = stop_reason
        diagnostics.append(row)
        snapshot = {
            "status": "completed" if end_proven else "diagnostic_error" if stop_reason else "running",
            "endpoint": endpoint,
            "page_size": page_size,
            "totalCount": max(observed_totals) if observed_totals else None,
            "pages_read": sum(item["returned_count"] > 0 for item in diagnostics),
            "requests_made": len(diagnostics),
            "raw_count": len(raw_records),
            "unique_count": len(unique),
            "duplicate_count": len(raw_records) - len(unique),
            "end_proven": end_proven,
            "stop_reason": stop_reason,
            "diagnostic_error": diagnostic_error,
            "page_diagnostics": diagnostics,
        }
        if progress:
            progress(snapshot)
        if stop_reason:
            return {**snapshot, "raw_records": raw_records,
                    "unique_records": list(unique.values()),
                    "observed_total_counts": observed_totals}
        page += 1
        if pause_seconds:
            time.sleep(pause_seconds)


def _deadline_status(raw: dict[str, Any], now: datetime) -> str:
    try:
        deadline = _parse_eat_datetime(raw.get("applicationFillingEndDate"))
    except (TypeError, ValueError):
        deadline = None
    if deadline is None:
        return "unknown"
    return "expired" if deadline <= now else "active"


def _load_already_processed_ids() -> set[str]:
    """Только локальные receipts/payload production-записей; Sheets не читается."""
    found: set[str] = set()
    payload_path = PROJECT_ROOT / "data" / "live_e2e_production_payload.json"
    receipt_path = PROJECT_ROOT / "data" / "live_e2e_google_sheets_receipt.json"
    try:
        value = json.loads(payload_path.read_text(encoding="utf-8"))
        identifier = (value.get("procurement") or {}).get("id")
        if identifier:
            found.add(str(identifier))
    except (OSError, ValueError, TypeError):
        pass
    try:
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        key = value.get("key") or []
        if key:
            found.add(str(key[0]))
    except (OSError, ValueError, TypeError):
        pass
    return found


def audit_unique_records(
    records: list[dict[str, Any]],
    purchase_types: dict[str, str],
    *,
    already_processed_ids: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Фильтр применяется ко всем unique до deadline/processed маршрутизации."""
    config = load_config()
    processed_ids = already_processed_ids or set()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    counters: Counter[str] = Counter()
    audited: list[dict[str, Any]] = []

    for raw in records:
        identifier = str(raw.get("id") or "")
        title = (purchase_types.get(str(raw.get("purchaseTypeId")))
                 or raw.get("purchaseTypeTitle"))
        try:
            decision = filter_purchase_v2(raw, title, config)
            counters["filter_applied"] += 1
        except Exception as exc:
            counters["filter_errors"] += 1
            audited.append({"id": identifier or None, "filter_result": "filter_error",
                            "error": f"{type(exc).__name__}: {exc}", "raw": _sanitize(raw)})
            continue

        result = decision.get("filter_result") or "unknown"
        counters[result] += 1
        deadline = _deadline_status(raw, current)
        counters[f"deadline_{deadline}"] += 1
        processed = bool(identifier and identifier in processed_ids)
        if processed:
            counters["already_processed"] += 1

        routing = None
        if result == "passed" and deadline == "active" and not processed:
            routing = procurement_queue({"purchase_id": identifier, "raw": raw,
                                         "purchase_type_title": title})
            counters["priority_1" if routing["priority"] == 1 else "priority_2"] += 1

        audited.append({
            "id": identifier or None,
            "tradeNumber": raw.get("tradeNumber"),
            "filter_result": result,
            "deadline_status": deadline,
            "already_processed": processed,
            "priority": routing.get("priority") if routing else None,
            "queue": routing.get("queue") if routing else None,
            "filter": _sanitize(decision),
            "raw": _sanitize(raw),
        })

    counts = {
        "unique": len(records),
        "filter_applied": counters["filter_applied"],
        "filter_errors": counters["filter_errors"],
        "expired": counters["deadline_expired"],
        "deadline_unknown": counters["deadline_unknown"],
        "already_processed": counters["already_processed"],
        "passed_filter_v2": counters["passed"],
        "rejected_filter_v2": counters["rejected"],
        "manual_check": counters["manual_check"],
        "confidential": counters["confidential_locked"],
        "priority_1": counters["priority_1"],
        "priority_2": counters["priority_2"],
    }
    return {"counts": counts, "purchases": audited}


def _request_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items()
            if key.casefold() not in {"host", "content-length", "cookie"}}


def run_live_collection_audit(*, pause_seconds: float = 0.25) -> dict[str, Any]:
    """Production-команда: вся лента → dedup → filter v2 → deadline → priority."""
    started = time.monotonic()
    started_at = _utc_now()
    captured: dict[str, Any] = {}
    config = load_config()
    purchase_types = dict(config["proven_purchase_type_titles"])

    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page

        def capture(response: Response) -> None:
            host = urlsplit(response.url).hostname
            if host not in {"tender-api.agregatoreat.ru", "tender-cache-api.agregatoreat.ru"}:
                return
            if response.status != 200 or "json" not in response.headers.get("content-type", "").casefold():
                return
            try:
                payload = response.json()
            except Exception:
                return
            if ENDPOINT_PART in response.url and _largest_purchase_batch(payload):
                request: Request = response.request
                captured.update({"url": response.url, "method": request.method,
                                 "headers": dict(request.headers),
                                 "post_data": request.post_data_json if request.post_data else None})
            if "filter-purchase-types" in response.url:
                purchase_types.update(_purchase_type_map(payload))

        page.on("response", capture)
        try:
            page.goto(PURCHASES_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(12_000)
            body = captured.get("post_data")
            if not isinstance(body, dict):
                raise RuntimeError("Не удалось перехватить production POST списка закупок")
            page_fields = _find_numeric_fields(body, PAGE_KEYS)
            size_fields = _find_numeric_fields(body, SIZE_KEYS)
            if len(page_fields) != 1 or len(size_fields) != 1:
                raise RuntimeError("Не удалось однозначно определить page/size production-запроса")
            page_path = page_fields[0][0]
            size_path = size_fields[0][0]
            headers = _request_headers(captured["headers"])
            try:
                response = context.request.get(PURCHASE_TYPES_URL, headers=headers)
                if response.ok:
                    purchase_types.update(_purchase_type_map(response.json()))
            except Exception:
                pass

            def fetch_page(requested_page: int, page_size: int) -> Any:
                request_body = copy.deepcopy(body)
                _set_path(request_body, page_path, requested_page)
                _set_path(request_body, size_path, page_size)
                response = context.request.fetch(captured["url"], method=captured["method"],
                                                 headers=headers, data=request_body)
                if not response.ok:
                    raise RuntimeError(f"page={requested_page}: HTTP {response.status}")
                if "json" not in response.headers.get("content-type", "").casefold():
                    raise RuntimeError(f"page={requested_page}: ответ не JSON")
                return response.json()

            def save_progress(snapshot: dict[str, Any]) -> None:
                _atomic_json(AUDIT_PATH, {
                    "run": {"started_at": started_at, "status": snapshot["status"]},
                    "pagination": snapshot,
                    "filter_audit": None,
                })

            pagination = collect_all_pages(fetch_page, endpoint=captured["url"],
                                           page_size=DEFAULT_PAGE_SIZE,
                                           pause_seconds=pause_seconds,
                                           progress=save_progress)
            if not pagination["end_proven"]:
                _atomic_json(AUDIT_PATH, {
                    "run": {"started_at": started_at, "finished_at": _utc_now(),
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "status": "diagnostic_error"},
                    "pagination": {key: value for key, value in pagination.items()
                                   if key not in {"raw_records", "unique_records"}},
                    "filter_audit": None,
                })
                raise RuntimeError(pagination["diagnostic_error"] or "Конец выдачи не доказан")

            audit = audit_unique_records(
                pagination["unique_records"], purchase_types,
                already_processed_ids=_load_already_processed_ids(),
            )
            elapsed = round(time.monotonic() - started, 3)
            pagination_report = {key: value for key, value in pagination.items()
                                 if key not in {"raw_records", "unique_records"}}
            report = {
                "run": {"started_at": started_at, "finished_at": _utc_now(),
                        "elapsed_seconds": elapsed, "status": "completed",
                        "scope": "live_collection_and_filter_audit_only",
                        "documents_downloaded": False, "price_search_performed": False,
                        "google_sheets_written": False},
                "pagination": pagination_report,
                "filter_audit": audit["counts"],
            }
            _atomic_json(AUDIT_PATH, report)
            _atomic_json(COLLECTION_PATH, {
                "metadata": report,
                "purchases": audit["purchases"],
            })
            return report
        finally:
            session.close()


if __name__ == "__main__":
    result = run_live_collection_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
