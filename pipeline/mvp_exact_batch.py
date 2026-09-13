"""Urgent live MVP: EAT window -> exact model -> suppliers -> Google Sheets.

Model discovery is still intentionally skipped. Expensive supplier discovery
is capped at ten exact-model items, while every analyzed position is written
to the production Google Sheet.
"""
from __future__ import annotations

import copy
import csv
import json
import logging
import time
from collections import Counter
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import Request, Response, sync_playwright

from calculator.formulas import platform_commission
from documents.pipeline import audit_from_extraction, process_procurement_documents
from eat.browser_auth import PURCHASES_URL
from eat.browser_policy import open_authorized_eat_browser
from eat.browser_session import PROJECT_ROOT
from eat.filter_pipeline import PURCHASE_TYPES_URL, _purchase_type_map
from eat.live_collection import (DEFAULT_PAGE_SIZE, _purchase_batch,
                                 _request_headers, collect_all_pages)
from eat.pagination_test import (ENDPOINT_PART, PAGE_KEYS, SIZE_KEYS,
                                 _find_numeric_fields, _set_path)
from eat.single_purchase import fetch_purchase_card
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2
from google_sheets.production_upsert import upsert_live_payload
from model_search.live_price_search import search_exact_model_prices
from model_search.price_readiness import (MODEL_DISCOVERY_REQUIRED,
                                          MODEL_IDENTIFIER_REVIEW_REQUIRED,
                                          PRICE_READINESS_VERSION,
                                          PRICE_SEARCH_READY,
                                          classify_price_search_readiness)
from reports.confidential_links import _parse_eat_datetime
from suppliers.economic_precheck import (evaluate_price_request_candidate,
                                         is_explicitly_unavailable)
from suppliers.market_search import (classify_source, price_priority,
                                     supplier_target_price)
from suppliers.verification import HIGH_RISK, INSUFFICIENT, MANUAL, PASSED, verify_supplier
from security.customer_check import extract_customer
from security.traceability import analyze_item

MOSCOW = ZoneInfo("Europe/Moscow")
MAX_SUPPLIER_SEARCHES = 10
CALENDAR_PATH = PROJECT_ROOT / "config" / "russian_work_calendar.json"
CHECKPOINT_PATH = PROJECT_ROOT / "data" / "checkpoints" / "mvp_exact_batch.json"
RESULT_PATH = PROJECT_ROOT / "data" / "mvp_exact_batch_latest.json"
CONFIDENTIAL_PATH = PROJECT_ROOT / "data" / "mvp_confidential_window.json"
REPORT_PATH = PROJECT_ROOT / "reports" / "mvp_exact_batch_latest.md"
CSV_PATH = PROJECT_ROOT / "reports" / "mvp_exact_batch_latest.csv"
LOG_PATH = PROJECT_ROOT / "logs" / "mvp_exact_batch.log"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tender_ai.mvp_exact_batch")
    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _non_working_dates() -> set[date]:
    value = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    return {date.fromisoformat(item) for item in value.get("non_working_dates") or []}


def deadline_window(now: datetime | None = None,
                    non_working: set[date] | None = None) -> tuple[datetime, datetime]:
    """Strict Moscow window ending at the end of the second workday."""
    lower = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    holidays = _non_working_dates() if non_working is None else non_working
    cursor = lower.date()
    workdays = 0
    while workdays < 2:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5 and cursor not in holidays:
            workdays += 1
    upper = datetime.combine(cursor, day_time.max, tzinfo=MOSCOW)
    return lower, upper


def _deadline_in_window(raw: dict[str, Any], lower: datetime, upper: datetime) -> bool:
    try:
        deadline = _parse_eat_datetime(raw.get("applicationFillingEndDate"))
    except (TypeError, ValueError):
        return False
    return bool(deadline and lower < deadline <= upper)


def _replace_named(value: Any, key_name: str, replacement: str) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == key_name.casefold():
                value[key] = replacement
                changed += 1
            else:
                changed += _replace_named(child, key_name, replacement)
    elif isinstance(value, list):
        for child in value:
            changed += _replace_named(child, key_name, replacement)
    return changed


def apply_server_deadline_prefilter(body: dict[str, Any], lower: datetime,
                                    upper: datetime) -> dict[str, Any]:
    """Set the EAT UI date range; exact deadline semantics are checked locally."""
    result = copy.deepcopy(body)
    # EAT expects UTC-naive ISO strings in these UI request fields.
    lower_value = lower.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    upper_value = upper.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    start_count = _replace_named(result, "applicationFillingStartDate", lower_value)
    end_count = _replace_named(result, "applicationFillingEndDate", upper_value)
    if not start_count:
        result["applicationFillingStartDate"] = lower_value
    if not end_count:
        result["applicationFillingEndDate"] = upper_value
    return result


def _lot(card: dict[str, Any]) -> dict[str, Any]:
    raw = card.get("raw") or card
    return raw.get("lot") or raw


def _merge_card_list_raw(card: dict[str, Any], list_raw: dict[str, Any]) -> dict[str, Any]:
    card_raw = card.get("raw") or {}
    merged = dict(list_raw)
    if isinstance(card_raw, dict):
        merged.update(card_raw)
    # Некоторые вложенные API карточки используют нулевой технический UUID и
    # не повторяют номер закупки. Идентичность списка в этом случае приоритетна.
    if not merged.get("id") or merged.get("id") == "00000000-0000-0000-0000-000000000000":
        merged["id"] = list_raw.get("id")
    if not merged.get("tradeNumber"):
        merged["tradeNumber"] = list_raw.get("tradeNumber")
    if not merged.get("subject"):
        merged["subject"] = list_raw.get("subject")
    if not merged.get("applicationFillingEndDate"):
        merged["applicationFillingEndDate"] = list_raw.get("applicationFillingEndDate")
    card["raw"] = merged
    return card


def _classify_model(item: dict[str, Any], raw_item: dict[str, Any] | None = None) -> dict[str, Any]:
    readiness = classify_price_search_readiness(raw_item or {}, item)
    classification = readiness["classification"]
    if classification == PRICE_SEARCH_READY:
        from_price = readiness.get("model_source") == "PRICE_JUSTIFICATION"
        mode = ("PRICE_JUSTIFICATION_MODEL" if from_price else
                item.get("model_search_mode") if item.get("model_search_mode") in
                {"EXACT_MODEL_ONLY", "EXACT_MODEL_OR_EQUIVALENT"} else "EXACT_MODEL_ONLY")
        return {**readiness, "route": "exact",
                "status_ru": "МОДЕЛЬ ИЗ ОБОСНОВАНИЯ ЦЕНЫ" if from_price else "PRICE_SEARCH_READY",
                "model": readiness["identifier"], "mode": mode}
    if classification == MODEL_IDENTIFIER_REVIEW_REQUIRED:
        return {**readiness, "route": "manual_review",
                "status_ru": "MODEL_IDENTIFIER_REVIEW_REQUIRED", "model": readiness.get("identifier"),
                "mode": "MODEL_IDENTIFIER_REVIEW_REQUIRED"}
    return {**readiness, "route": "discovery", "status_ru": "ТРЕБУЕТСЯ ПОДБОР МОДЕЛИ",
            "model": None, "mode": MODEL_DISCOVERY_REQUIRED}


def _source_label(source: str | None) -> str:
    return {"CUSTOMER_SPECIFICATION": "спецификация",
            "EAT_SPECIFICATION": "спецификация",
            "CONTRACT_DOCUMENT": "контракт/ТЗ",
            "PRICE_JUSTIFICATION": "обоснование цены"}.get(str(source), str(source or "не определён"))


def _supplier_row(offer: dict[str, Any], model: str, target: float) -> dict[str, Any]:
    url = offer.get("url") or ""
    domain = (urlsplit(url).hostname or "").removeprefix("www.")
    availability = {"В наличии": "in_stock", "Под заказ": "to_order",
                    "Нет в наличии": "out_of_stock"}.get(offer.get("availability"), "unknown")
    row = {
        "supplier_name": offer.get("seller") or domain,
        "exact_model": offer.get("exact_model_match") is True,
        "exact_model_name": model,
        "product_url": url,
        "public_price": offer.get("price"),
        "purchase_price": None,
        "availability": availability,
        "availability_display": offer.get("availability") or "Не удалось определить",
        "available_quantity": offer.get("available_quantity"),
        "delivery_information": offer.get("delivery_information"),
        "source_type": classify_source(domain),
        "source": offer.get("source") or domain,
        "checked_at": offer.get("checked_at"),
        "product_page_available": True,
        "price_confirmed": offer.get("price") is not None,
    }
    row["price_priority"] = price_priority(row, target)
    row["target_price_difference"] = (round(float(row["public_price"]) - target, 2)
                                              if row["public_price"] is not None else None)
    return row


def _preliminary_economics(item: dict[str, Any], best: dict[str, Any] | None,
                           commission_rate: float) -> dict[str, Any]:
    quantity = item.get("quantity")
    unit_price = item.get("customer_unit_price")
    try:
        quantity_value = float(quantity)
        customer_total = float(item.get("customer_sum") or float(unit_price) * quantity_value)
    except (TypeError, ValueError):
        return {"status": "Недостаточно данных", "calculation_basis":
                "РАСЧЁТ ПО ПУБЛИЧНОЙ ЦЕНЕ — ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ ПОСТАВЩИКА",
                "purchase_price": None}
    commission = platform_commission(customer_total, commission_rate)
    public_total = (round(float(best["public_price"]) * quantity_value, 2)
                    if best and best.get("public_price") is not None else None)
    reserve = (round(customer_total - commission - public_total, 2)
               if public_total is not None else None)
    return {
        "status": "Предварительно рассчитано" if reserve is not None else "Нет публичной цены",
        "calculation_basis": "РАСЧЁТ ПО ПУБЛИЧНОЙ ЦЕНЕ — ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ ПОСТАВЩИКА",
        "customer_position_total": round(customer_total, 2),
        "eat_commission_rate": commission_rate,
        "eat_commission": commission,
        "public_unit_price": best.get("public_price") if best else None,
        "public_total_for_quantity": public_total,
        "preliminary_reserve_before_logistics_and_taxes": reserve,
        "purchase_price": None,
        "logistics_included": False,
    }


def _process_suppliers(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    model = item["model"]["model"]
    trade = item["trade_number"] or item["purchase_id"]
    number = item["item_number"]
    price_path = PROJECT_ROOT / "data" / "mvp_price_search" / f"{trade}_{number}.json"
    prices = search_exact_model_prices(model, output_path=price_path)
    target = supplier_target_price(float(item["customer_unit_price"]))
    rows = [_supplier_row(offer, model, target) for offer in prices.get("offers") or []]
    verified = []
    for row in rows:
        try:
            verified.append(verify_supplier(row))
        except Exception as exc:
            verified.append({**row, "verification_status": INSUFFICIENT,
                             "verification_comment": f"Ошибка проверки: {type(exc).__name__}: {exc}",
                             "risk_flags": [], "purchase_price": None})
    status_rank = {PASSED: 0, MANUAL: 1, INSUFFICIENT: 2, HIGH_RISK: 3}
    verified.sort(key=lambda row: (status_rank.get(row.get("verification_status"), 9),
                                   row.get("public_price") is None,
                                   row.get("public_price") or float("inf")))
    working = [row for row in verified
               if row.get("verification_status") != HIGH_RISK and not is_explicitly_unavailable(row)]
    top = working[:3]
    calls = [evaluate_price_request_candidate(row, target) for row in working]
    calls = [row for row in calls if row.get("call_candidate")]
    best = min((row for row in working if row.get("public_price") is not None),
               key=lambda row: float(row["public_price"]), default=None)
    below = [row for row in working if row.get("public_price") is not None
             and float(row["public_price"]) <= target]
    comment = ("ТОЧНАЯ МОДЕЛЬ НЕ НАЙДЕНА У ПОСТАВЩИКОВ" if not verified else
               "ХОРОШИЙ КАНДИДАТ ДЛЯ ПРОСЧЁТА" if below else
               "ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА" if working and all(
                   row.get("verification_status") in {MANUAL, INSUFFICIENT} for row in working) else
               "ЗАПРОСИТЬ СКИДКУ")
    return {
        "target_discount_percent": 15,
        "target_unit_price": target,
        "price_search_path": str(price_path),
        "candidate_urls_found": (prices.get("candidate_discovery") or {}).get("candidate_count", 0),
        "offers_found": len(verified),
        "all_verified_offers": verified,
        "top_offers": top,
        "audit_history_high_risk": [row for row in verified if row.get("verification_status") == HIGH_RISK],
        "suppliers_for_price_request": calls,
        "public_price_below_target": bool(below),
        "minimum_public_price": best.get("public_price") if best else None,
        "minimum_public_price_supplier": best.get("supplier_name") if best else None,
        "verification_counts": dict(Counter(row.get("verification_status") for row in verified)),
        "source_errors": prices.get("source_errors") or [],
        "summary": comment,
        "preliminary_economics": _preliminary_economics(
            item, best, float((config.get("calculator") or {}).get("eat_commission_rate", 0.03))
        ),
    }


def _capture_and_collect(context: Any, page: Any, lower: datetime, upper: datetime,
                         logger: logging.Logger) -> tuple[dict[str, Any], dict[str, str]]:
    captured: dict[str, Any] = {}
    config = load_config()
    purchase_types = dict(config["proven_purchase_type_titles"])

    def capture(response: Response) -> None:
        if ENDPOINT_PART in response.url and response.status == 200:
            try:
                payload = response.json()
                if _purchase_batch(payload):
                    request: Request = response.request
                    captured.update({"url": response.url, "method": request.method,
                                     "headers": dict(request.headers),
                                     "post_data": request.post_data_json if request.post_data else None})
            except Exception:
                pass
        if "filter-purchase-types" in response.url and response.status == 200:
            try:
                purchase_types.update(_purchase_type_map(response.json()))
            except Exception:
                pass

    page.on("response", capture)
    try:
        page.goto(PURCHASES_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(10_000)
    finally:
        page.remove_listener("response", capture)
    body = captured.get("post_data")
    if not isinstance(body, dict):
        raise RuntimeError("Не удалось перехватить POST списка закупок ЕАТ")
    body = apply_server_deadline_prefilter(body, lower, upper)
    page_fields = _find_numeric_fields(body, PAGE_KEYS)
    size_fields = _find_numeric_fields(body, SIZE_KEYS)
    if len(page_fields) != 1 or len(size_fields) != 1:
        raise RuntimeError("Не удалось однозначно определить page/size запроса ЕАТ")
    page_path, size_path = page_fields[0][0], size_fields[0][0]
    headers = _request_headers(captured["headers"])
    try:
        response = context.request.get(PURCHASE_TYPES_URL, headers=headers)
        if response.ok:
            purchase_types.update(_purchase_type_map(response.json()))
    except Exception as exc:
        logger.warning("purchase types unavailable: %s", type(exc).__name__)

    def fetch_page(requested_page: int, page_size: int) -> Any:
        request_body = copy.deepcopy(body)
        _set_path(request_body, page_path, requested_page)
        _set_path(request_body, size_path, page_size)
        response = context.request.fetch(captured["url"], method=captured["method"],
                                         headers=headers, data=request_body)
        if not response.ok:
            raise RuntimeError(f"page={requested_page}: HTTP {response.status}")
        return response.json()

    pagination = collect_all_pages(fetch_page, endpoint=captured["url"],
                                   page_size=DEFAULT_PAGE_SIZE, pause_seconds=0.25)
    if not pagination["end_proven"]:
        raise RuntimeError(pagination.get("diagnostic_error") or "Конец выдачи ЕАТ не доказан")
    pagination["server_prefilter"] = {
        "applicationFillingStartDate": lower.isoformat(),
        "applicationFillingEndDate": upper.isoformat(),
        "semantics": "предварительный диапазон UI; точный deadline проверен локально",
    }
    return pagination, purchase_types


def _safe_item(raw_item: dict[str, Any], resolved: dict[str, Any], model: dict[str, Any],
               raw: dict[str, Any], card: dict[str, Any], audit: dict[str, Any], number: int) -> dict[str, Any]:
    identity = card.get("raw") or {}
    quantity = raw_item.get("quantity")
    unit_price = raw_item.get("unitPrice")
    if unit_price is None and quantity not in (None, 0) and raw_item.get("sum") is not None:
        try:
            unit_price = round(float(raw_item["sum"]) / float(quantity), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return {
        "purchase_id": identity.get("id") or card.get("purchase_id"),
        "trade_number": identity.get("tradeNumber") or raw.get("tradeNumber"),
        "purchase_url": card.get("card_url"),
        "subject": raw.get("subject"),
        "deadline": identity.get("applicationFillingEndDate") or raw.get("applicationFillingEndDate"),
        "item_number": number,
        "item_name": raw_item.get("description") or raw_item.get("name") or raw_item.get("eatTitle"),
        "quantity": quantity,
        "unit": raw_item.get("okeiTitle") or (raw_item.get("okei") or {}).get("title"),
        "customer_unit_price": unit_price,
        "customer_sum": raw_item.get("sum"),
        "nmck": raw.get("price"),
        "commission_fee": raw.get("commissionFee"),
        "delivery_address": ((raw.get("deliveryInfos") or [{}])[0].get("deliveryAddress") or {}).get("formattedFullInfo"),
        "delivery_period": raw.get("deliveryPeriod"),
        "delivery_working_days": raw.get("isDeliveryDaysWorking"),
        "payment_type": raw.get("paymentType"),
        "okpd2_code": ((raw_item.get("okpd2") or {}).get("code")
                       if isinstance(raw_item.get("okpd2"), dict)
                       else raw_item.get("okpd2Code")),
        "eat_code": ((raw_item.get("eat") or {}).get("code")
                     if isinstance(raw_item.get("eat"), dict)
                     else raw_item.get("eatCode")),
        "model": model,
        "requirements_count": len(resolved.get("requirements") or []),
        "special_conditions": audit.get("special_conditions") or "",
        "audit_status": audit.get("audit_status"),
        "documents_found": len(card.get("documents") or []),
        "documents_processed": audit.get("documents_analyzed", 0),
        "customer_check": extract_customer(
            card,
            [{"text": x.get("text") or ""}
             for x in (card.get("documents") or [])],
        ),
        "traceability": analyze_item({
            **raw_item,
            "okpd2Code": ((raw_item.get("okpd2") or {}).get("code")
                          if isinstance(raw_item.get("okpd2"), dict)
                          else raw_item.get("okpd2Code")),
        }),
        "supplier_search": None,
    }


def _sheet_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Адаптировать результат MVP к общему production-upsert payload."""
    model = item.get("model") or {}
    search = item.get("supplier_search") or {}
    offers = [offer for offer in (search.get("top_offers") or [])
              if offer.get("public_price") is not None][:3]
    preliminary = search.get("preliminary_economics") or {}
    customer_total = preliminary.get("customer_position_total")
    commission = preliminary.get("eat_commission")
    nmck_after_commission = (
        round(float(customer_total) - float(commission), 2)
        if customer_total is not None and commission is not None else None
    )
    customer = item.get("customer_check") or {}
    audit_status = item.get("audit_status")
    model_status = model.get("status_ru") or "Требуется подбор модели"
    warnings = list(model.get("source_warnings") or [])
    if model.get("route") != "exact":
        warnings.append(model_status)
    if search.get("summary") and search.get("summary") != "ХОРОШИЙ КАНДИДАТ ДЛЯ ПРОСЧЁТА":
        warnings.append(search["summary"])
    return {
        "procurement": {
            "id": item.get("purchase_id"),
            "trade_number": item.get("trade_number"),
            "url": item.get("purchase_url"),
            "subject": item.get("subject"),
            "nmck": item.get("nmck") or item.get("customer_sum"),
            "deadline": item.get("deadline"),
            "delivery_address": item.get("delivery_address"),
            "delivery_period": item.get("delivery_period"),
            "delivery_working_days": item.get("delivery_working_days"),
            "payment_type": item.get("payment_type"),
            "commission_fee": item.get("commission_fee"),
            "contact": {
                "phone": customer.get("customer_phone"),
                "email": customer.get("customer_email"),
            },
        },
        "item": {
            "position_number": item.get("item_number"),
            "display_name": item.get("item_name"),
            "okpd2_code": item.get("okpd2_code"),
            "eat_code": item.get("eat_code"),
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "customer_unit_price": item.get("customer_unit_price"),
            "sum": item.get("customer_sum"),
        },
        "documents": {
            "found": item.get("documents_found", 0),
            "processed": item.get("documents_processed", 0),
        },
        "audit": {
            "status": audit_status,
            "special_conditions": item.get("special_conditions"),
            "contract_analysis": (
                "Документы проанализированы" if audit_status == "analyzed"
                else "Требуется проверка документов"
            ),
            "requirements_count": item.get("requirements_count", 0),
            "customer_check": customer,
        },
        "traceability": item.get("traceability") or {},
        "model": {
            "selected_model": model.get("model"),
            "customer_model": model.get("model"),
            "search_mode": model.get("mode"),
            "equivalent_allowed": model.get("mode") == "EXACT_MODEL_OR_EQUIVALENT",
            "compliance_required": False,
            "compliance_status": model_status,
            "compliance_reason": model.get("reason") or model_status,
            "full_analogs_note": (
                "Подбор аналогов не выполнялся в текущем MVP"
                if model.get("route") != "exact" else ""
            ),
        },
        "supplier_search": {
            "confirmed_offers": offers,
            "minimum_confirmed_price": search.get("minimum_public_price"),
            "suppliers_for_call": search.get("suppliers_for_price_request") or [],
        },
        "economics": {
            "nmck_after_eat_commission": nmck_after_commission,
            "preliminary_margin_before_logistics": preliminary.get(
                "preliminary_reserve_before_logistics_and_taxes"
            ),
        },
        "current_analysis_result": search.get("summary") or model_status,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _write_reports(result: dict[str, Any]) -> None:
    lines = ["# TENDER_AI — MVP exact-model batch", "",
             f"Проверено: {result['finished_at']}",
             f"Окно МСК: {result['deadline_window']['from']} — {result['deadline_window']['to']}", "",
             "## Итоги", ""]
    for key, value in result["summary"].items():
        lines.append(f"- {key}: {value}")
    for item in result["items"]:
        lines.extend(["", f"## Закупка № {item.get('trade_number') or item['purchase_id']}", "",
                      f"- Позиция: {item['item_number']}. {item.get('item_name') or 'Нет данных'}",
                      f"- Количество: {item.get('quantity')} {item.get('unit') or ''}",
                      f"- Цена заказчика за единицу: {item.get('customer_unit_price')}",
                      f"- Модель: {item['model'].get('model') or 'Не определена'}",
                      f"- Источник модели: {_source_label(item['model'].get('model_source'))}",
                      f"- Режим: {item['model'].get('mode')}"])
        search = item.get("supplier_search")
        if not search:
            lines.append(f"- Статус: {item['model']['status_ru']}")
            continue
        lines.extend([f"- Целевая цена −15%: {search.get('target_unit_price', 'не рассчитана')}", "", "### TOP предложений", ""])
        for number, offer in enumerate(search["top_offers"], 1):
            lines.extend([f"{number}. {offer.get('supplier_name')} — {offer.get('public_price') or 'цена не указана'} ₽",
                          f"   - Наличие: {offer.get('availability_display')}",
                          f"   - Доставка: {offer.get('delivery_information') or 'не определена'}",
                          f"   - Проверка: {offer.get('verification_status')}",
                          f"   - Ссылка: {offer.get('product_url')}"])
        lines.append(f"- Итог: {search['summary']}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Номер закупки", "Позиция", "Модель", "Режим", "Цена заказчика",
                         "Цель -15%", "Минимальная публичная цена", "Поставщик", "Итог"])
        for item in result["items"]:
            search = item.get("supplier_search") or {}
            writer.writerow([item.get("trade_number"), item.get("item_number"), item["model"].get("model"),
                             item["model"].get("mode"), item.get("customer_unit_price"),
                             search.get("target_unit_price"), search.get("minimum_public_price"),
                             search.get("minimum_public_price_supplier"),
                             search.get("summary") or item["model"].get("status_ru")])


def run(*, resume: bool = False,
        sheet_writer=upsert_live_payload) -> dict[str, Any]:
    started = time.monotonic()
    started_at = _now_utc()
    logger = _logger()
    config = load_config()
    lower, upper = deadline_window()
    previous = {}
    if resume and CHECKPOINT_PATH.exists():
        previous = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    # Результаты старой маршрутизации не должны возвращать compliance-gate
    # после resume. Сохраняем только checkpoint текущей версии правила.
    completed = (previous.get("completed_supplier_searches") or {}
                 if previous.get("price_readiness_version") == PRICE_READINESS_VERSION
                 else {})
    errors: list[dict[str, Any]] = []
    analyzed_items: list[dict[str, Any]] = []
    documents_count = 0

    with sync_playwright() as playwright:
        session = open_authorized_eat_browser(playwright)
        try:
            pagination, purchase_types = _capture_and_collect(
                session.context, session.page, lower, upper, logger
            )
            window_records = [row for row in pagination["unique_records"]
                              if _deadline_in_window(row, lower, upper)]
            confidential = []
            passed = []
            filtered = []
            for raw in window_records:
                try:
                    decision = filter_purchase_v2(
                        raw, purchase_types.get(str(raw.get("purchaseTypeId"))), config
                    )
                except Exception as exc:
                    errors.append({"purchase_id": raw.get("id"), "stage": "filter",
                                   "error": f"{type(exc).__name__}: {exc}"})
                    continue
                filtered.append({"purchase_id": raw.get("id"), "trade_number": raw.get("tradeNumber"),
                                 "filter_result": decision.get("filter_result"),
                                 "reasons": decision.get("rejection_reasons") or []})
                if decision.get("filter_result") == "confidential_locked":
                    confidential.append(raw)
                elif decision.get("filter_result") == "passed":
                    passed.append(raw)
            passed.sort(key=lambda row: _parse_eat_datetime(row.get("applicationFillingEndDate"))
                        or datetime.max.replace(tzinfo=MOSCOW))
            _atomic_json(CONFIDENTIAL_PATH, {"generated_at": _now_utc(),
                                             "deadline_window": {"from": lower.isoformat(), "to": upper.isoformat()},
                                             "count": len(confidential), "purchases": confidential})

            for raw in passed:
                purchase_id = str(raw.get("id") or "")
                try:
                    card = _merge_card_list_raw(
                        fetch_purchase_card(session.context, session.page, purchase_id), raw
                    )
                    documents_count += len(card.get("documents") or [])
                    extraction = process_procurement_documents(card, card.get("documents") or [], logger=logger)
                    audit = audit_from_extraction(card, extraction)
                    lot = _lot(card)
                    raw_items = lot.get("lotItems") or []
                    for number, resolved in enumerate(audit.get("items") or [], 1):
                        raw_item = raw_items[number - 1] if number <= len(raw_items) else {}
                        model = _classify_model(resolved, raw_item)
                        analyzed_items.append(_safe_item(raw_item, resolved, model, lot, card, audit, number))
                except Exception as exc:
                    logger.exception("purchase=%s stage=card_documents", purchase_id)
                    errors.append({"purchase_id": purchase_id, "stage": "card_documents",
                                   "error": f"{type(exc).__name__}: {exc}"})

            exact_items = [item for item in analyzed_items if item["model"]["route"] == "exact"
                           and item.get("customer_unit_price") is not None]
            exact_items.sort(key=lambda item: (item.get("deadline") or "9999", item["purchase_id"],
                                                item["item_number"]))
            for item in exact_items[:MAX_SUPPLIER_SEARCHES]:
                key = f"{item['purchase_id']}:{item['item_number']}"
                if key in completed:
                    item["supplier_search"] = completed[key]
                    continue
                try:
                    item["supplier_search"] = _process_suppliers(item, config)
                    completed[key] = item["supplier_search"]
                except Exception as exc:
                    logger.exception("purchase=%s item=%s stage=supplier_search",
                                     item["purchase_id"], item["item_number"])
                    errors.append({"purchase_id": item["purchase_id"], "item_number": item["item_number"],
                                   "stage": "supplier_search", "error": f"{type(exc).__name__}: {exc}"})
                    item["supplier_search"] = {"summary": "ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА",
                                               "error": f"{type(exc).__name__}: {exc}",
                                               "top_offers": [], "all_verified_offers": [],
                                               "verification_counts": {}}
                    completed[key] = item["supplier_search"]
                _atomic_json(CHECKPOINT_PATH, {"updated_at": _now_utc(),
                                               "price_readiness_version": PRICE_READINESS_VERSION,
                                               "completed_supplier_searches": completed,
                                               "supplier_search_cap": MAX_SUPPLIER_SEARCHES})
        finally:
            session.close()

    searched = [item for item in analyzed_items if item.get("supplier_search")]
    verification = Counter()
    supplier_total = 0
    below_target = []
    for item in searched:
        search = item["supplier_search"] or {}
        supplier_total += len(search.get("all_verified_offers") or [])
        verification.update(search.get("verification_counts") or {})
        if search.get("public_price_below_target"):
            below_target.append(f"{item.get('trade_number')} / позиция {item['item_number']}")

    sheet_receipts: list[dict[str, Any]] = []
    for item in analyzed_items:
        try:
            sheet_receipts.append(sheet_writer(_sheet_payload(item)))
        except Exception as exc:
            logger.exception("purchase=%s item=%s stage=google_sheets", item.get("purchase_id"),
                             item.get("item_number"))
            errors.append({"purchase_id": item.get("purchase_id"),
                           "item_number": item.get("item_number"),
                           "stage": "google_sheets",
                           "error": f"{type(exc).__name__}: {exc}"})

    summary = {
        "A. Получено от ЕАТ (raw)": pagination["raw_count"],
        "B. Осталось после точного deadline": len(window_records),
        "C. Прошло бизнес-фильтры": len(passed),
        "D. Товарных позиций": len(analyzed_items),
        "E. Позиции с точной моделью": len(exact_items),
        "F. Требуют model discovery": sum(x["model"]["route"] == "discovery" for x in analyzed_items),
        "G. Требуют ручной проверки": sum(x["model"]["route"] == "manual_review" for x in analyzed_items),
        "H. Exact-model позиций просчитано": len(searched),
        "I. Поставщиков найдено": supplier_total,
        "J. ✅": verification[PASSED],
        "J. 🟡": verification[MANUAL],
        "J. ⚪": verification[INSUFFICIENT],
        "J. 🔴": verification[HIGH_RISK],
        "Закрытых закупок сохранено отдельно": len(confidential),
        "Ошибок этапов": len(errors),
        "Google Sheets записано": len(sheet_receipts),
    }
    result = {
        "run_type": "LIVE_MVP_EXACT_MODEL_BATCH",
        "started_at": started_at,
        "finished_at": _now_utc(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "deadline_window": {"from": lower.isoformat(), "to": upper.isoformat(),
                            "rule": "deadline > now Moscow; deadline <= end of second workday Moscow"},
        "supplier_search_cap": MAX_SUPPLIER_SEARCHES,
        "pagination": {key: value for key, value in pagination.items()
                       if key not in {"raw_records", "unique_records"}},
        "filter_audit": filtered,
        "summary": summary,
        "below_target_positions": below_target,
        "items": analyzed_items,
        "errors": errors,
        "artifacts": {"confidential": str(CONFIDENTIAL_PATH), "report": str(REPORT_PATH),
                      "csv": str(CSV_PATH), "checkpoint": str(CHECKPOINT_PATH)},
        "google_sheets_written": bool(analyzed_items) and len(sheet_receipts) == len(analyzed_items),
        "google_sheets_receipts": sheet_receipts,
        "old_parser_used": False,
        "documents_downloaded": documents_count,
    }
    _atomic_json(RESULT_PATH, result)
    _write_reports(result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
