"""Конфигурируемые фильтры опубликованных закупок ЕАТ."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "eat_filters.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _key(value: str) -> str:
    value = value.casefold().replace("ё", "е").replace("—", "-").replace("–", "-")
    value = re.sub(r"[.,;()]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _region_table(config: dict[str, Any]) -> dict[str, str]:
    table = {_key(region): region for region in config["allowed_regions"]}
    table.update({_key(alias): canonical for alias, canonical in config["region_aliases"].items()})
    return table


def normalize_region(value: Any, config: dict[str, Any]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _region_table(config).get(_key(value))


def _is_explicit_region_name(value: str) -> bool:
    key = _key(value)
    return bool(
        re.fullmatch(
            r"(?:г |город )?(?:севастополь|москва|санкт-петербург|санкт петербург)"
            r"|.+ (?:область|обл|край|республика|респ|автономный округ|автономная область)"
            r"|(?:республика|респ) .+",
            key,
        )
    )


def _delivery_regions(raw: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    explicit: list[str] = []
    addresses: list[str] = []
    structured: list[dict[str, Any]] = []
    for info in raw.get("deliveryInfos") or []:
        if not isinstance(info, dict):
            continue
        address = info.get("deliveryAddress")
        if not isinstance(address, dict):
            continue
        structured.append(address)
        region = address.get("regionName")
        if isinstance(region, str) and region.strip() and region not in explicit:
            explicit.append(region)
        full = address.get("formattedFullInfo")
        if isinstance(full, str) and full.strip() and full not in addresses:
            addresses.append(full)
    return explicit, addresses, structured


def _city_key(value: Any) -> str:
    key = _key(value) if isinstance(value, str) else ""
    return re.sub(r"^(?:г|город)\s+", "", key).strip()


def _address_is_allowed_conditional_city(
    address: dict[str, Any],
    rule: dict[str, Any],
) -> bool:
    allowed = {_city_key(value) for value in rule.get("allowed_cities") or []}
    structured_city = _city_key(address.get("city"))
    if structured_city:
        return structured_city in allowed
    full = address.get("formattedFullInfo")
    if not isinstance(full, str):
        return False
    for city in allowed:
        if re.search(
            rf"(?:^|[,;]\s*)(?:г(?:ород)?\.?\s+){re.escape(city)}(?:\s*[,;]|$)",
            full,
            re.I,
        ):
            return True
    return False


def _searchable_product_text(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    subject = raw.get("subject")
    if isinstance(subject, str):
        parts.append(subject)
    for item in raw.get("lotItems") or []:
        if isinstance(item, dict):
            for field in ("name", "description"):
                value = item.get(field)
                if isinstance(value, str):
                    parts.append(value)
    return "\n".join(parts).casefold()


def _subsidy_text(raw: dict[str, Any], purchase_type_title: Any) -> str:
    parts = [_searchable_product_text(raw)]
    if isinstance(purchase_type_title, str):
        parts.append(purchase_type_title.casefold())
    for field in ("description", "generalDescription", "purchaseDescription"):
        value = raw.get(field)
        if isinstance(value, str):
            parts.append(value.casefold())
    return "\n".join(parts)


def filter_purchase(
    raw: dict[str, Any],
    purchase_type_title: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    is_confidential_locked = bool(
        raw.get("isTradeInfoHiddenByPrivacyAgreement") is True
        and raw.get("hideDetailsForUnauthorized") is True
    )
    if is_confidential_locked:
        return {
            "filter_result": "confidential_locked",
            "rejection_reasons": ["confidentiality_agreement_required"],
            "matched_bad_words": [],
            "normalized_regions": [],
            "unmatched_regions": [],
            "delivery_addresses": [],
            "items_count": None,
            "purchaseTypeTitle": purchase_type_title,
            "rule_checks": {
                "price_150_400_thousand": False,
                "region_allowed": False,
                "items_count_at_most_10": False,
                "law_44fz_confirmed": bool(
                    isinstance(purchase_type_title, str)
                    and config["law_required_substring"].casefold()
                    in purchase_type_title.casefold()
                ),
                "no_bad_words": False,
                "no_subsidy": False,
            },
            "confidentiality_evidence": {
                "isTradeInfoHiddenByPrivacyAgreement": True,
                "hideDetailsForUnauthorized": True,
            },
        }

    rejected: list[str] = []
    manual: list[str] = []

    price = raw.get("price")
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        manual.append("price_not_determined")
    elif price < config["price"]["min"]:
        rejected.append("price_below_min")
    elif price > config["price"]["max"]:
        rejected.append("price_above_max")

    if not isinstance(purchase_type_title, str) or not purchase_type_title.strip():
        manual.append("law_not_determined")
    elif config["law_required_substring"].casefold() not in purchase_type_title.casefold():
        rejected.append("not_44fz")

    subsidy_found = config["subsidy_substring"].casefold() in _subsidy_text(raw, purchase_type_title)

    procedure_text = " ".join(str(raw.get(field) or "") for field in
                              ("purchaseMethod", "purchaseMethodTitle", "type", "typeTitle",
                               "subType", "subTypeTitle", "procedureType", "procedureTypeTitle"))
    if re.search(r"\bаукцион\w*", procedure_text, re.I):
        rejected.append("auction_not_allowed")
    if raw.get("stateDefenseOrder") is True or raw.get("isStateDefenseOrder") is True:
        rejected.append("state_defense_order_not_allowed")

    source_regions, addresses, structured_addresses = _delivery_regions(raw)
    normalized_regions: list[str] = []
    unmatched_regions: list[str] = []
    allowed = set(config["allowed_regions"])
    conditional = config.get("conditional_delivery_regions") or {}
    forbidden_regions: list[str] = []
    for address in structured_addresses:
        source = address.get("regionName")
        if not isinstance(source, str) or not source.strip():
            unmatched_regions.append(str(address.get("formattedFullInfo") or ""))
            continue
        normalized = normalize_region(source, config)
        if normalized is None:
            if _is_explicit_region_name(source):
                forbidden_regions.append(source)
            else:
                unmatched_regions.append(source)
        elif normalized in allowed:
            if normalized not in normalized_regions:
                normalized_regions.append(normalized)
        elif normalized in conditional:
            rule = conditional[normalized]
            if _address_is_allowed_conditional_city(address, rule):
                location = str(rule.get("normalized_location") or normalized)
                if location not in normalized_regions:
                    normalized_regions.append(location)
            else:
                forbidden_regions.append(source)
        else:
            forbidden_regions.append(normalized)
    if forbidden_regions:
        rejected.append("region_not_allowed")
    elif not source_regions or unmatched_regions:
        manual.append("region_not_determined")

    lot_items = raw.get("lotItems")
    items_count = len(lot_items) if isinstance(lot_items, list) else None
    if items_count is None:
        manual.append("items_count_not_determined")
    elif items_count > config["max_items"]:
        rejected.append("too_many_items")

    product_text = _searchable_product_text(raw)
    matched_bad_words: list[str] = []
    for word in config["bad_words"]:
        if word.casefold() in product_text and word not in matched_bad_words:
            matched_bad_words.append(word)
    rejected.extend(f"bad_word: {word}" for word in matched_bad_words)

    reasons = rejected + manual
    result = "rejected" if rejected else "manual_check" if manual else "passed"
    return {
        "filter_result": result,
        "rejection_reasons": reasons,
        "matched_bad_words": matched_bad_words,
        "normalized_regions": normalized_regions,
        "unmatched_regions": unmatched_regions,
        "delivery_addresses": addresses,
        "items_count": items_count,
        "purchaseTypeTitle": purchase_type_title,
        "rule_checks": {
            "price_150_400_thousand": not any(reason.startswith("price_") for reason in reasons),
            "region_allowed": not any(reason.startswith("region_") for reason in reasons),
            "items_count_at_most_10": not any(reason.startswith("items_count_") or reason == "too_many_items" for reason in reasons),
            "law_44fz_confirmed": not any(reason in {"law_not_determined", "not_44fz"} for reason in reasons),
            "no_bad_words": not matched_bad_words,
            "no_subsidy": not subsidy_found,
        },
        "special_conditions": "СУБСИДИИ" if subsidy_found else "",
        "warnings": ["Обнаружено упоминание субсидии; это особое условие, а не причина отклонения"] if subsidy_found else [],
    }
