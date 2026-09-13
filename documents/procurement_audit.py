"""Предаудит одной сохранённой закупки без обращения к ЕАТ."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from documents.text_extraction import extract_pdf

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/eat_filtered_semantic_v2.json"
CONTRACT_REPORT = ROOT / "data/eat_contract_test.json"
OUTPUT = ROOT / "data/procurement_audit_test.json"
TEST_TRADE_NUMBER = "100309200126100271"

TZ_STATUS_RU = {
    "ok": "ТЗ корректно",
    "inconsistencies_found": "Есть противоречия",
    "impossible_parameters": "Есть невозможные параметры",
    "manual_check": "Нужна ручная проверка",
}
PRICE_STATUS_RU = {
    "matches_tz": "Соответствует ТЗ",
    "does_not_match_tz": "Не соответствует ТЗ",
    "model_not_identified": "Не указана",
    "no_price_justification": "Обоснование цены не приложено",
    "manual_check": "Нужна ручная проверка",
}
PRECHECK_RU = {
    "ready": "Готово к поиску поставщиков",
    "ready_with_attention": "Можно искать, но есть риски",
    "manual_check": "Нужна ручная проверка",
    "do_not_search": "Не тратить время на поиск",
}

FINAL_AUDIT_REASONS_RU = {
    "price_justification_mismatch": "Обоснование НМЦК не соответствует ТЗ",
    "quantity_or_amount_conflict": "Противоречие количества/суммы",
    "possible_customer_tz_error": "ТЗ содержит вероятную ошибку заказчика",
    "model_above_price_threshold": "Модель существует, но цена выше ценового порога",
    "no_fully_compliant_model": "Нет модели, полностью соответствующей всем требованиям ТЗ",
    "insufficient_evidence": "Недостаточно данных для автоматического решения",
}

ISSUE_TYPES = {
    "price_justification_model_mismatch",
    "quantity_mismatch",
    "arithmetic_mismatch",
    "nmck_not_supported",
    "possible_customer_tz_error",
}


def customer_document_issue(
    issue_type: str,
    position_number: int | None,
    description: str,
    evidence: dict[str, Any] | list[Any] | str,
    severity: str,
) -> dict[str, Any]:
    """Создаёт проверяемую проблему документа без технического текста для интерфейса."""
    if issue_type not in ISSUE_TYPES:
        raise ValueError(f"Неизвестный тип проблемы: {issue_type}")
    if severity not in {"warning", "high"}:
        raise ValueError(f"Неизвестная критичность: {severity}")
    return {
        "issue_type": issue_type,
        "position_number": position_number,
        "description": description,
        "evidence": evidence,
        "severity": severity,
    }


def classify_supplier_search_readiness(
    customer_document_issues: list[dict[str, Any]],
    position_assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ошибки обоснования информационны; блокирует лишь доказанная проблема самого ТЗ."""
    issue_types = {x.get("issue_type") for x in customer_document_issues}
    reasons: list[str] = []
    if "price_justification_model_mismatch" in issue_types:
        reasons.append(FINAL_AUDIT_REASONS_RU["price_justification_mismatch"])
    if issue_types & {"quantity_mismatch", "arithmetic_mismatch", "nmck_not_supported"}:
        reasons.append(FINAL_AUDIT_REASONS_RU["quantity_or_amount_conflict"])
    if "possible_customer_tz_error" in issue_types:
        reasons.append(FINAL_AUDIT_REASONS_RU["possible_customer_tz_error"])

    technically_compliant = [
        model
        for position in position_assessments
        for model in position.get("candidate_models", [])
        if model.get("technical_status") == "fully_compliant"
    ]
    if any(not any(m.get("technical_status") == "fully_compliant"
                   for m in position.get("candidate_models", []))
           for position in position_assessments):
        reasons.append(FINAL_AUDIT_REASONS_RU["no_fully_compliant_model"])

    proven_stop = any(
        x.get("issue_type") == "possible_customer_tz_error"
        and x.get("severity") == "high"
        and isinstance(x.get("evidence"), dict)
        and x["evidence"].get("proven_impossible") is True
        for x in customer_document_issues
    )
    if proven_stop:
        status = "do_not_search_tz_impossible"
        status_ru = "НЕ ТРАТИТЬ ВРЕМЯ — доказанная невыполнимость/противоречие ТЗ"
    else:
        status = "continue_model_search"
        status_ru = "ПРОДОЛЖИТЬ ПОИСК МОДЕЛЕЙ"
    return {
        "automatic_supplier_search_allowed": not proven_stop,
        "final_reasons_ru": list(dict.fromkeys(reasons)),
        "supplier_search_status": status,
        "supplier_search_status_ru": status_ru,
    }

DOCUMENT_KINDS = {
    "technical_specification": "Техническое задание / описание объекта закупки",
    "specification": "Спецификация",
    "contract_draft": "Проект договора / контракт",
    "contract_appendix": "Приложение к договору",
    "price_justification": "Обоснование НМЦК / расчёт цены",
    "commercial_offer": "Коммерческое предложение",
    "price_list": "Прайс",
    "other": "Иное приложение",
}


def _extract_xlsx(path: Path) -> tuple[str, list[dict[str, Any]]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    rows: list[dict[str, Any]] = []
    with ZipFile(path) as archive:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                  for item in shared_root.findall("m:si", ns)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relations = {node.attrib["Id"]: node.attrib["Target"] for node in relations_root}
        for sheet in workbook.findall(".//m:sheet", ns):
            target = relations[sheet.attrib[f"{{{ns['r']}}}id"]]
            member = target.lstrip("/") if target.startswith("/xl/") else (
                target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
            )
            sheet_root = ET.fromstring(archive.read(member))
            for row in sheet_root.findall(".//m:row", ns):
                values: dict[str, Any] = {}
                for cell in row.findall("m:c", ns):
                    coordinate = cell.attrib["r"]
                    column = re.match(r"[A-Z]+", coordinate).group(0)  # type: ignore[union-attr]
                    value_node = cell.find("m:v", ns)
                    value: Any = "" if value_node is None else value_node.text
                    if cell.attrib.get("t") == "s" and value not in (None, ""):
                        value = shared[int(value)]
                    elif value not in (None, ""):
                        try:
                            value = float(value)
                            if value.is_integer():
                                value = int(value)
                        except (ValueError, AttributeError):
                            pass
                    values[column] = value
                if any(value not in (None, "") for value in values.values()):
                    rows.append({"sheet": sheet.attrib["name"], "row": int(row.attrib["r"]), "cells": values})
    text = "\n".join(" | ".join(str(v) for v in row["cells"].values() if v not in (None, "")) for row in rows)
    return text, rows


def extract_document(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    """Возвращает текст, статус и табличные строки (для XLSX)."""
    try:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            extracted = extract_pdf(path)
            return extracted["text"], "analyzed", []
        if suffix in {".doc", ".docx"}:
            result = subprocess.run(
                ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, check=True,
            )
            return result.stdout.decode("utf-8", errors="replace"), "analyzed", []
        if suffix == ".xlsx":
            text, rows = _extract_xlsx(path)
            return text, "analyzed", rows
        return "", "unsupported", []
    except Exception:
        return "", "parse_failed", []


def extract_document_with_evidence(path: Path) -> dict[str, Any]:
    """Расширенный результат для аудита; старый API extract_document сохранён."""
    if path.suffix.casefold() == ".pdf":
        result = extract_pdf(path)
        return {"text": result.pop("text"), "document_parse_status": "analyzed", "rows": [], **result}
    text, status, rows = extract_document(path)
    return {"text": text, "document_parse_status": status, "rows": rows,
            "read_method": "text_layer", "ocr_engine": None, "ocr_pages": [], "pages": [],
            "raw_ocr_text": "", "critical_values": [], "doubtful_critical_values": [], "warnings": []}


def classify_document(name: str, text: str) -> list[str]:
    lowered_name = name.casefold()
    probe = f"{name}\n{text[:12000]}".casefold()
    kinds: list[str] = []
    is_justification = bool(re.search(r"обоснован\w*\s+(?:нмцк|начальн)|расч[её]т\w*\s+(?:нмцк|цен)", probe))
    if is_justification:
        kinds.append("price_justification")
    # Упоминание трёх КП в расчёте не превращает сам расчёт в приложенные КП.
    if re.search(r"коммерческ\w*\s+предложен|\bкп\b", lowered_name) and not is_justification:
        kinds.append("commercial_offer")
    if "прайс" in probe:
        kinds.append("price_list")
    if (re.search(r"(?:проект|контракт|договор)", lowered_name)
            or re.search(r"\n\s*(?:1\.?\s*)?предмет\s+контракт|сторон\w*.{0,100}заключили\s+настоящ", probe)) and not is_justification:
        kinds.append("contract_draft")
    if re.search(r"техническ\w*\s+задани|описани\w*\s+объект", probe):
        kinds.append("technical_specification")
    if "спецификац" in probe:
        kinds.append("specification")
    if "приложение к договор" in probe or "приложение к контракт" in probe:
        kinds.append("contract_appendix")
    return list(dict.fromkeys(kinds)) or ["other"]


def _comparison(value: str) -> str:
    lowered = value.casefold()
    if "не менее" in lowered: return "minimum"
    if "не более" in lowered: return "maximum"
    if re.search(r"\bот\b.+\bдо\b", lowered): return "range"
    if "наличие" in lowered or "соответствие" in lowered: return "required"
    return "equals"


def _requirements(item: dict[str, Any], index: int) -> list[dict[str, Any]]:
    text = item.get("description") or ""
    requirements = []
    for raw_line in text.splitlines():
        line = re.sub(r"^[\s;–—-]+", "", raw_line).strip(" ;")
        if not line:
            continue
        if ":" in line:
            parameter, value = (part.strip() for part in line.split(":", 1))
        elif "–" in line or " - " in line:
            parts = re.split(r"\s+[–-]\s+", line, maxsplit=1)
            parameter, value = (parts + [""])[:2]
        else:
            parameter, value = line, "Требуется"
        requirements.append({
            "parameter": parameter,
            "required_value": value,
            "comparison_type": _comparison(f"{parameter} {value}"),
            "source": f"Карточка ЕАТ, позиция {index}",
            "criticality": "mandatory",
        })
    return requirements


def _normalized_items(purchase: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(purchase["raw"].get("lotItems") or [], 1):
        result.append({
            "item_index": index,
            "name": item.get("description") or item.get("name") or item.get("eatTitle") or purchase["raw"].get("subject"),
            "short_name": item.get("name") or item.get("eatTitle"),
            "quantity": item.get("quantity"),
            "unit": item.get("okeiTitle"),
            "brand": None,
            "model": None,
            "required_characteristics": _requirements(item, index),
            "configuration": [],
            "document_requirements": [x for x in _requirements(item, index) if re.search(r"наличие|руководство|стандарт", x["parameter"], re.I)],
            "origin_requirements": [],
            "other_mandatory_conditions": [],
        })
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def customer_prices(item: dict[str, Any]) -> tuple[float | None, float | None]:
    """Цена конкретной позиции и допустимый максимум 80%."""
    unit_price = _number(item.get("unitPrice"))
    if unit_price is None:
        total, quantity = _number(item.get("sum")), _number(item.get("quantity"))
        if total is not None and quantity not in (None, 0):
            unit_price = total / quantity
    target = round(unit_price * 0.8, 2) if unit_price is not None else None
    return unit_price, target


def evaluate_candidate_model(
    requirements: list[dict[str, Any]],
    candidate: dict[str, Any],
    target_price: float | None,
) -> dict[str, Any]:
    """Модель проходит только при доказательстве каждого требования и цены."""
    supplied = candidate.get("parameter_check") or []
    by_parameter = {str(x.get("parameter")): x for x in supplied}
    checks = []
    for requirement in requirements:
        parameter = requirement["parameter"]
        check = dict(by_parameter.get(parameter) or {})
        status = check.get("result")
        if status not in {"complies", "does_not_comply", "not_confirmed"}:
            status = "not_confirmed"
        checks.append({
            "parameter": parameter,
            "tz_requirement": requirement["required_value"],
            "model_value": check.get("model_value"),
            "result": status,
            "source": check.get("source"),
            "evidence": check.get("evidence") or check.get("model_value"),
        })
    mismatched = [x["parameter"] for x in checks if x["result"] == "does_not_comply"]
    unconfirmed = [x["parameter"] for x in checks if x["result"] == "not_confirmed"]
    technical = "non_compliant" if mismatched else "not_confirmed" if unconfirmed else "fully_compliant"
    market_price = _number(candidate.get("market_price"))
    if market_price is None or not candidate.get("market_price_source"):
        price_status = "price_not_confirmed"
    elif target_price is not None and market_price <= target_price:
        price_status = "passes_20_percent_threshold"
    elif target_price is not None:
        price_status = "fails_20_percent_threshold"
    else:
        price_status = "price_not_confirmed"
    return {
        "brand": candidate.get("brand"),
        "model": candidate.get("model"),
        "market_price": market_price,
        "market_price_source": candidate.get("market_price_source"),
        "price_check_status": price_status,
        "technical_status": technical,
        "parameter_check": checks,
        "unconfirmed_parameters": unconfirmed,
        "mismatched_parameters": mismatched,
        "confidence": candidate.get("confidence") or "low",
    }


def suitable_models_text(candidates: list[dict[str, Any]]) -> str:
    suitable = [x for x in candidates if x["technical_status"] == "fully_compliant"
                and x.get("brand") and x.get("model")]
    if suitable:
        confidence = {"high": 0, "medium": 1, "low": 2}
        suitable.sort(key=lambda x: (confidence.get(x.get("confidence"), 3), x["market_price"]))
        return "\n".join(
            f"{index}. {x['brand']} {x['model']} — {x['market_price']:,.2f} ₽ — полностью соответствует ТЗ".replace(",", " ")
            for index, x in enumerate(suitable[:3], 1)
        )
    if any(x["technical_status"] == "not_confirmed" for x in candidates):
        return "Есть вероятная модель, но полное соответствие ТЗ не подтверждено"
    return "Модель, полностью соответствующая ТЗ, не найдена — проверить ТЗ заказчика"


def _fragment(text: str, match: re.Match[str], radius: int = 115) -> str:
    return re.sub(r"\s+", " ", text[max(0, match.start()-radius):match.end()+radius]).strip()


def delivery_conditions(documents: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    patterns = {
        "unloading": r"погрузочно-разгрузочн\w*\s+работ|разгрузк\w*.{0,80}(?:поставщик|силами)",
        "advance_notice": r"не\s+менее\s+чем\s+за\s+(\d+)\s+дн\w*.{0,90}уведомлен\w*.{0,80}доставк",
        "supplier_delivery": r"поставщик\s+самостоятельно\s+доставл",
    }
    labels = {"unloading": "Погрузочно-разгрузочные работы за счёт поставщика",
              "advance_notice": "Уведомить о доставке минимум за 2 дня",
              "supplier_delivery": "Доставка силами поставщика"}
    evidence = []
    for document in documents:
        for kind, pattern in patterns.items():
            for match in re.finditer(pattern, document["text"], re.I | re.S):
                local = document["text"][max(0, match.start()-60):match.end()+80]
                if re.search(r"не\s+(?:требу|предусмотр|осуществл|вход)", local, re.I):
                    continue
                key = (kind, document["file_name"])
                if any((e["type"], e["document"]) == key for e in evidence):
                    continue
                evidence.append({"type": kind, "document": document["file_name"],
                                 "text_fragment": _fragment(document["text"], match), "confidence": "high"})
    short = "; ".join(dict.fromkeys(labels[e["type"]] for e in evidence))
    return short, evidence


def _price_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    offers, products = [], []
    for row in rows:
        cells = row["cells"]
        if row["row"] == 6:
            offers = [cells.get(column) for column in ("E", "F", "G") if cells.get(column)]
        if row["row"] in (7, 8):
            products.append({
                "name": cells.get("B"), "unit": cells.get("C"), "quantity": cells.get("D"),
                "source_prices": [cells.get(column) for column in ("E", "F", "G")],
                "calculated_unit_price": cells.get("N"), "calculated_total": cells.get("O"),
                "brand": None, "model": None, "article": None, "characteristics": [],
            })
    return {"method": "Сопоставимые рыночные цены", "sources": offers, "products": products}


def run_test() -> dict[str, Any]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))["purchases"]
    purchase = next(p for p in source if p["normalized"]["number"] == TEST_TRADE_NUMBER)
    contract_report = json.loads(CONTRACT_REPORT.read_text(encoding="utf-8"))["purchases"]
    metadata = next(p for p in contract_report if p["tradeNumber"] == TEST_TRADE_NUMBER)

    documents = []
    all_rows: list[dict[str, Any]] = []
    for file_info in metadata["contract_files"]:
        path = Path(file_info["local_path"])
        text, status, rows = extract_document(path)
        all_rows.extend(rows)
        kinds = classify_document(file_info["file_name"], text)
        documents.append({
            "file_name": file_info["file_name"], "format": path.suffix.upper().lstrip("."),
            "local_path": str(path), "document_parse_status": status,
            "document_kinds": kinds, "document_kinds_ru": [DOCUMENT_KINDS[x] for x in kinds],
            "text": text,
        })

    normalized_tz = _normalized_items(purchase)
    raw_items = purchase["raw"].get("lotItems") or []
    position_assessments = []
    for index, (normalized_item, raw_item) in enumerate(zip(normalized_tz, raw_items), 1):
        unit_price, target = customer_prices(raw_item)
        # Сохранённые документы называют только совместимый корпус оборудования.
        # Это не является доказательством модели закупаемого фильтра.
        clues = []
        description = raw_item.get("description") or ""
        for match in re.finditer(r"корпус\s+фильтра\s+([^\n;]+)", description, re.I):
            clues.append({"text": match.group(1).strip(), "meaning": "Модель корпуса оборудования заказчика, не модель закупаемого фильтра"})
        candidates: list[dict[str, Any]] = []
        evaluated = [evaluate_candidate_model(normalized_item["required_characteristics"], x, target) for x in candidates]
        position_assessments.append({
            "item_index": index,
            "item_name": normalized_item["short_name"],
            "customer_unit_price": unit_price,
            "target_price_80_percent": target,
            "requirements": normalized_item["required_characteristics"],
            "model_identification_clues": clues,
            "candidate_models": evaluated,
            "suitable_models_determined_by_tz": suitable_models_text(evaluated),
            "model_technical_check_ru": "Не удалось подтвердить",
            "model_price_check_ru": "Не удалось подтвердить",
            "conclusion": "Не удалось подтвердить реальную модель и её рыночную цену; переход к списку подходящих моделей невозможен.",
        })
    # Совместимость с конкретно названным имеющимся оборудованием — не ошибка,
    # но подозрительно узкое условие, которое человек должен проверить.
    tz_text = "\n".join(item.get("description") or "" for item in purchase["raw"].get("lotItems") or [])
    narrow_compatibility = bool(re.search(r"оборудовани\w*\s+имеющ\w*\s+у\s+заказчик", tz_text, re.I))
    unreadable = any(d["document_parse_status"] != "analyzed" for d in documents)
    tz_status = "manual_check" if unreadable or narrow_compatibility else "ok"
    tz_note = (
        "Явных противоречий и невозможных параметров не найдено, но позиция 2 содержит "
        "точную привязку к имеющемуся корпусу domnick hunter demi HSI Plus, адаптеру T 126 "
        "и размеру 5 дюймов; совместимость требует ручной проверки."
        if narrow_compatibility else
        "Явных внутренних противоречий и физически невозможных параметров не найдено."
    )
    special_short, special_evidence = delivery_conditions(documents)
    price = _price_data(all_rows)
    price_status = "model_not_identified" if price["products"] else "no_price_justification"
    comparisons = []
    for index, item in enumerate(normalized_tz, 1):
        price_item = next((x for x in price["products"] if (x["name"] or "").casefold() in (item["short_name"] or "").casefold() or (item["short_name"] or "").casefold() in (x["name"] or "").casefold()), None)
        comparisons.append({
            "item_index": index, "tz_product": item["short_name"],
            "price_justification_product": price_item["name"] if price_item else None,
            "identified_model": None, "parameter_results": [],
            "result": "нет данных", "source": "Приложение №2 — обоснование НМЦК.xlsx",
            "explanation": "В расчёте есть общее наименование и цены, но нет бренда, модели, артикула и характеристик товара из коммерческих предложений.",
        })
    mismatches: list[dict[str, Any]] = []
    mismatch_short = "Несоответствия не доказаны: модели и характеристики товаров из трёх КП отсутствуют."
    precheck = "ready_with_attention"

    public_documents = [{k: v for k, v in doc.items() if k != "text"} for doc in documents]
    result = {
        "tradeNumber": TEST_TRADE_NUMBER,
        "purchase_id": purchase["normalized"]["id"],
        "subject": purchase["normalized"]["name"],
        "documents": public_documents,
        "normalized_tz": normalized_tz,
        "position_assessments": position_assessments,
        "tz_audit_status": tz_status,
        "tz_audit_status_ru": TZ_STATUS_RU[tz_status],
        "tz_audit_note": tz_note,
        "special_conditions_short": special_short,
        "special_conditions_evidence": special_evidence,
        "price_justification": price,
        "price_justification_status": price_status,
        "price_justification_status_ru": PRICE_STATUS_RU[price_status],
        "model_from_price_justification": "Не указана",
        "price_justification_note": "Найдены расчёт НМЦК и три ценовых предложения, но без приложенных КП, брендов, моделей, артикулов и характеристик.",
        "price_to_tz_comparison": comparisons,
        "detected_mismatches": mismatches,
        "mismatch_short": mismatch_short,
        "pre_supplier_check_status": precheck,
        "pre_supplier_check_status_ru": PRECHECK_RU[precheck],
        "pre_supplier_check_note": "ТЗ не признано невозможным; следующий автоматический этап — внешний поиск и строгая проверка моделей по всем параметрам.",
        "model_search_status": "external_model_search_required",
        "model_search_status_ru": "Требуется внешний поиск модели по ТЗ",
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    audit = run_test()
    print(json.dumps({
        "tradeNumber": audit["tradeNumber"],
        "tz_audit_status_ru": audit["tz_audit_status_ru"],
        "price_justification_status_ru": audit["price_justification_status_ru"],
        "pre_supplier_check_status_ru": audit["pre_supplier_check_status_ru"],
    }, ensure_ascii=False, indent=2))
