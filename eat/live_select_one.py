"""Выбор одной свежей закупки ЕАТ и загрузка карточки в одной сессии."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Request, Response, sync_playwright

from eat.browser_auth import PURCHASES_URL
from eat.browser_policy import open_authorized_eat_browser
from eat.card_payload import card_purchase_payloads, fullest_card_purchase
from eat.browser_session import PROJECT_ROOT, START_URL, _sanitize, _save
from eat.contract_analysis import _documents, _file_url, _safe_name
from eat.filter_pipeline import PURCHASE_TYPES_URL, _purchase_type_map
from eat.pagination_test import (ENDPOINT_PART, PAGE_KEYS, SIZE_KEYS,
                                 _find_numeric_fields, _largest_purchase_batch,
                                 _set_path)
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2
from documents.item_sources import resolve_item_sources
from model_search.price_readiness import (PRICE_SEARCH_READY,
                                          classify_price_search_readiness)

OUTPUT = PROJECT_ROOT / "data/live_e2e_selected.json"
REPORT = PROJECT_ROOT / "data/live_e2e_selection_report.json"
SUPPORTED = {".pdf", ".docx", ".doc", ".xlsx"}


def _candidate_models(raw: dict[str, Any]) -> list[str]:
    """Модели, допущенные единым production-правилом к первичному поиску цены."""
    result: list[str] = []
    items = raw.get("lotItems") or []
    for number, item in enumerate(items, 1):
        resolved = resolve_item_sources(item, item_number=number, items=items)
        readiness = classify_price_search_readiness(item, resolved)
        value = readiness.get("identifier")
        if readiness["classification"] == PRICE_SEARCH_READY and value and value not in result:
            result.append(value)
    return result


def _deadline_active(raw: dict[str, Any]) -> bool:
    value = raw.get("applicationFillingEndDate")
    try:
        deadline = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        now = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.now()
        return deadline > now
    except (TypeError, ValueError):
        return False


def _score(raw: dict[str, Any]) -> tuple[int, int, int]:
    items = raw.get("lotItems") or []
    models = _candidate_models(raw)
    return (3 if len(items) == 1 and len(models) == 1 else
            2 if len(items) == 1 else 1 if models else 0,
            -len(items), -len(str(raw.get("subject") or "")))


def _request_headers(headers: dict[str, str]) -> dict[str, str]:
    # Cookie передаётся самим browser context. Bearer/CSRF живут только в памяти.
    return {key: value for key, value in headers.items()
            if key.casefold() not in {"host", "content-length", "cookie"}}


def run(*, max_pages: int = 60, pause_seconds: float = .5) -> int:
    config = load_config()
    captured: dict[str, Any] = {}
    purchase_types = dict(config["proven_purchase_type_titles"])
    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        context, page = session.context, session.page

        def capture_list(response: Response) -> None:
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

        page.on("response", capture_list)
        try:
            page.goto(PURCHASES_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(12_000)
            body = captured.get("post_data")
            if not isinstance(body, dict):
                raise RuntimeError("Не удалось перехватить запрос списка закупок")
            pages = _find_numeric_fields(body, PAGE_KEYS)
            sizes = _find_numeric_fields(body, SIZE_KEYS)
            if len(pages) != 1 or len(sizes) != 1:
                raise RuntimeError("Не удалось однозначно определить page/size")
            page_path, size_path = pages[0][0], sizes[0][0]
            headers = _request_headers(captured["headers"])
            # Справочник типов необязателен: доказанные 44-ФЗ значения уже есть в config.
            try:
                types_response = context.request.get(PURCHASE_TYPES_URL, headers=headers)
                if types_response.ok:
                    purchase_types.update(_purchase_type_map(types_response.json()))
            except Exception:
                pass

            checked = 0
            passed: list[dict[str, Any]] = []
            seen: set[str] = set()
            page_summaries: list[dict[str, Any]] = []
            for requested_page in range(1, max_pages + 1):
                request_body = copy.deepcopy(body)
                _set_path(request_body, page_path, requested_page)
                _set_path(request_body, size_path, 10)
                response = context.request.fetch(captured["url"], method=captured["method"],
                                                 headers=headers, data=request_body)
                if not response.ok:
                    raise RuntimeError(f"Страница {requested_page}: HTTP {response.status}")
                batch = _largest_purchase_batch(response.json())
                if not batch:
                    break
                page_passed = 0
                for raw in batch:
                    purchase_id = str(raw.get("id") or "")
                    if not purchase_id or purchase_id in seen:
                        continue
                    seen.add(purchase_id); checked += 1
                    title = purchase_types.get(str(raw.get("purchaseTypeId")))
                    decision = filter_purchase_v2(raw, title, config)
                    if decision["filter_result"] == "passed" and _deadline_active(raw):
                        passed.append({"raw": raw, "filter": decision,
                                       "purchase_type_title": title,
                                       "candidate_models": _candidate_models(raw),
                                       "page": requested_page})
                        page_passed += 1
                page_summaries.append({"page": requested_page, "returned": len(batch),
                                       "new_unique": checked - sum(x["new_unique"] for x in page_summaries),
                                       "passed": page_passed})
                exact = [item for item in passed if _score(item["raw"])[0] == 3]
                if exact:
                    break
                if len(passed) >= 12:
                    break
                time.sleep(pause_seconds)
            if not passed:
                _save(REPORT, {"pages_read": len(page_summaries), "unique_checked": checked,
                               "page_summaries": page_summaries, "result": "no_passed"})
                raise RuntimeError("В просмотренных свежих страницах нет закупки, прошедшей filter_purchase_v2")
            selected = max(passed, key=lambda item: _score(item["raw"]))
            raw = selected["raw"]
            purchase_id = str(raw["id"])
            card_url = f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"

            payloads: list[dict[str, Any]] = []
            docs: list[dict[str, Any]] = []
            def capture_card(response: Response) -> None:
                if "json" not in response.headers.get("content-type", "").casefold():
                    return
                try:
                    value = response.json()
                    payloads.extend(card_purchase_payloads(value, purchase_id))
                    docs.extend(_documents(value, response.url))
                except Exception:
                    pass
            page.on("response", capture_card)
            page.goto(card_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(10_000)
            for anchor in page.locator("a[href]").all():
                try:
                    href = anchor.get_attribute("href")
                    label = anchor.inner_text(timeout=500).strip()
                    if href and Path(urlsplit(href).path).suffix.casefold() in SUPPORTED:
                        docs.append({"file_name": label or Path(urlsplit(href).path).name,
                                     "document_type": None, "file_id": None,
                                     "download_url": urljoin(page.url, href)})
                except Exception:
                    pass
            unique_docs = {str(doc.get("file_id") or doc.get("download_url") or doc.get("file_name")): doc
                           for doc in docs}
            folder = PROJECT_ROOT / "data" / "contracts" / str(raw.get("tradeNumber") or purchase_id)
            folder.mkdir(parents=True, exist_ok=True)
            files: list[dict[str, Any]] = []
            for document in unique_docs.values():
                if not document.get("download_url") and document.get("file_id") and document.get("document_type") is not None:
                    document["download_url"] = _file_url(purchase_id, document["document_type"], document["file_id"])
                url = document.get("download_url")
                if not url:
                    files.append({**document, "local_path": None, "download_status": "no_url"})
                    continue
                response = context.request.get(url)
                if not response.ok:
                    files.append({**document, "local_path": None,
                                  "download_status": f"http_{response.status}"})
                    continue
                name = _safe_name(document.get("file_name") or Path(urlsplit(url).path).name)
                path = folder / name
                content = response.body()
                if not path.exists() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(content).digest():
                    path.write_bytes(content)
                files.append({**document, "local_path": str(path), "download_status": "downloaded"})
            fullest = fullest_card_purchase(payloads)
            # Карточка может вернуть расширенный объект. Не теряем поля списка/lotItems.
            card_raw = {**raw, **fullest}
            fixture = {"purchase_id": purchase_id, "card_url": card_url,
                       "purchase_type_title": selected["purchase_type_title"],
                       "filter": selected["filter"], "raw": _sanitize(card_raw),
                       "documents": _sanitize(files),
                       "selection": {"pages_read": len(page_summaries),
                                     "unique_checked": checked,
                                     "candidate_models": selected["candidate_models"]}}
            _save(OUTPUT, fixture)
            passed_summaries = [{"id": item["raw"].get("id"),
                                 "tradeNumber": item["raw"].get("tradeNumber"),
                                 "subject": item["raw"].get("subject"),
                                 "price": item["raw"].get("price"),
                                 "applicationFillingEndDate": item["raw"].get("applicationFillingEndDate"),
                                 "items_count": len(item["raw"].get("lotItems") or []),
                                 "candidate_models": item["candidate_models"],
                                 "page": item["page"]} for item in passed]
            _save(REPORT, {"pages_read": len(page_summaries), "unique_checked": checked,
                           "passed_found": len(passed), "page_summaries": page_summaries,
                           "passed_candidates": passed_summaries,
                           "selected_id": purchase_id, "selected_trade_number": raw.get("tradeNumber"),
                           "selected_subject": raw.get("subject"),
                           "candidate_models": selected["candidate_models"],
                           "documents_found": len(files)})
            print(f"Выбрана закупка {raw.get('tradeNumber')}: {raw.get('subject')}", flush=True)
            print(f"Карточка и документы сохранены: {OUTPUT}", flush=True)
            return 0
        finally:
            session.close()


if __name__ == "__main__":
    raise SystemExit(run())
