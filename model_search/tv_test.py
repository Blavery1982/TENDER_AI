"""Изолированный model_search-тест закупки 100250237126100180.

Данные карточки и документы уже сохранены локально. Модуль не обращается к ЕАТ,
не меняет Google Sheets и не выполняет поиск поставщиков/КП.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/eat_single_31f18b8a-d3b9-4cd8-890c-5d11824fc635.json"
OUTPUT = ROOT / "data/model_search_tv_test.json"

URLS = {
    "ge": "https://general-electronics.com/en/product/led-televizor-ge32lfn0/",
    "lg_monitor": "https://www.lg.com/ru/monitors/lg-32ep950-b",
    "dexp": "https://www.dns-shop.kz/product/characteristics/1ecd772ee75fed20/43-108-sm-televizor-dexp-43ucy3-cernyj/",
    "lg_c6": "https://www.lg.com/ru/televisions/lg-oled42c6rla",
    "lg_c6_price": "https://www.technocity.ru/catalog/detail/1655606/",
    "samsung": "https://www.samsung.com/uk/tvs/oled-tv/s90f-42-inch-oled-4k-smart-tv-qe42s90faexxu/",
    "samsung_price": "https://www.bigstv.ru/product/televizor-samsung-qe42s90f/",
}


def check(parameter: str, required: str, value: str, status: str, source: str, evidence: str) -> dict:
    return {
        "parameter": parameter,
        "required_value": required,
        "model_value": value,
        "result": status,
        "source": source,
        "evidence": evidence,
    }


def candidate(brand, model, checks, production, availability, price=None, price_source=None):
    mismatches = [x["parameter"] for x in checks if x["result"] == "does_not_comply"]
    unknown = [x["parameter"] for x in checks if x["result"] == "not_confirmed"]
    technical = "non_compliant" if mismatches else ("not_confirmed" if unknown else "fully_compliant")
    reasons = [f"Не соответствует параметру: {x}" for x in mismatches]
    reasons += [f"Не удалось подтвердить параметр: {x}" for x in unknown]
    return {
        "brand": brand,
        "model": model,
        "parameter_check": checks,
        "technical_status": technical,
        "production_status": production,
        "russia_availability": availability,
        "russia_availability_sources": [price_source] if price_source else [],
        "russian_market_price_rub": price,
        "price_source": price_source,
        "rejection_reasons": reasons,
    }


def run_test() -> dict:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    req1 = [
        ("Диагональ экрана", ">= 30 и < 35 дюймов"),
        ("Разрешение экрана", "4K UHD"),
        ("Тип экрана", "OLED"),
    ]
    req2 = [
        ("Диагональ экрана", ">= 40 и < 45 дюймов"),
        ("Разрешение экрана", "4K UHD"),
        ("Тип экрана", "OLED"),
    ]
    ge_checks = [
        check(*req1[0], "32 дюйма", "complies", URLS["ge"], "Официально указана диагональ 32 дюйма."),
        check(*req1[1], "Full HD", "does_not_comply", URLS["ge"], "Официально указано Full HD, а требуется 4K UHD."),
        check(*req1[2], "LED, матрица VA", "does_not_comply", URLS["ge"], "Официально указан LED/VA, а требуется OLED."),
    ]
    dexp_checks = [
        check(*req2[0], "43 дюйма", "complies", URLS["dexp"], "Указана диагональ 43 дюйма."),
        check(*req2[1], "3840×2160, 4K", "complies", URLS["dexp"], "Указан стандарт 4K."),
        check(*req2[2], "LED", "does_not_comply", URLS["dexp"], "Карточка называет модель LED-телевизором; OLED не подтвержден."),
    ]
    lg_checks = [
        check(*req2[0], "42 дюйма", "complies", URLS["lg_c6"], "Официально указана диагональ 42 дюйма."),
        check(*req2[1], "3840×2160, 4K UHD", "complies", URLS["lg_c6"], "Официально указано 4K Ultra HD 3840×2160."),
        check(*req2[2], "4K OLED", "complies", URLS["lg_c6"], "Официально указан экран 4K OLED."),
    ]
    samsung_checks = [
        check(*req2[0], "42 дюйма", "complies", URLS["samsung"], "Официально указана диагональ 42 дюйма."),
        check(*req2[1], "3840×2160, 4K", "complies", URLS["samsung"], "Официально указано 4K 3840×2160."),
        check(*req2[2], "OLED", "complies", URLS["samsung"], "Официально указан тип OLED."),
    ]

    positions = [
        {
            "item_index": 1,
            "item_name": "Телевизор 30–35 дюймов, 4K UHD, OLED",
            "quantity": 2,
            "customer_unit_price_rub": 13750,
            "price_threshold_80_percent_rub": 11000,
            "requirements": [{"parameter": p, "required_value": v} for p, v in req1],
            "candidate_models": [
                candidate("General Electronics", "GE32LFN0", ge_checks, "Модель присутствует в актуальном официальном каталоге", "Доступность в РФ подтверждается официальным российским каталогом"),
            ],
            "admitted_models": [],
            "result_ru": "Нет модели, соответствующей всем требованиям ТЗ",
        },
        {
            "item_index": 2,
            "item_name": "Телевизор 40–45 дюймов, 4K UHD, OLED",
            "quantity": 2,
            "customer_unit_price_rub": 26600,
            "price_threshold_80_percent_rub": 21280,
            "requirements": [{"parameter": p, "required_value": v} for p, v in req2],
            "candidate_models": [
                candidate("DEXP", "43UCY3", dexp_checks, "Снятие с производства достоверно не установлено", "Предлагалась в приложенном КП; текущая доступность в РФ отдельно не подтверждена"),
                candidate("LG", "OLED42C6RLA", lg_checks, "Актуальная модель 2026 года в официальном каталоге LG Россия", "В наличии/доступна к заказу в российском магазине", 86230, URLS["lg_c6_price"]),
                candidate("Samsung", "QE42S90F", samsung_checks, "Актуальная модель 2025 года в официальном каталоге Samsung; сведений о снятии нет", "В наличии в российском магазине", 87800, URLS["samsung_price"]),
            ],
            "admitted_models": [],
            "result_ru": "Модели соответствуют ТЗ, но цена выше порога −20%",
        },
    ]
    for pos in positions:
        for model in pos["candidate_models"]:
            if model["technical_status"] == "fully_compliant":
                price = model["russian_market_price_rub"]
                model["price_check_status"] = "above_20_percent_threshold" if price and price > pos["price_threshold_80_percent_rub"] else "not_confirmed"
                if model["price_check_status"] == "above_20_percent_threshold":
                    model["rejection_reasons"].append("Технически соответствует, но цена выше порога")
            else:
                model["price_check_status"] = "not_run_due_to_technical_result"

    result = {
        "test_type": "single_procurement_model_search",
        "procurement_id": "31f18b8a-d3b9-4cd8-890c-5d11824fc635",
        "tradeNumber": "100250237126100180",
        "subject": "Телевизор",
        "source_card_file": str(SOURCE),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "documents": source.get("documents", []),
        "downloaded_documents": [
            "data/contracts/31f18b8a-d3b9-4cd8-890c-5d11824fc635/рап.pdf",
            "data/contracts/31f18b8a-d3b9-4cd8-890c-5d11824fc635/кп.pdf",
        ],
        "nmck_analysis": {
            "quote_models": ["General Electronics GE32LFN0", "DEXP 43UCY3"],
            "quote_unit_prices_rub": [13750, 26600],
            "quote_total_rub": 54100,
            "eat_total_rub": 80700,
            "findings": [
                "GE32LFN0 из КП не соответствует ТЗ по разрешению и типу экрана.",
                "DEXP 43UCY3 из КП не соответствует ТЗ по типу экрана.",
                "В КП для второй позиции указана 1 штука, в карточке ЕАТ — 2 штуки.",
                "Итог КП 54 100 ₽ не обосновывает итог карточки ЕАТ 80 700 ₽ при количестве 2+2.",
            ],
        },
        "positions": positions,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))
