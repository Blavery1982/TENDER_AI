"""Production-oriented batch-каркас, безопасный локальный dry-run без live I/O."""
from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from calculator.result_decision import attach_business_decision
from documents.item_sources import resolve_item_sources, VERSION as SOURCE_VERSION
from documents.pipeline import (audit_from_extraction, document_processing_stop_reason,
                                process_procurement_documents)
from filters.semantic_bad_words import filter_purchase_v2
from security.customer_check import build_customer_check
from security.traceability import analyze_item
from suppliers.market_search import SUPPLIER_TARGET_DISCOUNT_PERCENT, supplier_target_price
from model_search.live_discovery import discover_models
from model_search.search_mode import determine_model_search_mode, blocked_discovery_result
from model_search.price_readiness import (MODEL_DISCOVERY_REQUIRED as READINESS_DISCOVERY,
                                          PRICE_READINESS_VERSION, PRICE_SEARCH_READY,
                                          classify_price_search_readiness)

ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT = ROOT / "data/checkpoints/production_dry_run.json"
OUTPUT_DIR = ROOT / "data/production_dry_run"
SUMMARY_PATH = ROOT / "reports/production_dry_run_summary.md"
LOG_PATH = ROOT / "logs/production_dry_run.log"

MODEL_NOT_LIVE = "LIVE MODEL SEARCH ЕЩЁ НЕ ПОДКЛЮЧЁН"
SUPPLIER_NOT_LIVE = "LIVE SUPPLIER MARKET SEARCH ЕЩЁ НЕ ПОДКЛЮЧЁН"
SECRET_RE = re.compile(r"(?i)(authorization|cookie|token|password|private[_ -]?key)\s*[:=]\s*\S+")
PRIORITY_EXACT_MODEL = 1
PRIORITY_MODEL_NOT_SPECIFIED = 2


def _deadline_key(fixture: dict) -> str:
    lot = _lot(fixture)
    return str(lot.get("applicationFillingEndDate") or "9999-12-31T23:59:59")


def procurement_queue(fixture: dict, resolved_items: list[dict] | None = None) -> dict:
    """Определить очередь без сетевых вызовов; resolved_items учитывают документы."""
    lot = _lot(fixture)
    items = lot.get("lotItems") or []
    resolved = resolved_items or [resolve_item_sources(item, item_number=number, items=items)
                                  for number, item in enumerate(items, 1)]
    readiness = [classify_price_search_readiness(item, row)
                 for item, row in zip(items, resolved)]
    exact = [row.get("identifier") if row.get("price_search_ready") else None
             for row in readiness]
    # Документ ТЗ/КП является полноценным источником exact model. Для
    # многопозиционной закупки PRIORITY 1 допустим только при однозначной модели
    # каждой позиции; частичное сопоставление остаётся в PRIORITY 2.
    all_items_exact = bool(items and len(resolved) == len(items) and all(exact))
    priority = PRIORITY_EXACT_MODEL if all_items_exact else PRIORITY_MODEL_NOT_SPECIFIED
    return {"priority": priority,
            "queue": "PRIORITY_1_EXACT_MODEL" if all_items_exact else "PRIORITY_2_MODEL_NOT_SPECIFIED",
            "deadline": _deadline_key(fixture),
            "exact_models": list(dict.fromkeys(model for model in exact if model)),
            "price_readiness": readiness}


def prioritize_fixtures(fixtures: list[dict]) -> list[dict]:
    """Exact-model первыми; внутри очереди — ближайший срок подачи."""
    return sorted(fixtures, key=lambda item: (procurement_queue(item)["priority"],
                                               procurement_queue(item)["deadline"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _logger(path: Path = LOG_PATH) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"tender_ai.production_dry_run.{path}")
    if not logger.handlers:
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate = False
    return logger


def _safe_text(value: Any) -> str:
    return SECRET_RE.sub(r"\1=<redacted>", str(value)).replace("\n", " ")[:1200]


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _lot(fixture: dict) -> dict:
    raw = fixture.get("raw") or fixture
    return raw.get("lot") or raw


def load_local_fixtures() -> list[dict]:
    """Только сохранённые файлы; эта функция не содержит сетевых вызовов."""
    fixtures = []
    for path in sorted((ROOT / "data").glob("eat_single_*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        card["fixture_source"] = str(path)
        fixtures.append(card)
    saved = json.loads((ROOT / "data/eat_filtered_semantic_v2.json").read_text(encoding="utf-8"))["purchases"]
    multi = next(x for x in saved if len(x.get("raw", {}).get("lotItems") or []) >= 3)
    fixtures.append({"purchase_id": multi.get("raw_id"), "raw": deepcopy(multi["raw"]),
                     "documents": [], "purchase_type_title": multi.get("purchaseTypeTitle"),
                     "fixture_source": "data/eat_filtered_semantic_v2.json"})
    return fixtures


def _saved_model_result(trade_number: str, item_number: int) -> dict:
    paths = {"100205573126100053": ROOT / "data/procurement_model_search_third_test.json",
             "100250237126100180": ROOT / "data/model_search_tv_test.json"}
    path = paths.get(str(trade_number))
    if not path or not path.exists():
        return {"status": MODEL_NOT_LIVE, "source": None, "result": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    positions = data.get("positions") or []
    position = next((x for x in positions if int(x.get("position_number", x.get("item_index", -1))) == item_number), None)
    return {"status": "Использован сохранённый тестовый результат" if position else MODEL_NOT_LIVE,
            "source": str(path), "result": position}


def _saved_supplier_result(trade_number: str) -> dict:
    if str(trade_number) != "100205573126100053":
        return {"status": SUPPLIER_NOT_LIVE, "source": None, "offers": []}
    path = ROOT / "data/supplier_market_search_third_test.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"status": "Использован сохранённый тестовый результат", "source": str(path),
            "offers": data.get("offers") or [], "suppliers_for_call": data.get("suppliers_for_call") or [],
            "live_search_performed": False, "verification_performed": False}


def process_item(raw_item: dict, item_number: int, trade_number: str,
                 customer_check: dict, requirements: list[dict] | None = None,
                 fault: str | None = None, *, model_live: bool = False,
                 model_provider: Any = None, logger: logging.Logger | None = None,
                 source_resolution: dict | None = None,
                 procurement: dict | None = None) -> dict:
    if fault == "item":
        raise RuntimeError("synthetic item failure")
    quantity = raw_item.get("quantity")
    unit_price = raw_item.get("unitPrice")
    if unit_price is None and quantity not in (None, 0) and raw_item.get("sum") is not None:
        unit_price = float(raw_item["sum"]) / float(quantity)
    traceability = analyze_item(raw_item)
    model_input = {**raw_item, "procurement_number": trade_number, "item_number": item_number,
                   "item_name": raw_item.get("name") or raw_item.get("eatTitle") or raw_item.get("description"),
                   "quantity": quantity, "customer_unit_price": unit_price,
                   "structured_requirements": requirements or raw_item.get("structured_requirements") or raw_item.get("requirements") or []}
    resolved = source_resolution or resolve_item_sources(model_input)
    model_input.update(resolved)
    model_input["structured_requirements"] = resolved["requirements"]
    readiness = classify_price_search_readiness(raw_item, resolved)
    decision = determine_model_search_mode(model_input)
    if readiness["classification"] == PRICE_SEARCH_READY:
        model = {**decision, **readiness, "search_status": PRICE_SEARCH_READY,
                 "selected_model": readiness["identifier"], "model_discovery_called": False,
                 "compliance_required_before_price_search": False}
    elif readiness["classification"] == READINESS_DISCOVERY and model_live:
        model = {**discover_models(model_input, provider=model_provider, logger=logger), **decision,
                 **readiness, "model_discovery_called": True}
    elif readiness["classification"] == READINESS_DISCOVERY:
        model = {**_saved_model_result(trade_number, item_number), **decision, **readiness,
                 "model_discovery_called": False}
    else:
        model = {**blocked_discovery_result(decision), **readiness,
                 "model_discovery_called": False}
    # This orchestrator stage never calls supplier discovery, including after a live model hit.
    supplier = {"status": SUPPLIER_NOT_LIVE, "source": None, "offers": [], "live_search_performed": False,
                "baseline_model": readiness.get("identifier") or resolved.get("supplier_baseline_model"),
                "minimum_real_prices": resolved.get("supplier_prices_required"),
                "minimum_distinct_suppliers": resolved.get("distinct_suppliers_required"),
                "alternative_policy": resolved.get("alternative_policy")}
    target = supplier_target_price(unit_price) if unit_price is not None else None
    purchase_price = None
    model_available = model.get("status") != MODEL_NOT_LIVE if "status" in model else bool(model.get("selected_model"))
    result = {"item_number": item_number,
            "item_name": raw_item.get("description") or raw_item.get("name") or raw_item.get("eatTitle"),
            "quantity": quantity, "unit": raw_item.get("okeiTitle") or (raw_item.get("okei") or {}).get("title"),
            "customer_unit_price": unit_price, "requirements": resolved["requirements"], "source_resolution": resolved, "customer_check": customer_check,
            "traceability": traceability, "price_readiness": readiness,
            "model_search": model, "supplier_market_search": supplier,
            "supplier_target_discount_percent": SUPPLIER_TARGET_DISCOUNT_PERCENT,
            "supplier_target_price": target, "calculator": {"status": "🟡 РУЧНАЯ ПРОВЕРКА",
                                                               "purchase_price": purchase_price,
                                                               "unknown_costs": ["delivery_cost"]},
            "warnings": [x for x in (traceability.get("traceability_reason"),
                         MODEL_NOT_LIVE if model.get("status") == MODEL_NOT_LIVE else None,
                         SUPPLIER_NOT_LIVE if supplier["status"] == SUPPLIER_NOT_LIVE else None) if x],
            "status": "completed",
            "procurement": procurement or {"id": trade_number, "trade_number": trade_number,
                                            "nmck": None, "commission_fee": None,
                                            "commission_source": "raw.lot.commissionFee"},
            "item": {"position_number": item_number, "quantity": quantity,
                     "customer_unit_price": unit_price, "item_name": model_input.get("item_name")},
            "supplier_search": {"ranked_offers": supplier.get("offers") or []},
            "audit": {"additional_expense_state": {"ready": False, "reason": "Дополнительные расходы не проверены"}},
            "calculation_complete": False}
    business = attach_business_decision(result)
    result["business_decision"] = business
    result["calculator"]["status"] = business["label"]
    return result


def run_batch(fixtures: list[dict], *, resume: bool = False, checkpoint_path: Path = CHECKPOINT,
              output_dir: Path = OUTPUT_DIR, summary_path: Path = SUMMARY_PATH,
              log_path: Path = LOG_PATH, faults: dict[str, str] | None = None,
              customer_builder: Callable[..., dict] = build_customer_check,
              model_live_test: bool = False, model_provider: Any = None) -> dict:
    logger = _logger(log_path); faults = faults or {}
    fixtures = prioritize_fixtures(fixtures)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if resume and checkpoint_path.exists() else {"version":1,"procurements":{}}
    results=[]; skipped=0
    for fixture in fixtures:
        raw = fixture.get("raw") or fixture; lot = _lot(fixture)
        pid = str(raw.get("id") or fixture.get("purchase_id") or lot.get("id"))
        trade = str(raw.get("tradeNumber") or lot.get("tradeNumber") or pid)
        old = checkpoint["procurements"].get(pid) or {}
        if resume and old.get("status") == "completed" and all(
            x.get("source_resolution", {}).get("source_resolution_version") == SOURCE_VERSION
            and x.get("price_readiness", {}).get("price_readiness_version") == PRICE_READINESS_VERSION
            and isinstance(x.get("business_decision"), dict)
            for x in old.get("result", {}).get("items", [])):
            skipped += 1; results.append(old["result"]); logger.info("procurement=%s stage=batch status=skipped_completed",pid); continue
        state={"procurement_id":pid,"status":"processing","started_at":old.get("started_at") or _now(),
               "completed_at":None,"current_stage":"filter","processed_items":old.get("processed_items",[]),
               "failed_items":old.get("failed_items",[]),"warnings":old.get("warnings",[]),"last_error":None,
               "stage_statuses":old.get("stage_statuses",{}),"stage_results":old.get("stage_results",{})}
        checkpoint["procurements"][pid]=state; _atomic_json(checkpoint_path,checkpoint)
        try:
            if faults.get(pid)=="procurement": raise RuntimeError("synthetic procurement failure")
            decision=filter_purchase_v2(lot,fixture.get("purchase_type_title") or raw.get("purchaseTypeTitle"))
            state["current_stage"]="documents"; state["stage_statuses"]["documents"]="processing"; _atomic_json(checkpoint_path,checkpoint)
            if resume and state["stage_statuses"].get("text_extraction")=="completed" and state["stage_results"].get("document_processing"):
                extraction=state["stage_results"]["document_processing"]
            else:
                extraction=process_procurement_documents(fixture,fixture.get("documents") or [],logger=logger)
                state["stage_results"]["document_processing"]=extraction
            document_stop = document_processing_stop_reason(extraction)
            if document_stop:
                state["stage_statuses"]["documents"]="blocked"
                state["stage_statuses"]["text_extraction"]="blocked"
                state["stage_statuses"]["ocr"]="blocked" if extraction["extraction_summary"]["ocr_documents"] else "not_required"
                state["current_stage"]="stopped_manual_review"
                result={"procurement_id":pid,"procurement_number":trade,
                        "filter":decision,"document_processing":extraction,
                        "items":[],"total_items":0,"completed_items":0,
                        "partial_items":0,"failed_items":0,
                        "warnings":list(dict.fromkeys([*state["warnings"], document_stop])),
                        "final_status":"blocked","manual_stop_reason":document_stop,
                        "started_at":state["started_at"],"completed_at":_now()}
                state.update({"status":"blocked","completed_at":result["completed_at"],
                              "last_error":document_stop,"result":result})
                results.append(result)
                logger.error("procurement=%s stage=documents status=blocked reason=%s",pid,document_stop)
                _atomic_json(checkpoint_path,checkpoint)
                continue
            state["stage_statuses"]["documents"]="completed"
            state["stage_statuses"]["text_extraction"]="completed"
            state["stage_statuses"]["ocr"]="completed" if extraction["extraction_summary"]["ocr_documents"] else "not_required"
            state["current_stage"]="customer_check"
            customer_docs=[{"text":x.get("text") or ""} for x in extraction["document_results"]]
            customer=customer_builder(fixture,customer_docs)
            state["stage_statuses"]["customer_check"]="completed"; state["stage_results"]["customer_check"]=customer
            state["current_stage"]="procurement_audit"; _atomic_json(checkpoint_path,checkpoint)
            audit=audit_from_extraction(fixture,extraction)
            state["stage_statuses"]["procurement_audit"]="completed"
            state["stage_statuses"]["items_extracted"]="completed"
            state["stage_results"]["procurement_audit"]=audit; _atomic_json(checkpoint_path,checkpoint)
            item_results=[]; existing={x["item_number"]:x for x in old.get("result",{}).get("items",[])
                if x.get("status")=="completed"
                and x.get("source_resolution", {}).get("source_resolution_version") == SOURCE_VERSION
                and x.get("price_readiness", {}).get("price_readiness_version") == PRICE_READINESS_VERSION}
            audit_items={x["item_number"]:x for x in audit.get("items") or []}
            for number,item in enumerate(lot.get("lotItems") or [],1):
                if resume and number in existing:
                    item_results.append(existing[number]); continue
                state["current_stage"]=f"item_{number}"; _atomic_json(checkpoint_path,checkpoint)
                try:
                    state["current_stage"]="model_discovery"; state["stage_statuses"]["model_discovery"]="processing"; _atomic_json(checkpoint_path,checkpoint)
                    result=process_item(item,number,trade,customer,
                                        (audit_items.get(number) or {}).get("requirements") or [],
                                        "item" if faults.get(f"{pid}:{number}")=="item" else None,
                                        model_live=model_live_test, model_provider=model_provider, logger=logger,
                                        source_resolution=audit_items.get(number),
                                        procurement={"id": pid, "trade_number": trade,
                                                     "nmck": lot.get("price"),
                                                     "commission_fee": lot.get("commissionFee"),
                                                     "commission_source": "raw.lot.commissionFee",
                                                     "commission_verified": lot.get("commissionFee") is not None})
                    state["stage_statuses"]["model_discovery"]=(
                        "completed" if result["model_search"].get("model_discovery_called")
                        else "not_required" if result["price_readiness"]["classification"] == PRICE_SEARCH_READY
                        else "not_requested"
                    )
                    item_results.append(result)
                    if number not in state["processed_items"]: state["processed_items"].append(number)
                    logger.info("procurement=%s item=%s stage=item status=completed",pid,number)
                except Exception as exc:
                    error=_safe_text(exc); item_results.append({"item_number":number,"status":"failed","last_error":error})
                    if number not in state["failed_items"]: state["failed_items"].append(number)
                    state["last_error"]=error; logger.error("procurement=%s item=%s stage=item status=failed error=%s",pid,number,error)
                state["result"]={"items":item_results}; _atomic_json(checkpoint_path,checkpoint)
            failed=sum(x.get("status")=="failed" for x in item_results); completed=len(item_results)-failed
            status="partial" if failed and completed else "failed" if failed else "completed"
            state["stage_statuses"]["traceability"]="completed"
            result={"procurement_id":pid,"procurement_number":trade,"filter":decision,
                    "processing_queue": procurement_queue(fixture, list(audit_items.values())),
                    "customer_check":customer,"document_processing":extraction,
                    "procurement_audit":audit,"items":item_results,
                    "total_items":len(item_results),"completed_items":completed,"partial_items":failed,
                    "failed_items":failed,"warnings":list(dict.fromkeys(state["warnings"])),
                    "final_status":status,"started_at":state["started_at"],"completed_at":_now()}
            state.update({"status":status,"completed_at":result["completed_at"],"current_stage":"completed","result":result})
            results.append(result)
        except Exception as exc:
            error=_safe_text(exc); state.update({"status":"failed","completed_at":_now(),"last_error":error})
            state["result"]={"procurement_id":pid,"procurement_number":trade,"items":[],"final_status":"failed","last_error":error}
            results.append(state["result"]); logger.error("procurement=%s stage=procurement status=failed error=%s",pid,error)
        _atomic_json(checkpoint_path,checkpoint)
    summary={"total_procurements":len(results),"skipped_completed_on_resume":skipped,
             "completed_procurements":sum(x.get("final_status")=="completed" for x in results),
             "partial_procurements":sum(x.get("final_status")=="partial" for x in results),
             "failed_procurements":sum(x.get("final_status")=="failed" for x in results),
             "blocked_procurements":sum(x.get("final_status")=="blocked" for x in results),
             "total_items":sum(x.get("total_items",0) for x in results),
             "completed_items":sum(x.get("completed_items",0) for x in results),
             "failed_items":sum(x.get("failed_items",0) for x in results),
             "documents_found":sum((x.get("document_processing") or {}).get("documents_found",0) for x in results),
             "documents_processed":sum((x.get("document_processing") or {}).get("documents_processed",0) for x in results),
             "documents_failed":sum((x.get("document_processing") or {}).get("documents_failed",0) for x in results),
             "ocr_documents":sum((x.get("document_processing") or {}).get("extraction_summary",{}).get("ocr_documents",0) for x in results),
             "mixed_documents":sum((x.get("document_processing") or {}).get("extraction_summary",{}).get("mixed_documents",0) for x in results),
             "items_with_requirements":sum(sum(bool(i.get("requirements")) for i in x.get("items",[])) for x in results),
             "live_eat_called":False,"live_model_search_called":model_live_test,
             "live_model_queries":sum(sum((i.get("model_search") or {}).get("live_queries_count",0) for i in x.get("items",[])) for x in results),
             "live_supplier_search_called":False,"google_sheets_written":False}
    output={"mode":"production_dry_run_local_fixtures","started_at":_now(),"procurements":results,"summary":summary,
            "checkpoint_path":str(checkpoint_path),"summary_path":str(summary_path)}
    output_dir.mkdir(parents=True,exist_ok=True)
    run_path=output_dir/f"run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"; _atomic_json(run_path,output)
    summary_path.parent.mkdir(parents=True,exist_ok=True)
    procurement_lines=[]
    for result in results:
        doc=result.get("document_processing") or {}; ext=doc.get("extraction_summary") or {}; audit=result.get("procurement_audit") or {}
        procurement_lines.append(
            f"- {result.get('procurement_number')}: статус {result.get('final_status')}, "
            f"документов {doc.get('documents_found',0)}, "
            f"обработано {doc.get('documents_processed',0)}, OCR {ext.get('ocr_documents',0)}, "
            f"failed {doc.get('documents_failed',0)}, audit {audit.get('audit_status','Нет данных')}, "
            f"позиций {result.get('total_items',0)}, requirements {sum(bool(i.get('requirements')) for i in result.get('items',[]))}, "
            f"warnings {len((audit.get('document_warnings') or []))}"
            + (f", комментарий: {result.get('manual_stop_reason')}"
               if result.get('manual_stop_reason') else "")
        )
    summary_path.write_text("# Production dry-run\n\n"+"\n".join([
        f"- Закупок: {summary['total_procurements']}",f"- Позиций: {summary['total_items']}",
        f"- Документов: {summary['documents_found']}; обработано: {summary['documents_processed']}; OCR: {summary['ocr_documents']}; mixed: {summary['mixed_documents']}; failed: {summary['documents_failed']}",
        f"- Позиций с requirements: {summary['items_with_requirements']}",
        f"- Завершено закупок: {summary['completed_procurements']}",f"- Частично: {summary['partial_procurements']}",
        f"- Остановлено для ручной обработки: {summary['blocked_procurements']}",
        f"- Ошибок закупок: {summary['failed_procurements']}",f"- Пропущено при resume: {summary['skipped_completed_on_resume']}",
        "- Live ЕАТ: не запускался",f"- Live model search: {'ограниченный контрольный запуск' if model_live_test else 'не запускался'}",
        "- Live supplier search: не запускался","- Google Sheets: запись не выполнялась",
        "","## По закупкам",*procurement_lines,
    ])+"\n",encoding="utf-8")
    output["run_path"]=str(run_path)
    return output


def run_local_dry_run(resume: bool = False, model_live_test: bool = False) -> dict:
    fixtures=load_local_fixtures()
    if model_live_test:
        # Explicitly bounded development allow-list: two known local control procurements.
        allowed={"100205573126100053","100250237126100180"}
        fixtures=[x for x in fixtures if str((x.get("raw") or x).get("tradeNumber")) in allowed][:2]
    return run_batch(fixtures,resume=resume,model_live_test=model_live_test)
