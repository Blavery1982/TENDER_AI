"""Воспроизводимый тест внешнего поиска для закупки 100309200126100271."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from documents.procurement_audit import run_test as run_audit
from model_search.price_discovery import discover_prices
from model_search.technical_discovery import unique_query_terms, verify_candidates
from model_search.market_eligibility import apply_market_eligibility, market_eligible
from model_search.ranking import rank_models

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/model_search_test.json"
PARKER = "https://www.parker.com/content/dam/Parker-com/Countries-2011/South-Korea/Support/Literature/KFD/%EC%A0%9C%EC%95%BD-%EB%B0%8F-%EB%B0%94%EC%9D%B4%EC%98%A4-%ED%94%84%EB%A1%9C%EC%84%B8%EC%8A%A4-%EC%82%B0%EC%97%85-%ED%95%84%ED%84%B0-%EC%A0%9C%ED%92%88-%EC%B9%B4%ED%83%88%EB%A1%9C%EA%B7%B8_Filtration_%ED%8C%8C%EC%B9%B4%EC%BD%94%EB%A6%AC%EC%95%84_%EC%B9%B4%ED%83%88%EB%A1%9C%EA%B7%B8.pdf"
PARKER_DATASHEET = "https://www.parker.com/content/dam/Parker-com/Literature/Bioscience-Division/FoodBev/UK-Lit/DS_FBG_02_HF_TETPOR_II_1B.pdf"
PARKER_LIFESCIENCE = "https://www.parker.com/content/dam/Parker-com/Literature/Bioscience-Division/LS/Datasheets/High_Flow_Tetpor-II_Air-Gas_Filter_Datasheet.pdf"
PALL = "https://shop.pall.com/us/en/products/zidKA1PFRW1"
PALL_FAMILY = "https://shop.pall.com/us/en/food-beverage/zidimmfdh4o"
SARTORIUS = "https://shop.sartorius.com/ch/p/sartofluor-lg-capsule-02-m-size-5/5181307T5--OO--D"

def _check(parameter, value, result, source):
    return {"parameter": parameter, "model_value": value, "result": result, "source": source}

def candidates_for(position):
    names = [x["parameter"] for x in position["requirements"]]
    find = lambda part: next(x for x in names if part.casefold() in x.casefold())
    common = {"market_price": None, "market_price_source": None}
    if position["item_index"] == 1:
        return [
            {**common, "brand":"Pall", "model":"Emflon PFRW Kleenpak KA1PFRW1", "confidence":"high", "technical_sources":[PALL,PALL_FAMILY], "parameter_check":[
                _check(find("технологических газов"),"Стерильная фильтрация воздуха и газов","complies",PALL),
                _check(find("Мембрана гидрофобная"),"Гидрофобная двухслойная PTFE","complies",PALL),
                _check(find("Эффективная зона"),"0,04 м²","does_not_comply",PALL_FAMILY),
                _check(find("теста о прохождении"),"Заводской тест целостности","complies",PALL)]},
            {**common, "brand":"Sartorius", "model":"Sartofluor LG Capsule 5181307T5--OO--D", "confidence":"high", "technical_sources":[SARTORIUS], "parameter_check":[
                _check(find("технологических газов"),"Стерильная фильтрация газов","complies",SARTORIUS),
                _check(find("Мембрана гидрофобная"),"Гидрофобная PTFE","complies",SARTORIUS),
                _check(find("Эффективная зона"),"0,03 м²","does_not_comply",SARTORIUS)]},
        ]
    return [{**common, "brand":"Parker domnick hunter", "model":"HIGH FLOW TETPOR II ZHFT/A T (уплотнение не определено)", "confidence":"high", "technical_sources":[PARKER,PARKER_DATASHEET,PARKER_LIFESCIENCE], "parameter_check":[
        _check(find("технологических газов"),"Стерилизующий фильтр воздуха/газов","complies",PARKER),
        _check(find("Материал мембраны"),"PTFE","complies",PARKER),
        _check(find("Размер пор"),"0,2 мкм","complies",PARKER),
        _check(find("Высота фильтроэлемента"),"5 дюймов (код A)","complies",PARKER),
        _check(find("Адаптер"),"TRUESEAL (код T)","complies",PARKER),
        _check(find("международным стандартам"),"21 CFR 177; EC1935/2004; USP Class VI; ISO10993","complies",PARKER),
        _check(find("Тест целостности"),"Все картриджи проходят заводской тест целостности методом диффузионного потока и аэрозольного испытания","complies",PARKER_DATASHEET),
        _check(find("Диффузионный поток"),"Для размера A: 5,6 мл/мин при 0,8 бар","complies",PARKER_DATASHEET)]}]

def run_test():
    audit = run_audit(); positions = []
    for position in audit["position_assessments"]:
        checked = verify_candidates(position["requirements"], candidates_for(position), position["target_price_80_percent"])
        if position["item_index"] == 2:
            for candidate in checked:
                for parameter in candidate["parameter_check"]:
                    if parameter["result"] == "not_confirmed":
                        parameter["source"] = [PARKER_DATASHEET, PARKER_LIFESCIENCE]
                        parameter["evidence"] = "В изученных официальных документах Parker точное требуемое значение не найдено."
        checked = [apply_market_eligibility(x) for x in checked]
        checked = discover_prices(checked, position["target_price_80_percent"])
        admitted = rank_models(checked, position["customer_unit_price"])
        positions.append({"item_index":position["item_index"],"item_name":position["item_name"],"customer_unit_price":position["customer_unit_price"],"target_price_80_percent":position["target_price_80_percent"],"search_query_terms":unique_query_terms(position["requirements"]),"requirements":position["requirements"],"candidate_models":checked,"models_for_supplier_search":admitted,"admitted_models":admitted,"result_ru":"ГОТОВО К ПОИСКУ ПОСТАВЩИКОВ — найдены полностью соответствующие модели" if admitted else "ПРОДОЛЖИТЬ ПОИСК МОДЕЛЕЙ — недостаточно полностью подтверждённых кандидатов"})
    result={"tradeNumber":audit["tradeNumber"],"subject":audit["subject"],"searched_at":datetime.now(timezone.utc).isoformat(),"stages":["technical_discovery","price_discovery"],"positions":positions}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__": print(json.dumps(run_test(),ensure_ascii=False,indent=2))
