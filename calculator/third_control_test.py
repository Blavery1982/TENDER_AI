"""Точечный тест экономики кондиционеров без обращений к внешним системам."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from calculator.calculator import scenario
from calculator.result_decision import attach_business_decision
from calculator.schemas import analyze_costs

ROOT=Path(__file__).resolve().parent.parent
SUPPLIER_RESULT=ROOT/"data/supplier_search_third_test.json"
EAT_CARD=ROOT/"data/eat_single_31caee8a-cca2-4e2b-b773-42229d413d03.json"
OUTPUT=ROOT/"data/calculator_third_test.json"


def run_test():
    supplier=json.loads(SUPPLIER_RESULT.read_text(encoding="utf-8"))
    card=json.loads(EAT_CARD.read_text(encoding="utf-8")); lot=(card.get("raw") or {}).get("lot") or {}
    nmck=76200.0; quantity=3; customer_unit_price=25400.0
    real_costs={"delivery_cost":None,"logistics_cost":0,"unloading_cost":0,
                "assembly_cost":0,"installation_cost":0,
                "packaging_removal_cost":0,"other_costs":0}
    cost_state=analyze_costs(real_costs)
    purchase_price=None
    scenarios=[scenario(value,quantity,nmck,{key:(0 if val is None else val)
                                            for key,val in real_costs.items()})
               for value in (19000,20000,21000,22000)]
    decision_payload={"procurement":{"nmck":nmck,"commission_fee":lot.get("commissionFee"),
                                      "commission_source":"raw.lot.commissionFee"},
                      "item":{"position_number":1,"quantity":quantity},
                      "supplier_search":{"ranked_offers":[]},
                      "costs":cost_state,"calculation_complete":False}
    decision=attach_business_decision(decision_payload)
    result={"procurement_number":"100205573126100053",
            "subject":"Поставка климатического оборудования",
            "calculated_at":datetime.now(timezone.utc).isoformat(),"nmck":nmck,
            "quantity":quantity,"customer_unit_price":customer_unit_price,
            "selected_model":"Royal Clima RC-TWN28HN",
            "public_price_references":[19900,26490],
            "purchase_price":purchase_price,"purchase_price_status":"Не подтверждена",
            **cost_state,"commission":lot.get("commissionFee"),
            "tax_logic":"max(цена подачи − закупочная стоимость − логистика − дополнительные расходы, 0) × 15%; комиссия не уменьшает налоговую базу",
            "scenario_assumptions":"Только для проверки математики: доставка и прочие расходы приняты равными 0 ₽; это не реальные данные.",
            "scenario_results":scenarios,"final_decision":decision["label"],
            "decision_reason":"; ".join(decision["decision_reasons"]),
            "break_even_real":"Нет данных — требуется закупочная цена"}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__": print(json.dumps(run_test(),ensure_ascii=False,indent=2))
