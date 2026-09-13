"""Conservative, position-local search policy. No network or document parsing."""
from __future__ import annotations

import re
from typing import Any

EXACT_MODEL_ONLY = "EXACT_MODEL_ONLY"
EXACT_MODEL_OR_EQUIVALENT = "EXACT_MODEL_OR_EQUIVALENT"
MODEL_DISCOVERY_REQUIRED = "MODEL_DISCOVERY_REQUIRED"
MODEL_MODE_REVIEW_REQUIRED = "MODEL_MODE_REVIEW_REQUIRED"
CUSTOMER_EXACT_MODEL = "CUSTOMER_EXACT_MODEL"
CUSTOMER_EXACT_MODEL_RU = "ТОЧНАЯ МОДЕЛЬ ЗАКАЗЧИКА"

TOKEN = r"(?=[A-Za-zА-Яа-я0-9/.-]*[A-Za-zА-Яа-я])(?=[A-Za-zА-Яа-я0-9/.-]*\d)[A-Za-zА-Яа-я0-9]+(?:[-/.][A-Za-zА-Яа-я0-9]+)*"
CODE = re.compile(r"(?i)\b(?:артикул|арт\.|код(?:\s+позиции)?|ОКПД2|номер\s+закупки|внутренний\s+код)\s*[:№#-]?\s*\S+")
MODEL = re.compile(r"\b(?:[A-Z][a-zA-Z]+\s+){1,3}" + TOKEN + r"\b")
LABEL = re.compile(r"(?i)\bмодель\s*[:—-]?\s*((?:[a-zа-я]+\s+){0,3}" + TOKEN + r")\b")
PERMISSION = re.compile(r"(?i)\b(?:или\s+)?(?:эквивалент(?:а|ы|ов|ом|ный)?|аналог(?:а|и|ов|ом|ичный)?)\b|допускается\s+(?:замена|другая модель)|разрешается\s+(?:замена|другая модель)")
DENIAL = re.compile(r"(?i)(?:не допуска\w*|запрещ\w*)\s+(?:эквивалент\w*|аналог\w*|замена)|(?:эквивалент\w*|аналог\w*|замена)\s+(?:не допуска\w*|запрещ\w*)|без\s+(?:замены|аналогов|эквивалентов)")
UNCERTAIN = re.compile(r"(?i)\b(?:возможно|предположительно|ориентировочно|например|типа|желательно|по согласованию)\b|\?")


def determine_model_search_mode(item: dict[str, Any]) -> dict[str, Any]:
    """Use only this position's specification, never price justification or lot text.

    Unlabelled mixed letter/digit tokens need a recognisable brand context;
    unresolved tokens and conditional wording are sent for review.
    """
    if isinstance(item.get("source_resolution_version"), int) and item["source_resolution_version"] >= 2:
        keys = ("model_search_mode", "original_model", "model_mode_reason", "model_discovery_allowed",
                "customer_required_model", "price_justification_model", "model_source", "model_evidence",
                "price_model_evidence", "supplier_baseline_model", "supplier_baseline_status",
                "supplier_prices_required", "distinct_suppliers_required", "alternative_policy",
                "pricing_reference_model", "source_resolution_version", "source_warnings", "source_conflicts")
        return {key: item.get(key) for key in keys}
    texts = [str(item[k]) for k in ("item_name", "name", "eatTitle", "description", "specification", "technical_description") if item.get(k)]
    for row in item.get("structured_requirements") or item.get("requirements") or []:
        texts.append(f"{row.get('parameter') or row.get('requirement_name') or ''}: {row.get('required_value', row.get('value', ''))}")
    text = "\n".join(dict.fromkeys(texts))
    clean = CODE.sub("", text)
    models = list(dict.fromkeys(m.group(0).strip() for m in MODEL.finditer(clean)))
    for match in LABEL.finditer(clean):
        value = match.group(1).strip()
        if not any(value.casefold() in model.casefold() for model in models):
            models.append(value)
    denied = bool(DENIAL.search(clean))
    permission_text = DENIAL.sub("", clean)
    allowed = bool(PERMISSION.search(permission_text))
    remainder = clean
    for model in models:
        remainder = remainder.replace(model, "")
    # Technical standards and units are not product identities.
    remainder = re.sub(r"(?i)\b(?:IP\d+|\d+[.,]?\d*\s*(?:кВт|Вт|В|Гц|мм|см|м|дБ)|(?:ГОСТ|ISO|DIN)\s*[\d.-]+)\b", "", remainder)
    ambiguous = bool(UNCERTAIN.search(clean) or (denied and allowed) or len(models) > 1)
    ambiguous = ambiguous or bool(re.search(r"\b" + TOKEN + r"\b", remainder))
    ambiguous = ambiguous or bool(re.search(r"(?i)\bмодель\b", remainder) and not models)
    if ambiguous:
        mode, reason = MODEL_MODE_REVIEW_REQUIRED, "Модель или условия замены требуют проверки"
    elif models:
        mode = EXACT_MODEL_OR_EQUIVALENT if allowed else EXACT_MODEL_ONLY
        reason = "Замена явно разрешена" if allowed else "Указана модель без разрешения замены"
    else:
        mode, reason = MODEL_DISCOVERY_REQUIRED, "Конкретная модель не указана"
    return {"model_search_mode": mode, "original_model": models[0] if len(models) == 1 else None,
            "model_mode_reason": reason, "model_discovery_allowed": mode in (EXACT_MODEL_OR_EQUIVALENT, MODEL_DISCOVERY_REQUIRED)}


def blocked_discovery_result(decision: dict[str, Any]) -> dict[str, Any]:
    """Retain the requested model without inventing compliance or availability."""
    original = decision["original_model"]
    candidates = ([{"exact_model": original, "model_name": original,
                    "candidate_source": "customer_specification", "status": "not_confirmed",
                    "technical_status": "not_confirmed"}] if original else [])
    return {**decision, "search_status": "review_required" if decision["model_search_mode"] == MODEL_MODE_REVIEW_REQUIRED else "skipped_exact_model_only",
            "queries_used": [], "live_queries_count": 0, "candidates": candidates,
            "candidates_found": len(candidates), "candidates_checked": 0,
            "fully_compliant_count": 0, "selected_model": None, "warnings": [decision["model_mode_reason"]]}


def customer_exact_model_policy(decision: dict[str, Any], *, exact_model_match: bool) -> dict[str, Any]:
    """Decide whether characteristic-by-characteristic compliance is necessary.

    An exact SKU named by the customer, with no allowed replacement, is the
    product identity itself.  A seller page must still independently confirm
    the exact SKU; similar models never qualify for this fast path.
    """
    customer_model = decision.get("original_model") or decision.get("customer_required_model")
    specification = str(decision.get("customer_specification_text") or "")
    model_lines = [line for line in specification.splitlines()
                   if customer_model and str(customer_model).casefold() in line.casefold()]
    clear_model_evidence = (not specification or any(not UNCERTAIN.search(line) for line in model_lines))
    source_resolved_exact = bool(
        isinstance(decision.get("source_resolution_version"), int)
        and decision["source_resolution_version"] >= 2
        and customer_model
        and decision.get("model_evidence")
        and not decision.get("source_warnings")
        and not decision.get("source_conflicts")
        and not PERMISSION.search(DENIAL.sub("", specification))
        and clear_model_evidence
    )
    fast_path = bool(customer_model and exact_model_match is True and (
        decision.get("model_search_mode") == EXACT_MODEL_ONLY or source_resolved_exact
    ))
    if fast_path:
        return {
            "compliance_required": False,
            "compliance_code": CUSTOMER_EXACT_MODEL,
            "compliance_status": CUSTOMER_EXACT_MODEL_RU,
            "reason": "Заказчик указал точную модель без права замены; карточка продавца подтвердила exact-model match",
        }
    return {
        "compliance_required": True,
        "compliance_code": None,
        "compliance_status": None,
        "reason": "Нужна полная проверка обязательных требований ТЗ",
    }
