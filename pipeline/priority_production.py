"""Быстрый production-путь одной закупки PRIORITY 1 — EXACT MODEL."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from documents.pipeline import (audit_from_extraction, document_processing_stop_reason,
                                process_procurement_documents)
from filters.eat_filters import load_config
from google_sheets.production_upsert import upsert_live_payload
from model_search.live_price_search import search_exact_model_prices
from model_search.price_readiness import PRICE_SEARCH_READY, classify_price_search_readiness
from pipeline.live_e2e import build_payload
from calculator.result_decision import attach_business_decision

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = ROOT / "data/live_e2e_selected.json"
OUTPUT = ROOT / "data/priority_exact_production_test.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _lot(fixture: dict[str, Any]) -> dict[str, Any]:
    raw = fixture.get("raw") or fixture
    return raw.get("lot") or raw


def _exact_item(fixture: dict[str, Any], audit: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    raw_items = _lot(fixture).get("lotItems") or []
    for position, item in enumerate(audit.get("items") or [], 1):
        raw_item = raw_items[position - 1] if position <= len(raw_items) else {}
        readiness = classify_price_search_readiness(raw_item, item)
        if readiness["classification"] == PRICE_SEARCH_READY:
            return position, item, readiness
    raise RuntimeError("В закупке не подтверждена точная модель")


def _economics(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    nmck = payload["procurement"].get("nmck")
    commission = payload["procurement"].get("commission_fee")
    try:
        nmck_value = float(nmck) if nmck is not None else None
        commission_value = float(commission) if commission is not None else None
        if (nmck_value is not None and not math.isfinite(nmck_value)) or (
                commission_value is not None
                and (not math.isfinite(commission_value) or commission_value < 0)):
            raise ValueError("Недопустимое значение комиссии или НМЦК")
    except (TypeError, ValueError):
        nmck_value = commission_value = None
    after = (round(nmck_value - commission_value, 2)
             if nmck_value is not None and commission_value is not None else None)
    price = payload["supplier_search"].get("minimum_confirmed_price")
    quantity = float(payload["item"].get("quantity") or 0)
    purchase_cost = round(float(price) * quantity, 2) if price is not None else None
    reserve = round(after - purchase_cost, 2) if after is not None and purchase_cost is not None else None
    return {"eat_commission": commission_value,
            "commission_source": "procurement.commission_fee",
            "nmck_after_eat_commission": after,
            "minimum_purchase_cost": purchase_cost,
            "preliminary_margin_before_logistics": reserve,
            "logistics_included": False}


def run_exact_fixture(fixture_path: Path = DEFAULT_FIXTURE, *,
                      price_search: Callable[..., dict[str, Any]] = search_exact_model_prices,
                      sheet_writer: Callable[[dict[str, Any]], dict[str, Any]] = upsert_live_payload) -> dict[str, Any]:
    started = time.monotonic()
    fixture = _load(fixture_path)
    extraction = process_procurement_documents(fixture, fixture.get("documents") or [])
    stop_reason = document_processing_stop_reason(extraction)
    if stop_reason:
        # Быстрый production-путь тоже обязан сохранить строку и остановить
        # расчёт, если PDF нельзя безопасно прочитать.
        from pipeline.mvp_exact_batch import _blocked_items, _sheet_payload
        blocked = _blocked_items(fixture, extraction, stop_reason, "card_documents")
        if len(blocked) != 1:
            raise RuntimeError("Контрольный быстрый запуск поддерживает одну остановленную позицию")
        payload = _sheet_payload(blocked[0])
        receipt = sheet_writer(payload)
        result = {"run_type": "PRIORITY_1_EXACT_MODEL_PRODUCTION_TEST",
                  "completed_at": datetime.now(timezone.utc).isoformat(),
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "payload": payload, "google_sheets": receipt,
                  "status": "blocked", "manual_stop_reason": stop_reason}
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    audit = audit_from_extraction(fixture, extraction)
    position, audit_item, readiness = _exact_item(fixture, audit)
    model = readiness["identifier"]
    if len(_lot(fixture).get("lotItems") or []) != 1 or position != 1:
        raise RuntimeError("Контрольный быстрый запуск поддерживает одну exact-model позицию")

    trade = str((_lot(fixture).get("tradeNumber") or fixture.get("purchase_id")))
    price_path = ROOT / f"data/price_search_{trade}.json"
    prices = price_search(model, output_path=price_path)
    # В EXACT_MODEL_ONLY полную проверку характеристик не запускаем. Статус
    # присваивается build_payload только после exact-model match на карточке.
    assessment = {"status": None, "status_ru": None, "requirements_check": []}
    analysis = {"document_processing": extraction, "procurement_audit": audit}
    config = load_config()
    payload = build_payload(fixture, analysis,
                            {"assessment": assessment, "official_sources": []}, prices,
                            config=config)
    mode = audit_item.get("model_search_mode")
    equivalent_allowed = mode == "EXACT_MODEL_OR_EQUIVALENT"
    payload["processing"] = {"queue": "PRIORITY_1_EXACT_MODEL",
                             "model_discovery_before_price": False,
                             "elapsed_seconds": None}
    payload["model"]["customer_model"] = model
    payload["model"]["customer_required_model"] = readiness.get("customer_required_model")
    payload["model"]["price_justification_model"] = readiness.get("price_justification_model")
    payload["model"]["model_source"] = readiness.get("model_source")
    payload["model"]["model_evidence"] = readiness.get("evidence") or []
    payload["model"]["price_readiness"] = readiness["classification"]
    payload["model"]["equivalent_allowed"] = equivalent_allowed
    payload["model"]["full_analogs"] = []
    payload["model"]["production_status"] = "Актуальность производства не подтверждена"
    payload["model"]["full_analogs_note"] = (
        "Дополнительный поиск не выполнялся в быстром pipeline"
        if equivalent_allowed else
        "НЕ ИСПОЛЬЗОВАТЬ ДЛЯ ПОДАЧИ — замена модели заказчиком не разрешена; "
        "дополнительный поиск аналогов не выполнялся в быстром pipeline"
    )
    payload["economics"] = _economics(payload, config)
    # После добавления всех входов заново применяем каноническую формулу.
    attach_business_decision(payload, config=config)
    if payload["supplier_search"]["confirmed_count"] < 3:
        warning = "НАЙДЕНО МЕНЕЕ 3 ПОСТАВЩИКОВ"
        payload["warnings"] = [warning if x == "НАЙДЕНО МЕНЕЕ 3" else x
                               for x in payload["warnings"]]
    payload["processing"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipt = sheet_writer(payload)  # Потоковая запись сразу после готовности.
    result = {"run_type": "PRIORITY_1_EXACT_MODEL_PRODUCTION_TEST",
              "completed_at": datetime.now(timezone.utc).isoformat(),
              "elapsed_seconds": payload["processing"]["elapsed_seconds"],
              "payload": payload, "google_sheets": receipt,
              "price_search_path": str(price_path)}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run_exact_fixture()
    print(json.dumps({"elapsed_seconds": result["elapsed_seconds"],
                      "trade_number": result["payload"]["procurement"]["trade_number"],
                      "model": result["payload"]["model"]["selected_model"],
                      "suppliers": result["payload"]["supplier_search"]["confirmed_count"],
                      "sheet_row": result["google_sheets"]["row"]}, ensure_ascii=False, indent=2))
