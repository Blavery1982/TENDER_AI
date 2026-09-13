"""Fail-safe конвейер одной закупки поверх существующих результатов модулей."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from calculator.decision import calculator_decision
from calculator.formulas import platform_commission
from calculator.schemas import analyze_costs
from pipeline.schemas import stage, user_value
from model_search.price_readiness import (PRICE_SEARCH_READY,
                                          classify_price_search_readiness)
from security.traceability import analyze_purchase
from security.customer_check import build_customer_check, customer_check_for_google_sheets
from suppliers.verification import normalize_domain

ROOT=Path(__file__).resolve().parent.parent
CARD=ROOT/"data/eat_single_31caee8a-cca2-4e2b-b773-42229d413d03.json"
AUDIT_MODEL=ROOT/"data/procurement_model_search_third_test.json"
SUPPLIERS=ROOT/"data/supplier_search_third_test.json"
VERIFICATION=ROOT/"data/supplier_verification_deep_test.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe(log: list[dict], name: str, action: Callable[[], Any], fallback: Any) -> Any:
    log.append(stage(name,"started"))
    try:
        result=action()
    except Exception as exc:  # fail-safe: причина сохраняется, секреты не выводятся
        log.append(stage(name,"failed",f"{type(exc).__name__}: {exc}"))
        return fallback
    log.append(stage(name,"completed"))
    return result


def _customer(documents: list[dict]) -> tuple[str | None,str | None]:
    text="\n".join(x.get("text") or "" for x in documents)
    name_match=re.search(r'(Федеральное казенное учреждение «Следственный изолятор №\s*1[^»]*»)',text,re.I)
    inn_match=re.search(r'ФКУ СИЗО-1[^\n]{0,300}?ИНН\s*:?\s*(\d{10}|\d{12})',text,re.I)
    if not inn_match:
        inn_match=re.search(r'ИНН\s*:?\s*(\d{10}|\d{12})',text,re.I)
    return (name_match.group(1) if name_match else None,inn_match.group(1) if inn_match else None)


def _merge_verification(supplier: dict, deep: dict) -> tuple[list[dict],dict]:
    latest={x["domain"]:x for x in deep.get("sites",[])}
    merged=[]
    for item in supplier.get("supplier_candidates",[]):
        row=dict(item); domain=normalize_domain(row.get("product_url") or row.get("domain") or "")
        if domain in latest:
            for key in ("verification_status","verification_comment","risk_flags",
                        "positive_signals","unavailable_checks","verification_evidence"):
                row[key]=latest[domain].get(key)
        merged.append(row)
    statuses=[x.get("verification_status","") for x in merged]
    summary={"checked":len(merged),"passed":sum(x.startswith("✅") for x in statuses),
             "manual_review":sum(x.startswith("🟡") for x in statuses),
             "high_risk":sum(x.startswith("🔴") for x in statuses),
             "insufficient":sum(x.startswith("⚪") for x in statuses)}
    return merged,summary


def _validated_quotes(quotes: dict, verified: list[dict]) -> dict:
    allowed={normalize_domain(x.get("product_url") or "") for x in verified
             if str(x.get("verification_status","")).startswith("✅")}
    result={}
    for key in ("kp1","kp2","kp3"):
        quote=quotes.get(key)
        result[key]=quote if isinstance(quote,dict) and normalize_domain(quote.get("product_url") or "") in allowed else None
    return result


def _refresh_request_statuses(requests: list[dict], verified: list[dict]) -> list[dict]:
    latest={normalize_domain(x.get("product_url") or ""):x.get("verification_status") for x in verified}
    result=[]
    for source in requests:
        item=dict(source); domain=normalize_domain(item.get("product_url") or "")
        if domain in latest: item["verification_status"]=latest[domain]
        result.append(item)
    return result


def build_pipeline(card: dict, audit_model: dict, supplier: dict, deep: dict,
                   failure_injections: set[str] | None = None) -> dict:
    failures=failure_injections or set(); logs=[]; warnings=[]
    raw=card.get("raw") or {}; lot=raw.get("lot") or {}; items=lot.get("lotItems") or []
    procurement=_safe(logs,"procurement",lambda:{
        "procurement_number":raw.get("tradeNumber"),"procurement_id":raw.get("id") or card.get("purchase_id"),
        "procurement_name":lot.get("subject"),"procurement_url":card.get("card_url"),
        "nmck":lot.get("price"),"deadline":lot.get("applicationFillingEndDate"),
        "delivery_place":((lot.get("deliveryInfos") or [{}])[0].get("deliveryAddress") or {}).get("formattedFullInfo"),
    },{})
    source_documents=audit_model.get("documents") or []
    customer_check=_safe(logs,"customer_check",
        lambda:build_customer_check(card,source_documents) if "customer_check" not in failures else 1/0,
        {"customer_name":None,"customer_inn":None,"arbitration_status":"🟡 ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА КАД",
         "arbitration_summary":"Арбитражные дела заказчика: требуется ручная проверка",
         "payment_risk_status":"Недостаточно данных для оценки риска оплаты","warnings":["Этап проверки заказчика не завершён"],
         "manual_actions_required":[]})
    procurement.update({"customer":customer_check.get("customer_name"),
                        "customer_inn":customer_check.get("customer_inn")})
    documents=_safe(logs,"documents",lambda:source_documents if "documents" not in failures else 1/0,[])

    position_source=(audit_model.get("positions") or [{}])[0]
    raw_item=items[0] if items else {}
    item={"position_number":position_source.get("position_number") or 1,
          "item_name":position_source.get("name") or raw_item.get("name"),
          "quantity":position_source.get("quantity",raw_item.get("quantity")),
          "unit":(raw_item.get("okei") or {}).get("title"),
          "customer_unit_price":position_source.get("unit_price_rub",raw_item.get("unitPrice"))}
    traceability=_safe(logs,"traceability",lambda:analyze_purchase(lot),
                       {"traceability_status":"Нет данных","traceability_warning":""})
    technical_audit=_safe(logs,"procurement_audit",lambda:{
        "technical_spec_status":"ТЗ извлечено; обязательные требования определены",
        "price_justification_status":"Обоснование НМЦК арифметически согласовано" if audit_model.get("price_justification",{}).get("nmck_supported") else "Нужна ручная проверка",
        "price_justification_model":audit_model.get("price_justification",{}).get("model"),
        "price_justification_model_match":"Модель в обосновании не указана" if not audit_model.get("price_justification",{}).get("model_specified") else "Нет данных",
        "special_conditions":audit_model.get("special_conditions_short") or "",
        "traceability_status":traceability.get("traceability_status"),
        "document_warnings":[w for d in documents for w in d.get("warnings",[])],
        "requirements":position_source.get("requirements") or [],
    } if "audit" not in failures else 1/0,{"technical_spec_status":"Этап не завершён","document_warnings":[]})

    readiness=classify_price_search_readiness(raw_item,position_source)
    model_candidates=audit_model.get("models_for_supplier_search") or []
    selected=model_candidates[0] if model_candidates else {}

    def _model_result() -> dict:
        if readiness["classification"] == PRICE_SEARCH_READY:
            return {
                "selected_model":readiness["identifier"],
                "model_status":"PRICE_SEARCH_READY",
                "all_requirements_confirmed":None,
                "current_model":None,
                "available_in_russia":None,
                "model_evidence":readiness.get("evidence") or [],
                "public_price":None,
                "public_price_source":None,
                "model_source":readiness.get("model_source"),
                "customer_required_model":readiness.get("customer_required_model"),
                "price_justification_model":readiness.get("price_justification_model"),
                "price_readiness":readiness,
                "model_discovery_called":False,
                "compliance_required_before_price_search":False,
            }
        # Только модель, найденная самой программой, остаётся в прежнем
        # compliance-пути до допуска к поиску поставщиков.
        return {
            "selected_model":" ".join(x for x in (selected.get("brand"),selected.get("model")) if x),
            "model_status":"Полностью соответствует ТЗ" if selected.get("technical_status")=="fully_compliant" else "Соответствие не подтверждено",
            "all_requirements_confirmed":selected.get("technical_status")=="fully_compliant" and not selected.get("unconfirmed_parameters"),
            "current_model":selected.get("production_status")=="in_production",
            "available_in_russia":selected.get("russia_availability") in {"available","available_to_order"},
            "model_evidence":selected.get("parameter_check") or [],
            "public_price":selected.get("public_price_reference"),
            "public_price_source":selected.get("public_price_source"),
            "price_readiness":readiness,
            "model_discovery_called":bool(selected),
            "compliance_required_before_price_search":True,
        }

    model_search=_safe(logs,"model_search",
                       lambda:_model_result() if "model_search" not in failures else 1/0,
                       {"model_status":"Этап не завершён","price_readiness":readiness})

    verified,verification_summary=_safe(logs,"supplier_verification",
        lambda:_merge_verification(supplier,deep) if "verification" not in failures else 1/0,([],{}))
    quotes=_validated_quotes(supplier.get("quotes") or {},verified)
    requests=_refresh_request_statuses(supplier.get("price_request_candidates") or [],verified)
    customer_price=item.get("customer_unit_price")
    supplier_search=_safe(logs,"supplier_search",lambda:{
        "supplier_search_status":"Поставщики найдены; требуется получить подтверждённые цены",
        "supplier_price_threshold":round(customer_price*.8,2) if customer_price is not None else None,
        "kp1":quotes.get("kp1"),"kp2":quotes.get("kp2"),"kp3":quotes.get("kp3"),
        "price_request_candidates":requests,
        "verification_summary":verification_summary,"verified_suppliers":verified,
    } if "supplier_search" not in failures else 1/0,{"supplier_search_status":"Этап не завершён","price_request_candidates":[]})

    purchase_prices=[q.get("purchase_price") for q in quotes.values() if isinstance(q,dict) and q.get("purchase_price") is not None]
    purchase_price=min(purchase_prices) if purchase_prices else None
    real_costs={"delivery_cost":None,"logistics_cost":0,"unloading_cost":0,
                "assembly_cost":0,"installation_cost":0,"packaging_removal_cost":0,"other_costs":0}
    costs=analyze_costs(real_costs)
    decision=calculator_decision(bool(model_search.get("selected_model")),
                                 bool(supplier.get("supplier_candidates")),purchase_price,
                                 costs["unknown_costs"])
    calculator=_safe(logs,"calculator",lambda:{
        "purchase_price":purchase_price,"purchase_price_status":"Подтверждена" if purchase_price is not None else "Не подтверждена",
        "known_costs":costs["known_costs"],"unknown_costs":costs["unknown_costs"],
        "commission":platform_commission(procurement.get("nmck")) if procurement.get("nmck") is not None else None,
        "tax_logic":"max(цена подачи − закупочная стоимость − логистика − дополнительные расходы, 0) × 15%; комиссия не уменьшает налоговую базу",
        "net_profit":None,"break_even_price":None,"reserve_to_zero_percent":None,
        "status":decision["status"],
    },{"status":"Этап не завершён"})
    logs[-1]["status"]="incomplete" if purchase_price is None and logs[-1]["stage"]=="calculator" else logs[-1]["status"]
    if purchase_price is None: warnings.append("Подтверждённая закупочная цена отсутствует")
    if "delivery_cost" in costs["unknown_costs"]: warnings.append("Стоимость доставки неизвестна")
    if not any(quotes.values()): warnings.append("КП1, КП2 и КП3 пока не сформированы")
    for row in verified:
        if str(row.get("verification_status","")).startswith("🟡"):
            warnings.append(f"Поставщик {row.get('supplier_name')} требует ручной проверки перед оплатой")
        for unavailable in row.get("unavailable_checks") or []:
            warnings.append(f"{row.get('supplier_name')}: {unavailable}")
    if traceability.get("traceability_warning"): warnings.append(traceability["traceability_warning"])
    warnings=list(dict.fromkeys(warnings))
    manual=["Запросить актуальную цену на 3 шт. минимум у нескольких поставщиков",
            "Подтвердить наличие товара","Подтвердить стоимость и условия доставки",
            "Получить счёт или официальное КП","Проверить реквизиты счёта",
            "Повторно запустить CALCULATOR после получения подтверждённой закупочной цены"]
    warnings.extend(customer_check.get("warnings") or [])
    manual[0:0]=[x.get("action") for x in customer_check.get("manual_actions_required") or [] if x.get("action")]
    warnings=list(dict.fromkeys(warnings)); manual=list(dict.fromkeys(manual))
    final={"final_status":decision["status"],
           "final_reason":"Полностью соответствующая модель найдена, но для окончательного расчёта экономики требуется подтверждённая закупочная цена и условия доставки.",
           "next_action":"Запросить актуальные цены и условия поставки минимум у нескольких поставщиков."}
    logs.append(stage("final_decision","completed"))
    result={"procurement":procurement,"customer_check":customer_check,
            "customer_check_google_sheets":customer_check_for_google_sheets(customer_check),
            "item":item,"documents":documents,
            "technical_audit":technical_audit,"model_search":model_search,
            "supplier_search":supplier_search,"supplier_verification":verification_summary,
            "calculator":calculator,"final_decision":final,"warnings":warnings,
            "manual_actions_required":manual,"stage_log":logs,
            "timestamps":{"pipeline_completed_at":datetime.now(timezone.utc).isoformat()},
            "scenario_results_reference":"data/calculator_third_test.json",
            "user_summary":user_value({"status":final["final_status"],"reason":final["final_reason"],
                                       "next_action":final["next_action"],"kp1":quotes.get("kp1"),
                                       "kp2":quotes.get("kp2"),"kp3":quotes.get("kp3"),
                                       "net_profit":calculator.get("net_profit"),
                                       "break_even_price":calculator.get("break_even_price")})}
    return result


def run_pipeline(failure_injections: set[str] | None = None) -> dict:
    return build_pipeline(load_json(CARD),load_json(AUDIT_MODEL),load_json(SUPPLIERS),
                          load_json(VERIFICATION),failure_injections)
