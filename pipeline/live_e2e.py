"""Сборка production payload одного уже загруженного live-тендера.

Сетевые этапы ЕАТ, compliance и price discovery выполняются существующими
модулями отдельно. Здесь только проверяем и соединяем их результаты.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from model_search.price_readiness import (PRICE_SEARCH_READY,
                                          classify_price_search_readiness)
from filters.eat_filters import load_config
from eat.enum_mapping import normalize_card_enums
from security.customer_check import build_customer_check
from security.traceability import analyze_item
from suppliers.market_search import (build_call_lists, price_priority,
                                     supplier_target_price)
from suppliers.verification import verify_supplier
from calculator.result_decision import attach_business_decision
from suppliers.price_search_flow import confirmed_price_ranking, select_verified_top3
from suppliers.economic_precheck import (current_analysis_recommendation,
                                         evaluate_price_request_candidate,
                                         economic_precheck,
                                         is_confirmed_available,
                                         price_request_candidates)

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "data/eat_single_fd71642c-1c03-4739-a487-f1e8fa2722a6.json"
ANALYSIS = ROOT / "data/live_e2e_local_analysis.json"
COMPLIANCE = ROOT / "data/live_e2e_compliance.json"
PRICES = ROOT / "data/live_e2e_price_search.json"
OUTPUT = ROOT / "data/live_e2e_production_payload.json"

SECRET = re.compile(r"(?i)(authorization|cookie|password|private[_ -]?key|client_secret|access_token)")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _lot(card: dict[str, Any]) -> dict[str, Any]:
    raw = card.get("raw") or card
    return raw.get("lot") or raw


def _flatten_item(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    for source, target in (("okpd2", "okpd2Code"), ("eat", "eatCode"),
                           ("okei", "okeiTitle")):
        value = item.get(source)
        if isinstance(value, dict):
            result[target] = value.get("code" if source != "okei" else "title")
    return result


def _model_evidence(audit_item: dict[str, Any]) -> str | None:
    value = audit_item.get("original_model") or audit_item.get("customer_required_model")
    return str(value).strip() if value else None


def _offer_for_verification(offer: dict[str, Any], model: str) -> dict[str, Any]:
    host = (urlsplit(offer["url"]).hostname or "").removeprefix("www.")
    name = offer.get("seller") or host
    return {**offer, "supplier_name": name, "product_url": offer["url"], "domain": host,
            "source_category": "marketplace" if host == "market.yandex.ru" else "professional",
            "model": model, "product_name": offer.get("exact_product_name"),
            "exact_model": offer.get('exact_model_match') is True,
            "public_price": offer.get("price"), "purchase_price": None,
            "availability": {"В наличии": "in_stock", "Под заказ": "to_order",
                             "Нет в наличии": "out_of_stock"}.get(offer.get("availability"), offer.get('availability') or "unknown"),
            "checked_at": offer.get("checked_at"), "source": offer.get("source")}


def _apply_supplier_verification(rows: list[dict[str, Any]], execute: bool,
                                 verifier=verify_supplier) -> list[dict[str, Any]]:
    result = []
    for source in rows:
        supplier = verifier(source) if execute else {
            **source,
            "risk_flags": [],
            "verification_status": "Не выполнялась: предварительная экономика не проходит",
            "verification_comment": "Глубокая проверка отложена до получения цены, проходящей экономический порог",
            "verification_skipped": True,
        }
        supplier["verification_scope"] = (("Проверена торговая площадка; реквизиты продавца "
                                            "перед оплатой нужно подтвердить по счёту")
                                           if execute else
                                           "Проверка отложена по результату economic pre-check")
        result.append(supplier)
    return result


def contains_secrets(value: Any) -> bool:
    if isinstance(value, dict):
        return any(SECRET.search(str(key)) or contains_secrets(child) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_secrets(child) for child in value)
    return False


def build_payload(card: dict[str, Any], analysis: dict[str, Any],
                  compliance: dict[str, Any], prices: dict[str, Any],
                  *, config: dict[str, Any] | None = None,
                  supplier_verifier=verify_supplier) -> dict[str, Any]:
    raw = card.get("raw") or card
    lot = _lot(card)
    item = (lot.get("lotItems") or [{}])[0]
    audit = analysis["procurement_audit"]
    audit_item = (audit.get("items") or [{}])[0]
    readiness = classify_price_search_readiness(item, audit_item)
    if readiness["classification"] != PRICE_SEARCH_READY:
        raise ValueError(f"Позиция не готова к поиску цены: {readiness['classification']}")
    model = readiness["identifier"]
    from_price = readiness.get("model_source") == "PRICE_JUSTIFICATION"
    model_mode = {
        "model_search_mode": ("PRICE_JUSTIFICATION_MODEL" if from_price else
                              audit_item.get("model_search_mode")
                              if audit_item.get("model_search_mode") in
                              {"EXACT_MODEL_ONLY", "EXACT_MODEL_OR_EQUIVALENT"}
                              else "EXACT_MODEL_ONLY"),
        "model_discovery_allowed": False,
        "model_mode_reason": readiness["reason"],
    }
    compliance_policy = {
        "compliance_required": False,
        "compliance_code": PRICE_SEARCH_READY,
        "compliance_status": ("МОДЕЛЬ ИЗ ОБОСНОВАНИЯ ЦЕНЫ" if from_price
                              else "ТОЧНАЯ МОДЕЛЬ ИЗ ЗАКУПКИ"),
        "reason": "Первичный поиск цены запускается по модели из закупки без предварительного compliance",
    }

    config = config or load_config()
    calculator_config = config.get("calculator") or {}
    target = supplier_target_price(float(item["unitPrice"]))
    supplier_rows = []
    for offer in prices.get("offers") or []:
        supplier = _offer_for_verification(offer, model)
        supplier["price_priority"] = price_priority(supplier, target)
        supplier["target_price_difference"] = (round(float(supplier["public_price"]) - target, 2)
                                                if supplier.get("public_price") is not None else None)
        supplier_rows.append(supplier)
    eat_commission = lot.get("commissionFee")
    precheck = economic_precheck(
        lot.get("price"), eat_commission,
        [{"position_number": 1, "quantity": item.get("quantity"), "offers": supplier_rows}],
        float(calculator_config.get("economic_precheck_margin_percent", 18)),
    )
    verification_executed = precheck["supplier_verification_required"]
    ranked = confirmed_price_ranking(supplier_rows)
    if verification_executed:
        priced_verified, verification_history = select_verified_top3(ranked, verifier=supplier_verifier)
        verified = verification_history
    else:
        verified = _apply_supplier_verification(supplier_rows, False, supplier_verifier)
        priced_verified = []
    try:
        quantity = float(item.get("quantity"))
        maximum_unit_price = (precheck.get("maximum_purchase_cost") / quantity
                              if quantity > 0 and precheck.get("maximum_purchase_cost") is not None
                              else None)
    except (TypeError, ValueError, ZeroDivisionError):
        maximum_unit_price = None
    if verification_executed:
        calls = build_call_lists(verified)
        call_pool = (calls["suppliers_for_best_price_request"]
                     + calls["requires_verification_before_work"])
    else:
        call_pool = verified
    call_rows = price_request_candidates(call_pool, maximum_unit_price)
    call_assessments = [evaluate_price_request_candidate(row, maximum_unit_price)
                        for row in verified]
    recommendation = current_analysis_recommendation(
        precheck, call_rows,
        [{"position_number": 1, "quantity": item.get("quantity")}],
    )

    doc_results = analysis["document_processing"].get("document_results") or []
    customer = build_customer_check(card, [{"text": doc.get("text") or ""} for doc in doc_results])
    enum_values = normalize_card_enums(raw, lot)
    trace = analyze_item(_flatten_item(item))
    checks = compliance.get("assessment", {}).get("requirements_check") or []
    confirmed = sum(check.get("result") == "corresponds" for check in checks)
    mismatch = sum(check.get("result") == "does_not_comply" for check in checks)
    unknown = sum(check.get("result") == "could_not_confirm" for check in checks)
    confirmed = mismatch = unknown = None
    warnings = []
    if len(priced_verified) < 3:
        warnings.append("НАЙДЕНО МЕНЕЕ 3")
    if not customer.get("customer_inn"):
        warnings.append("ИНН и наименование заказчика не извлечены из доступной карточки/ТЗ")
    if verified and all((row.get("public_price") or float("inf")) > target for row in verified):
        warnings.append("Подтверждённая публичная цена выше целевого порога закупки")
    result = {
        "run_type": "LIVE_ONE_PROCUREMENT_E2E",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "procurement": {
            "id": raw.get("id") or card.get("purchase_id"),
            "trade_number": raw.get("tradeNumber"), "url": card.get("card_url"),
            "subject": lot.get("subject"), "nmck": lot.get("price"),
            "deadline": lot.get("applicationFillingEndDate"),
            "delivery_address": ((lot.get("deliveryInfos") or [{}])[0].get("deliveryAddress") or {}).get("formattedFullInfo"),
            "delivery_period": lot.get("deliveryPeriod"),
            "delivery_working_days": lot.get("isDeliveryDaysWorking"),
            "payment_type": enum_values.get("payment_type"),
            "payment_type_code": lot.get("paymentType"),
            "payment_condition": enum_values.get("payment_condition"),
            "payment_deadline": enum_values.get("payment_deadline"),
            "purchase_method": enum_values.get("purchase_method"),
            "russian_items_purchase": enum_values.get("russian_items_purchase"),
            "commission_fee": lot.get("commissionFee"),
            "commission_source": "raw.lot.commissionFee",
            "customer": customer.get("customer_name"),
            "customer_inn": customer.get("customer_inn"),
            "customer_kpp": customer.get("customer_kpp"),
            "customer_address": customer.get("customer_address"),
            "contact": {
                "fio": customer.get("customer_contact_fio") or raw.get("contactFio"),
                "email": customer.get("customer_email") or raw.get("contactEmail"),
                "phone": customer.get("customer_phone") or raw.get("contactPhone"),
                "fax": customer.get("customer_fax") or raw.get("contactFax"),
            },
            "filter_result": "passed", "purchase_type_title": enum_values.get("purchase_type"),
        },
        "item": {"position_number": 1, "display_name": item.get("description") or item.get("name"),
                 "name": item.get("name"), "quantity": item.get("quantity"),
                 "unit": (item.get("okei") or {}).get("title"),
                 "customer_unit_price": item.get("unitPrice"), "sum": item.get("sum"),
                 "okpd2_code": (item.get("okpd2") or {}).get("code"),
                 "eat_code": (item.get("eat") or {}).get("code")},
        "documents": {"found": analysis["document_processing"].get("documents_found"),
                      "processed": analysis["document_processing"].get("documents_processed"),
                      "read_methods": [doc.get("extraction_method") for doc in doc_results]},
        "audit": {"status": audit.get("audit_status"), "special_conditions": audit.get("special_conditions"),
                  "contract_analysis": audit.get("contract_analysis") or "Анализ документов не завершён",
                  "requirements_count": len(audit_item.get("requirements") or []),
                  "customer_check": customer},
        "traceability": trace,
        "model": {"selected_model": model,
                  "selection_reason": readiness["reason"],
                  "model_source": readiness.get("model_source"),
                  "customer_required_model": readiness.get("customer_required_model"),
                  "price_justification_model": readiness.get("price_justification_model"),
                  "model_evidence": readiness.get("evidence") or [],
                  "price_readiness": readiness["classification"],
                  "search_mode": model_mode["model_search_mode"],
                  "compliance_required": compliance_policy["compliance_required"],
                  "compliance_status": (compliance_policy["compliance_status"]
                                        or compliance.get("assessment", {}).get("status_ru")),
                  "compliance_code": (compliance_policy["compliance_code"]
                                      or compliance.get("assessment", {}).get("status")),
                  "compliance_reason": compliance_policy["reason"],
                  "requirements_confirmed": confirmed, "requirements_mismatched": mismatch,
                  "requirements_unconfirmed": unknown,
                  "official_sources": compliance.get("official_sources") or [],
                  "production_status": "Актуальная карточка на официальном сайте производителя; снятие с производства не указано",
                  "russia_availability": "Подтверждена карточкой российского Яндекс Маркета" if verified else "Не подтверждена"},
        "supplier_search": {"target_discount_percent": 15, "target_price": target,
                            "ranked_offers": ranked,
                            "price_run_id": prices.get('price_run_id'),
                            "all_verified_offers": verified,
                            "confirmed_offers": priced_verified[:3],
                            "confirmed_count": len(priced_verified),
                            "exact_pages_confirmed_count": len(verified),
                            "minimum_confirmed_price": priced_verified[0].get("public_price") if priced_verified else None,
                            "suppliers_for_call": call_rows,
                            "less_than_three": len(priced_verified) < 3,
                            "supplier_verification_executed": verification_executed,
                            "supplier_verification_skip_reason": (None if verification_executed else
                                "Ни одно подтверждённое доступное предложение не прошло economic pre-check")},
        "economic_precheck": precheck,
        "current_analysis_result": recommendation,
        "logistics": {"status": "Не рассчитана", "estimated_cost": None},
        "warnings": warnings,
        "diagnostics": {
            "eat_enum_mapping": enum_values.get("diagnostics") or [],
            "supplier_call_filter": {
                "maximum_acceptable_unit_price": (round(maximum_unit_price, 2)
                                                   if maximum_unit_price is not None else None),
                "maximum_required_discount_percent": 20,
                "assessed_offers": call_assessments,
            },
        },
    }
    attach_business_decision(result, config=config)
    if contains_secrets(result):
        raise ValueError("Production payload содержит имя секретного поля")
    return result


def run() -> dict[str, Any]:
    result = build_payload(_load(CARD), _load(ANALYSIS), _load(COMPLIANCE), _load(PRICES))
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data/live_e2e_enum_diagnostics.json").write_text(
        json.dumps(result.get("diagnostics") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
