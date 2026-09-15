"""Третий точечный procurement_audit + model_search без повторного сбора ЕАТ."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from documents.procurement_audit import extract_document_with_evidence
from model_search.ranking import procurement_business_status

ROOT = Path(__file__).resolve().parent.parent
PURCHASE_ID = "31caee8a-cca2-4e2b-b773-42229d413d03"
CARD = ROOT / f"data/eat_single_{PURCHASE_ID}.json"
DOCS = ROOT / f"data/tenders/{PURCHASE_ID}"
OUTPUT = ROOT / "data/procurement_model_search_third_test.json"

ROYAL = "https://royal.com.ru/catalog/nastennye_konditsionery/royal_clima_rc_twn28hn/"
ROYAL_MANUAL = "https://mcgrp.ru/files/viewer/848759/1"
ROYAL_FUNCTIONS = "https://spb.technopark.ru/konditsionery-royalclima-rc-twn28hninrc-twn28hnout/description/"
ROYAL_PRICE = ROYAL
ROYAL_LOW_PRICE = "https://kazan.pvhmir.ru/royal-clima-rc-twx25hn"
GLORIA = "https://royal.com.ru/catalog/nastennye_konditsionery/royal_clima_rc_gl28hn/"
BALLU = "https://ballu.group/product/ballu-bso-09hn8_22y/"


def requirement(parameter, value):
    return {"parameter": parameter, "required_value": value, "source": "Требования + проект ГК.docx, Приложение №1"}


def check(parameter, value, result, source, evidence):
    return {"parameter": parameter, "model_value": value, "result": result, "source": source, "evidence": evidence}


def evaluated_model(brand, model, checks, production, availability, price=None, price_source=None):
    mismatch=[x["parameter"] for x in checks if x["result"]=="does_not_comply"]
    unknown=[x["parameter"] for x in checks if x["result"]=="not_confirmed"]
    status="non_compliant" if mismatch else "not_confirmed" if unknown else "fully_compliant"
    return {"brand":brand,"model":model,"technical_status":status,"parameter_check":checks,
            "mismatched_parameters":mismatch,"unconfirmed_parameters":unknown,
            "production_status":production,"russia_availability":availability,
            "russian_market_price_rub":price,"price_source":price_source,
            "market_price":price,"market_price_source":price_source,
            "price_discovery_status":"completed" if status=="fully_compliant" else "not_started_technical_check_failed"}


def run_test():
    card=json.loads(CARD.read_text(encoding="utf-8")); raw=card["raw"]; lot=raw["lot"]; item=lot["lotItems"][0]
    documents=[]
    for path in sorted(DOCS.iterdir()):
        x=extract_document_with_evidence(path)
        documents.append({"file_name":path.name, **x})
    justification=next(x for x in documents if x["file_name"].startswith("Обоснование"))
    contract=next(x for x in documents if x["file_name"].startswith("Требования"))
    requirements=[
        requirement("Инверторный тип", "Нет"), requirement("Энергоэффективность при нагреве", "не ниже A"),
        requirement("Энергоэффективность при охлаждении", "не ниже A"), requirement("Фильтр тонкой очистки", "Да"),
        requirement("Дополнительные функции", "Автоочистка; авторежим; ночной; турбо; самодиагностика"),
        requirement("Тип внутреннего блока", "Настенный"), requirement("Вид кондиционера", "Сплит-система"),
        requirement("Наружный блок", "Да"), requirement("Антибактериальный фильтр", "Да"),
        requirement("Мощность охлаждения", "≥ 2,6 кВт"), requirement("Мощность нагрева", "≥ 2,6 кВт"),
        requirement("Площадь", "≥ 25 м²"), requirement("Цвет", "Белый"),
    ]
    rc=[
        check("Инверторный тип","Нет","complies",ROYAL,"Официальная карточка: инверторная технология — нет."),
        check("Энергоэффективность при нагреве","A (COP 3,74)","complies",ROYAL,"Официальная карточка подтверждает класс A и COP 3,74."),
        check("Энергоэффективность при охлаждении","A (EER 3,21)","complies",ROYAL,"Официальная карточка подтверждает класс A и EER 3,21."),
        check("Фильтр тонкой очистки","Active Carbone и Silver Ion","complies",ROYAL,"Официально указана двойная очистка воздуха."),
        check("Дополнительные функции","Самоочистка, AUTO, ночной, TURBO, самодиагностика","complies",ROYAL_MANUAL,"Руководство модели и карточка функций подтверждают весь набор."),
        check("Тип внутреннего блока","Настенный","complies",ROYAL,"Официально: настенный кондиционер."),
        check("Вид кондиционера","Сплит-система","complies",ROYAL,"Официально: сплит-система."),
        check("Наружный блок","RC-TWN28HN/OUT","complies",ROYAL_MANUAL,"Руководство перечисляет внутренний и наружный блоки."),
        check("Антибактериальный фильтр","Silver Ion","complies",ROYAL,"Фильтр с ионами серебра подавляет бактерии и микробы."),
        check("Мощность охлаждения","2,85 кВт","complies",ROYAL,"Официальное значение 2,85 кВт."),
        check("Мощность нагрева","2,92 кВт","complies",ROYAL,"Официальное значение 2,92 кВт."),
        check("Площадь","до 28,5 м²","complies",ROYAL,"Официально указана площадь до 28,5 м²."),
        check("Цвет","Белый","complies",ROYAL_FUNCTIONS,"Российская карточка модели подтверждает белый цвет."),
    ]
    gloria=[check(r["parameter"],None,"not_confirmed",GLORIA,"Не удалось подтвердить весь набор функций в одном официальном документе.") for r in requirements]
    for name,value in {"Инверторный тип":"Нет","Энергоэффективность при нагреве":"A","Энергоэффективность при охлаждении":"A","Тип внутреннего блока":"Настенный","Вид кондиционера":"Сплит-система","Наружный блок":"Да","Мощность охлаждения":"2,73 кВт","Мощность нагрева":"2,92 кВт","Площадь":"до 28 м²","Цвет":"Белый"}.items():
        x=next(c for c in gloria if c["parameter"]==name); x.update(model_value=value,result="complies",evidence="Подтверждено официальной карточкой.")
    ballu=[check(r["parameter"],None,"not_confirmed",BALLU,"Параметр не подтверждён официальной карточкой.") for r in requirements]
    for name,value in {"Инверторный тип":"Нет","Энергоэффективность при нагреве":"A","Энергоэффективность при охлаждении":"A","Тип внутреннего блока":"Настенный","Вид кондиционера":"Сплит-система","Наружный блок":"Да","Мощность охлаждения":"2,64 кВт","Мощность нагрева":"2,64 кВт","Площадь":"до 29 м²","Цвет":"Белый"}.items():
        x=next(c for c in ballu if c["parameter"]==name); x.update(model_value=value,result="complies",evidence="Подтверждено карточкой официального дилера.")
    candidates=[
        evaluated_model("Royal Clima","RC-TWN28HN",rc,"in_production","available",21000,ROYAL_LOW_PRICE),
        evaluated_model("Royal Clima","RC-GL28HN",gloria,"in_production","available"),
        evaluated_model("Ballu","BSO-09HN8_22Y",ballu,"production_not_confirmed","available"),
    ]
    threshold=round(item["unitPrice"]*.8,2)
    for candidate in candidates:
        if candidate["technical_status"]=="fully_compliant":
            candidate["public_price_reference"] = candidate["russian_market_price_rub"]
            candidate["public_price_source"] = candidate["price_source"]
            candidate["purchase_price"] = None
            candidate["price_check_status"] = "public_price_reference_only"
            candidate["indicative_20_percent_threshold"] = candidate["russian_market_price_rub"] <= threshold
        else: candidate["price_check_status"]="not_run_due_to_technical_result"
    prices=[(26577,3,79731),(25400,3,76200),(25999,3,77997)]
    arithmetic=[{"unit_price":u,"quantity":q,"stated_total":s,"calculated_total":u*q,"matches":u*q==s} for u,q,s in prices]
    model_in_justification=None
    special=[{"type":"supplier_delivery","document":contract["file_name"],"evidence":"Доставка товара до места поставки осуществляется транспортом и силами Поставщика.","confidence":"high"}]
    excluded=[{"type":"unloading","reason":"Отрицательная обязанность поставщика","evidence":"Разгрузка осуществляется силами Заказчика."}]
    coverage={"candidates_checked":len(candidates),"brands_checked":len({c['brand'] for c in candidates}),
              "sufficiently_broad":False,"reason_ru":"Проверены только три кандидата двух брендов; рынок недорогих моделей исследован недостаточно широко."}
    business=procurement_business_status(candidates,item["unitPrice"],coverage)
    admitted=business["models_for_supplier_search"]
    result={"procurement_id":PURCHASE_ID,"tradeNumber":raw["tradeNumber"],"subject":lot["subject"],"analyzed_at":datetime.now(timezone.utc).isoformat(),
            "nmck_rub":lot["price"],"documents":documents,"positions":[{"position_number":1,"name":item["name"],"quantity":item["quantity"],"unit_price_rub":item["unitPrice"],"sum_rub":item["sum"],"price_threshold_80_percent_rub":threshold,"requirements":requirements,"requirement_count":len(requirements),"candidate_models":candidates,"admitted_models":admitted}],
            "price_justification":{"model_specified":False,"model":model_in_justification,"offers":arithmetic,"stated_selected_total_rub":76200,"nmck_supported":all(x["matches"] for x in arithmetic) and lot["price"]==76200,"issues":[]},
            "cross_document_checks":{"eat_quantity":3,"tz_quantity":3,"justification_quantities":[3,3,3],"eat_unit_price":25400,"selected_justification_unit_price":25400,"eat_sum":76200,"all_consistent":True},
            "special_conditions_short":"Доставка транспортом и силами поставщика","special_conditions_evidence":special,"conditions_not_assigned_to_supplier":excluded,
            "customer_document_issues":[],**business,
            "business_result":business["business_status"],
            "business_reason":"Найдена полностью соответствующая, актуальная и доступная в РФ модель. 21 000 ₽ — публичный ценовой ориентир, а реальную закупочную цену должен определить будущий supplier_search."}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); return result

if __name__=="__main__": print(json.dumps(run_test(),ensure_ascii=False,indent=2))
