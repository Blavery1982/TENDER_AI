"""Консервативная проверка товара по датированным правилам прослеживаемости."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config/traceability_rules.json"
MOSCOW = ZoneInfo("Europe/Moscow")

NOT_SUBJECT = "Не подлежит по имеющимся данным"
SUBJECT = "Подлежит прослеживаемости"
POSSIBLE = "Возможно подлежит — нужен ТН ВЭД/страна происхождения"
SUPPLIER_CHECK = "Требуется проверка при выборе поставщика"
NO_DATA = "Нет данных"


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digits(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = re.sub(r"\D", "", value)
    return result or None


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    return []


def _find_tnved(item: dict[str, Any]) -> str | None:
    for key in ("tnved", "tnvedCode", "tnVedCode", "ТН ВЭД", "тнвэд"):
        code = _digits(item.get(key))
        if code and 4 <= len(code) <= 10:
            return code
    text = " ".join(_text_values({k: item.get(k) for k in ("description", "characteristics", "name", "eatTitle")}))
    match = re.search(r"(?:ТН\s*ВЭД(?:\s*ЕАЭС)?)[^0-9]{0,15}((?:\d[\s.]*){4,10})", text, re.I)
    return _digits(match.group(1)) if match else None


def _country(item: dict[str, Any]) -> str | None:
    for key in ("countryOfOrigin", "country_of_origin", "originCountry", "countryCodeOfOrigin"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def analyze_item(item: dict[str, Any], checked_at: datetime | None = None) -> dict[str, Any]:
    rules = load_rules()
    checked = checked_at or datetime.now(MOSCOW)
    verified = datetime.fromisoformat(rules["last_verified_at"])
    rules_stale = checked > verified + timedelta(days=31)
    tnved = _find_tnved(item)
    okpd2 = item.get("okpd2Code") or item.get("okpd2") or item.get("okpd2_code")
    country = _country(item)
    normalized_tnved = _digits(tnved)
    normalized_okpd = str(okpd2).strip() if okpd2 else None
    tnved_match = bool(normalized_tnved and any(normalized_tnved.startswith(code) for code in rules["tnved_prefixes"]))
    tnved_excluded = bool(normalized_tnved and any(normalized_tnved.startswith(code) for code in rules["tnved_exclusions"]))
    belarus_exception = bool(
        normalized_tnved in rules["belarus_refrigerator_exclusions"]
        and country and country.casefold() in {"by", "беларусь", "республика беларусь"}
    )
    okpd_match = bool(normalized_okpd and any(normalized_okpd.startswith(code) for code in rules["okpd2_prefixes"]))
    russian = bool(country and country.casefold() in {"ru", "россия", "российская федерация"})

    if rules_stale:
        status = SUPPLIER_CHECK
        reason = "Официальный справочник не проверялся более 31 дня; перед выводом нужно обновить правила по источникам ФНС России"
    elif normalized_tnved and (tnved_excluded or belarus_exception or russian):
        status = NOT_SUBJECT
        reason = "ТН ВЭД или происхождение товара подпадает под официальное исключение из прослеживаемости"
    elif normalized_tnved and tnved_match and country:
        status = SUBJECT
        reason = "ТН ВЭД входит в действующий перечень, происхождение товара не российское; у поставщика нужно проверить реквизиты прослеживаемости для УПД"
    elif normalized_tnved and tnved_match:
        status = POSSIBLE
        reason = "ТН ВЭД входит в перечень прослеживаемости; для окончательного решения нужна страна происхождения конкретной партии"
    elif okpd_match:
        status = POSSIBLE
        reason = "Код ОКПД2 входит в группу товаров из перечня прослеживаемости; окончательно проверить ТН ВЭД и происхождение товара у поставщика"
    elif normalized_tnved:
        status = NOT_SUBJECT
        reason = "Указанный ТН ВЭД не входит в действующий перечень прослеживаемости"
    elif normalized_okpd:
        status = NOT_SUBJECT
        reason = "Код ОКПД2 не соответствует ни одной потенциально прослеживаемой группе актуального справочника"
    else:
        status = NO_DATA
        reason = "В позиции нет кодов ОКПД2 и ТН ВЭД, достаточных для проверки"

    return {
        "traceability_status": status,
        "traceability_reason": reason,
        "traceability_attention": status in {SUBJECT, POSSIBLE},
        "traceability_checked_at": checked.isoformat(),
        "traceability_source_date": rules["traceability_source_date"],
        "traceability_rules_version": rules["rules_version"],
        "traceability_rules_stale": rules_stale,
        "matched_okpd2": normalized_okpd if okpd_match else None,
        "matched_tnved": normalized_tnved if tnved_match else None,
        "supplier_verification": {
            "country_of_origin": country,
            "tnved": normalized_tnved,
            "specific_batch_traceable": None,
            "supplier_can_provide_upd_traceability_details": None
        }
    }


def analyze_purchase_items(raw_purchase: dict[str, Any], checked_at: datetime | None = None) -> list[dict[str, Any]]:
    return [
        {"item_index": index, **analyze_item(item, checked_at)}
        for index, item in enumerate(raw_purchase.get("lotItems") or [])
        if isinstance(item, dict)
    ]


def analyze_purchase(raw_purchase: dict[str, Any], checked_at: datetime | None = None) -> dict[str, Any]:
    """Агрегировать позиции, не меняя решение основного бизнес-фильтра."""
    items = analyze_purchase_items(raw_purchase, checked_at)
    statuses = [item["traceability_status"] for item in items]
    if SUBJECT in statuses:
        status = SUBJECT
    elif POSSIBLE in statuses:
        status = POSSIBLE
    elif SUPPLIER_CHECK in statuses or NO_DATA in statuses or not statuses:
        status = SUPPLIER_CHECK
    else:
        status = NOT_SUBJECT

    warnings = []
    raw_items = raw_purchase.get("lotItems") or []
    for result in items:
        if result["traceability_status"] not in {SUBJECT, POSSIBLE}:
            continue
        index = result["item_index"]
        raw_item = raw_items[index] if index < len(raw_items) else {}
        name = (
            raw_item.get("name") or raw_item.get("eatTitle")
            or raw_item.get("description") or "Наименование не указано"
        )
        wording = (
            "подлежит прослеживаемости" if result["traceability_status"] == SUBJECT
            else "возможно подлежит прослеживаемости"
        )
        warnings.append({
            "item_index": index,
            "item_number": index + 1,
            "item_name": str(name).strip(),
            "warning": (
                f"⚠ Позиция {index + 1} — {wording}. При выборе поставщика "
                "проверить ТН ВЭД, страну происхождения и реквизиты прослеживаемости."
            ),
        })
    return {
        "traceability_status": status,
        "traceability_attention": bool(warnings),
        "traceability_warning": " ".join(item["warning"] for item in warnings),
        "traceability_warning_items": warnings,
        "traceability_items": items,
        "filter_result_affected": False,
    }
