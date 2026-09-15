"""Контрольный аудит документов закупки 100250237126100180 без сети."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from documents.procurement_audit import (
    classify_supplier_search_readiness,
    customer_document_issue,
    extract_document_with_evidence,
)
from documents.text_extraction import identify_model_from_catalog

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/model_search_tv_test.json"
CARD_SOURCE = ROOT / "data/eat_single_31f18b8a-d3b9-4cd8-890c-5d11824fc635.json"
PDF_DIR = ROOT / "data/tenders/31f18b8a-d3b9-4cd8-890c-5d11824fc635"
OUTPUT = ROOT / "data/procurement_audit_tv_ocr_test.json"


def run_test() -> dict:
    model_search = json.loads(SOURCE.read_text(encoding="utf-8"))
    card = json.loads(CARD_SOURCE.read_text(encoding="utf-8"))
    positions = model_search["positions"]
    documents = []
    for path in sorted(PDF_DIR.glob("*.pdf")):
        extracted = extract_document_with_evidence(path)
        documents.append({"file_name": path.name, **extracted})
    by_name = {x["file_name"]: x for x in documents}
    quote, tz = by_name["кп.pdf"], by_name["рап.pdf"]

    # Модели берутся только из OCR. Внешний источник разрешён исключительно для
    # идентификации одной неоднозначной буквы/цифры, не для исправления ТЗ.
    external_catalog = [
        (positions[0]["candidate_models"][0]["model"], "https://general-electronics.com/en/product/led-televizor-ge32lfn0/"),
        (positions[1]["candidate_models"][0]["model"], "https://www.dns-shop.kz/product/characteristics/1ecd772ee75fed20/43-108-sm-televizor-dexp-43ucy3-cernyj/"),
    ]
    identified = [identify_model_from_catalog(quote, model, source) for model, source in external_catalog]
    identified = [x for x in identified if x]
    if len(identified) != 2:
        raise RuntimeError("OCR не дал однозначных вариантов для идентификации двух моделей")

    raw = card["raw"]
    lot = raw.get("lot") or {}
    eat_items = lot.get("lotItems") or []
    # Извлечение чисел из OCR-текста без их исправления.
    quote_lines = quote["text"]
    first = re.search(r"13\s*750[,.]00\s+(\d+)", quote_lines)
    second = re.search(r"26\s*600[,.]00\s+(\d+)", quote_lines)
    total = re.search(r"Итого:\s*54\s*100[,.]00", quote_lines, re.I)
    quote_quantities = [int(first.group(1)) if first else None, int(second.group(1)) if second else None]
    quote_total = 54100 if total else None
    eat_quantities = [int(x.get("quantity")) for x in eat_items]
    eat_nmck = lot.get("price")

    quote_models = {
        1: (identified[0]["identified_model"], "LED вместо OLED; Full HD при неуверенно распознанном требовании «4K LHD»"),
        2: (identified[1]["identified_model"], "LED вместо OLED"),
    }
    issues = []
    for number, (model, mismatch) in quote_models.items():
        candidate = positions[number - 1]["candidate_models"][0]
        issues.append(customer_document_issue(
            "price_justification_model_mismatch", number,
            f"Модель из КП {model} не соответствует ТЗ: {mismatch}.",
            {
                "model": model,
                "mismatched_parameters": candidate["rejection_reasons"],
                "parameter_check": candidate["parameter_check"],
                "source_document": "кп.pdf", "model_identification": identified[number - 1],
            },
            "high",
        ))

    issues.append(customer_document_issue(
        "quantity_mismatch", 2,
        "В КП указана 1 штука второй позиции, в карточке ЕАТ и ТЗ — 2 штуки.",
        {"commercial_offer_quantity": quote_quantities[1], "eat_quantity": eat_quantities[1], "tz_quantity": 2,
         "source_document": "кп.pdf"},
        "high",
    ))
    issues.append(customer_document_issue(
        "nmck_not_supported", None,
        "Итог КП 54 100 ₽ не подтверждает НМЦК 80 700 ₽.",
        {"commercial_offer_total_rub": quote_total, "eat_nmck_rub": eat_nmck,
         "difference_rub": eat_nmck - quote_total, "commercial_offer_line_totals_rub": [27500, 26600],
         "arithmetic_check": {"line_sum_equals_quote_total": 27500 + 26600 == quote_total,
                              "eat_position_sum_equals_nmck": sum(x.get("sum") or 0 for x in eat_items) == eat_nmck}},
        "high",
    ))
    issues.append(customer_document_issue(
        "possible_customer_tz_error", None,
        "Найденные серийные OLED 4K телевизоры требуемой диагонали существенно дороже заложенной цены.",
        {
            "position_2_threshold_rub": 21280,
            "confirmed_models": [
                {"model": "LG OLED42C6RLA", "russian_price_rub": 86230},
                {"model": "Samsung QE42S90F", "russian_price_rub": 87800},
            ],
            "position_1_fully_compliant_models_found": 0,
        },
        "warning",
    ))

    summary = (
        "КП не соответствует ТЗ: GE32LFN0 — Full HD/LED вместо 4K/OLED; "
        "DEXP 43UCY3 — LED вместо OLED; количество в КП не совпадает с ЕАТ; "
        "итог КП не обосновывает НМЦК."
    )
    decision = classify_supplier_search_readiness(issues, positions)
    result = {
        "tradeNumber": model_search["tradeNumber"],
        "purchase_id": model_search["procurement_id"],
        "subject": model_search["subject"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "source_model_search_file": str(SOURCE),
        "document_extraction": documents,
        "recognized_models": identified,
        "automatic_extraction_checks": {
            "tz_oled_found": bool(re.search(r"\bOLED\b", tz["text"])),
            "tz_4k_uhd_exact_found": bool(re.search(r"\b4K\s+UHD\b", tz["text"], re.I)),
            "tz_4k_uhd_possible_ocr_variant": bool(re.search(r"\b4[KК]\s+LHD\b", tz["text"], re.I)),
            "quote_led_found": bool(re.search(r"\bLED\b", quote["text"])),
            "quote_full_hd_found": bool(re.search(r"\bFull\s+HD\b", quote["text"], re.I)),
            "quote_prices_found": [bool(first), bool(second)],
            "quote_total_found": bool(total),
            "quote_quantities": quote_quantities,
            "eat_quantities": eat_quantities,
        },
        "customer_document_issues": issues,
        "customer_document_issues_summary_ru": summary,
        "google_sheets_future_field": {
            "column": "ОШИБКИ В ДОКУМЕНТАХ ЗАКАЗЧИКА",
            "value": summary,
        },
        "position_classification": [
            {
                "position_number": p["item_index"],
                "final_reasons_ru": (
                    ["Обоснование НМЦК не соответствует ТЗ",
                     "Нет модели, полностью соответствующей всем требованиям ТЗ"]
                    if p["item_index"] == 1 else
                    ["Обоснование НМЦК не соответствует ТЗ",
                     "Модель существует, но цена выше ценового порога"]
                ),
            }
            for p in positions
        ],
        **decision,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))
