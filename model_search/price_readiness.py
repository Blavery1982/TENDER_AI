"""Classify whether a procurement-provided product identifier is price-search ready.

This is identity validation, not technical compliance.  Seller pages still have
to prove the same exact identifier before their price can be accepted.
"""
from __future__ import annotations

import re
from typing import Any

from model_search.product_evidence import extract_skus
from filters.position_kind import goods_position, blocked_position

PRICE_READINESS_VERSION = 2
PRICE_SEARCH_READY = "PRICE_SEARCH_READY"
MODEL_DISCOVERY_REQUIRED = "MODEL_DISCOVERY_REQUIRED"
MODEL_IDENTIFIER_REVIEW_REQUIRED = "MODEL_IDENTIFIER_REVIEW_REQUIRED"

PROPERTY_CONTEXT = re.compile(
    r"(?i)(?:тип\s+(?:клемм|разъ[её]м|кабел|интерфейс|оборудован)|"
    r"категори|стандарт|напряжени|мощност|ёмкост|емкост|размер|диаметр|"
    r"количество\s+пар|версия)\s*[:—-]?\s*$"
)
GENERIC_IDENTIFIER = re.compile(
    r"(?i)^(?:FASTON\s*F?\d|RJ-?\d+|USB|HDMI|DisplayPort|UTP(?:\s*\d+PR)?|"
    r"AGM|LED|OLED|QLED|Full\s*HD|4K(?:\s*UHD)?|IP\d+)$"
)
INCOMPLETE = re.compile(r"(?i)(?:[-/.]0|[-/.]|\.{2,}|…)$")
GENERIC_LEADING = re.compile(
    r"(?i)^(?:мощност\w*|напряжени\w*|размер\w*|диаметр\w*|длина|ширина|высота|"
    r"ёмкост\w*|емкост\w*|частот\w*|количество|тип|категори\w*|версия|"
    r"предложени\w*|позици\w*|приложени\w*|таблиц\w*|страниц\w*)\b"
)
CYRILLIC_SKU = re.compile(
    r"(?<!\w)([А-ЯЁA-Z]{1,8}[а-яё]{0,4}(?:[-\s]\d{2,})(?:[-/]\d+)*(?:\s+[A-ZА-ЯЁ]\d+)?)(?!\w)"
)
CYRILLIC_DECIMAL_SKU = re.compile(
    r"(?<!\w)([А-ЯЁA-Z]{1,8}[а-яё]{0,4}-\d+[.,]\d+(?:[-/]\d+)*(?:\s+[A-ZА-ЯЁ]\d+)?)(?!\w)"
)
NAMED_WITH_NUMBER = re.compile(
    r"(?<!\w)([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{2,}\s+\d{1,4})(?![\w.,/-])(?!\s*(?:[\"″”]|(?:г|кг|мм|см|мл|Вт|В|Гц)\b))",
)
LATIN_COMMERCIAL_NAME = re.compile(r"\b([A-Z]{3,}(?:\s+[A-Z][A-Za-z]+){1,3})\b")
BRANDED_CODE = re.compile(
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{2,}\s+(?:"
    r"[A-ZА-ЯЁ]\d{2,}[A-Za-zА-Яа-яЁё0-9/-]*|"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{1,7}-\d+(?:[.,]\d+)?(?:[-/]\d+)*)"
)
SPACED_BRAND_MODEL = re.compile(
    r"(?<!\w)([A-Z][A-Za-z]{1,}(?:\s+[A-Z][A-Za-z]{1,}){0,2}\s+"
    r"[A-Z]{1,6}\s+\d{1,5}(?:[-/.]\d+(?:[.,]\d+)?)*)"
)
BRAND_CYRILLIC_MODEL = re.compile(
    r"(?<!\w)([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){0,2}\s+"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{1,7}-\d+(?:[.,]\d+)?(?:[-/]\d+)*)"
)
# Ограниченный span для фасованных коммерческих обозначений. В отличие от
# общего поиска ``слово + число`` он сохраняет бренд/серию и единицу фасовки,
# но не захватывает произвольное предложение: нужны минимум два
# идентифицирующих слова с прописной буквы и число с известной единицей.
PACKAGED_CUSTOMER_DESIGNATION = re.compile(
    r"(?<!\w)((?:[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{2,}\s+){1,3}"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё]{1,}\s+\d{1,5}(?:[.,]\d+)?\s*"
    r"(?:кг|г|л|мл|мм|см|м|шт|Вт|В|Гц|дБ))(?!\w)"
)
PACKAGED_GENERIC_PREFIX = re.compile(
    r"(?i)^(?:клей|плиточн\w*|телевизор\w*|принтер\w*|кондиционер\w*|"
    r"батаре\w*|весы|шкаф\w*|товар\w*|издели\w*)\s+"
)


def _clean(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:—-")
    return text or None


def _customer_packaged_span(value: str) -> str | None:
    """Убрать известный префикс типа товара, сохранив span обозначения."""
    candidate = _clean(value)
    if not candidate:
        return None
    trimmed = PACKAGED_GENERIC_PREFIX.sub("", candidate)
    return _clean(trimmed)


def _property_context(text: str, start: int) -> bool:
    prefix = text[max(0, start - 45):start]
    return bool(PROPERTY_CONTEXT.search(prefix))


def identifier_is_ambiguous(identifier: str, evidence_text: str = "") -> tuple[bool, str | None]:
    value = _clean(identifier) or ""
    if not value:
        return True, "Идентификатор отсутствует"
    if re.match(r'(?i)^(?:DIN|ГОСТ|ISO)\b', value):
        return True, "Обозначение является стандартом или характеристикой товара"
    if GENERIC_IDENTIFIER.fullmatch(value):
        return True, "Обозначение является типом, стандартом или характеристикой товара"
    if re.match(r"(?i)^(?:ИКЗ|ОКПД2?|КТРУ|ИНН|КПП|ОКОПФ|ОКТМО|ОКПО|ОКВЭД|БИК|ОГРН)\b", value):
        return True, "Обозначение является служебным кодом закупки, а не моделью товара"
    if re.fullmatch(r"[А-ЯЁ]{2,8}\s+\d{6,}", value):
        return True, "Обозначение похоже на служебный регистрационный код, а не модель товара"
    if re.search(r"(?i)\b(?:FASTON\s*F?\d|RJ-?\d+|UTP(?:\s*\d+PR)?|24AWG)$", value):
        return True, "Обозначение заканчивается типом или стандартом, а не конкретной моделью товара"
    if GENERIC_LEADING.search(value):
        return True, "Обозначение является технической характеристикой товара"
    if " " in value and not re.search(r"\d", value) and value == value.upper():
        return True, "Обозначение похоже на тип или серию без конкретного артикула"
    if INCOMPLETE.search(value):
        return True, "Обозначение выглядит неполным или оборванным"
    position = evidence_text.casefold().find(value.casefold())
    if position >= 0 and _property_context(evidence_text, position):
        return True, "Обозначение найдено в значении технической характеристики, а не как модель товара"
    # Один точный буквенно-цифровой SKU — наиболее надёжный случай.
    if len(extract_skus(value)) == 1:
        return False, None
    # Кириллические артикулы российских производителей (например НПМ-40).
    if CYRILLIC_SKU.fullmatch(value):
        return False, None
    # Полное коммерческое название допустимо даже без классического SKU.
    if (LATIN_COMMERCIAL_NAME.fullmatch(value) or NAMED_WITH_NUMBER.fullmatch(value)
            or BRANDED_CODE.fullmatch(value) or SPACED_BRAND_MODEL.fullmatch(value)
            or BRAND_CYRILLIC_MODEL.fullmatch(value)):
        return False, None
    return True, "По обозначению нельзя однозначно идентифицировать конкретный товар"


def extract_direct_identifier(item: dict[str, Any]) -> dict[str, Any] | None:
    """Find a concrete identifier in the EAT position itself, before documents."""
    if not goods_position(item):
        return None

    def result(value: str, *, ambiguous: bool, reason: str | None,
               source_field: str) -> dict[str, Any]:
        # ``customer_model_raw`` — выбранный source span, а не сокращённый
        # SKU; это поле повторно используется exact search.
        evidence = {"source_document": "Спецификация ЕАТ", "source_page": None,
                    "source_field": source_field, "fragment": value}
        return {"identifier": value, "customer_model_raw": value,
                "customer_model_evidence": evidence, "ambiguous": ambiguous,
                "reason": reason, "source": "CUSTOMER_SPECIFICATION",
                "source_field": source_field}

    for field in ("model", "article", "sku", "customer_required_model"):
        value = _clean(item.get(field))
        if value:
            ambiguous, reason = identifier_is_ambiguous(value, str(item.get("description") or ""))
            return result(value, ambiguous=ambiguous, reason=reason, source_field=field)

    # Название/eatTitle проверяются раньше характеристик: модель товара в
    # заголовке не должна проиграть встреченному ниже типу клеммы/разъёма.
    texts = [str(item.get(key) or "") for key in
             ("name", "eatTitle", "description", "offerDescription", "offer_description",
              "additionalCharacteristics", "additional_characteristics")]
    text = "\n".join(value for value in texts if value.strip())
    if not text:
        return None
    first_lines = " ".join(line.strip() for line in text.splitlines()[:2] if line.strip())
    candidates: list[str] = []
    # Explicit model/article labels have priority, unless the label is itself a
    # property such as "тип клеммы" or "тип разъёма".
    for match in re.finditer(r"(?i)\b(?:модель|марка|артикул|SKU)\s*[:—-]\s*([^\n;|]+)", text):
        value = _clean(match.group(1))
        if value:
            candidates.append(value)
    # Сначала проверяем целиком надёжный source span. Это не безусловный
    # выбор самой длинной строки: шаблон ограничен товарным обозначением с
    # фасовкой, а прочие кандидаты по-прежнему проходят обычную валидацию.
    candidates.extend(span for span in
                      (_customer_packaged_span(match.group(1))
                       for match in PACKAGED_CUSTOMER_DESIGNATION.finditer(first_lines))
                      if span)
    candidates.extend(extract_skus(first_lines))
    candidates.extend(match.group(1) for match in SPACED_BRAND_MODEL.finditer(first_lines))
    candidates.extend(match.group(1) for match in BRAND_CYRILLIC_MODEL.finditer(first_lines))
    candidates.extend(match.group(1) for match in LATIN_COMMERCIAL_NAME.finditer(first_lines))
    # Prefer a Latin commercial form when a bilingual name repeats the product.
    named = [match.group(1) for match in NAMED_WITH_NUMBER.finditer(first_lines)]
    candidates.extend(sorted(named, key=lambda value: bool(re.search(r"[А-Яа-я]", value))))
    candidates.extend(match.group(1) for match in CYRILLIC_DECIMAL_SKU.finditer(first_lines))
    candidates.extend(match.group(1) for match in CYRILLIC_SKU.finditer(first_lines))
    seen: set[str] = set()
    ambiguous_candidate = None
    for candidate in candidates:
        value = _clean(candidate)
        key = str(value).casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        ambiguous, reason = identifier_is_ambiguous(value, text)
        if not ambiguous:
            return result(value, ambiguous=False, reason=None, source_field="position_text")
        if reason and "похоже на тип или серию" in reason:
            ambiguous_candidate = result(value, ambiguous=True, reason=reason,
                                          source_field="position_text")
    return ambiguous_candidate


def extract_document_identifiers(text: str) -> list[dict[str, Any]]:
    """Extract market-searchable identifiers from already scoped document text.

    This performs identity screening only. Association of the text with a
    procurement position remains the responsibility of ``item_sources``.
    """
    source = str(text or "")
    if not source.strip():
        return []
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, bool]] = set()
    # Inspect rows independently: a table/appendix can contain several models,
    # and that must result in review rather than silently selecting the first.
    for line in source.splitlines():
        if not line.strip():
            continue
        candidate = extract_direct_identifier({"name": line})
        if candidate:
            if GENERIC_LEADING.search(str(candidate.get("identifier") or "")):
                continue
            key = (str(candidate.get("identifier") or "").casefold(),
                   bool(candidate.get("ambiguous")))
            if key not in seen:
                seen.add(key)
                found.append(candidate)
    # A one-line or layout-damaged extraction can still be parsed as a whole.
    if not found:
        candidate = extract_direct_identifier({"name": source})
        if candidate and not GENERIC_LEADING.search(str(candidate.get("identifier") or "")):
            found.append(candidate)
    return found


def extract_document_identifier(text: str) -> dict[str, Any] | None:
    """Backward-compatible singular view used by callers outside the resolver."""
    candidates = extract_document_identifiers(text)
    return candidates[0] if candidates else None


def _document_readiness(candidates: list[dict[str, Any]], source: str) -> dict[str, Any] | None:
    selected = [row for row in candidates if row.get("model_source") == source]
    exact: dict[str, dict[str, Any]] = {}
    ambiguous = []
    for row in selected:
        identifier = _clean(row.get("identifier"))
        if not identifier:
            continue
        if row.get("ambiguous"):
            ambiguous.append(row)
        else:
            exact.setdefault(identifier.casefold(), row)
    if len(exact) > 1:
        values = [row["identifier"] for row in exact.values()]
        return {"ambiguous": True, "identifier": None,
                "reason": "В одном источнике найдены разные модели: " + "; ".join(values),
                "evidence": [row.get("evidence") for row in exact.values() if row.get("evidence")]}
    if exact:
        row = next(iter(exact.values()))
        return {"ambiguous": False, "identifier": row["identifier"], "reason": None,
                "evidence": [row.get("evidence")] if row.get("evidence") else []}
    if ambiguous:
        row = ambiguous[0]
        return {"ambiguous": True, "identifier": row.get("identifier"),
                "reason": row.get("reason"),
                "evidence": [row.get("evidence")] if row.get("evidence") else []}
    return None


def classify_price_search_readiness(item: dict[str, Any],
                                    resolved: dict[str, Any]) -> dict[str, Any]:
    """Return price-search readiness without checking model/TZ compliance."""
    if not goods_position(item, resolved):
        blocked = blocked_position(item if item else resolved)
        return {**blocked, "price_readiness_version": PRICE_READINESS_VERSION,
                "classification": blocked["model_search_mode"], "price_search_ready": False,
                "identifier": None, "model_value": None, "reason": blocked["model_mode_reason"],
                "evidence": [], "compliance_before_price_search": False}
    if resolved.get("model_search_mode") == "EXACT_MODEL":
        model = resolved["original_model"]
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": PRICE_SEARCH_READY, "price_search_ready": True,
                "identifier": model, "model_value": model,
                "customer_required_model": model, "price_justification_model": None,
                "customer_model_raw": resolved.get("customer_model_raw") or model,
                "customer_model_evidence": resolved.get("customer_model_evidence"),
                "model_search_mode": "EXACT_MODEL", "model_source": resolved["model_source"],
                "identifier_source_field": "resolved_customer_model",
                "reason": resolved["model_mode_reason"], "evidence": resolved["model_evidence"],
                "compliance_before_price_search": False}
    # Разобранная позиция является единственным источником решения.
    # Повторное извлечение не вправе отменять ручную проверку основного этапа.
    if resolved.get('source_resolution_version'):
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED,
                "price_search_ready": False, "identifier": None,
                "model_value": None, "customer_required_model": None,
                "reason": resolved.get('model_mode_reason') or 'Модель не установлена',
                "evidence": [], "compliance_before_price_search": True}
    direct = extract_direct_identifier(item)
    if direct:
        direct_evidence = [{"source_document": "Спецификация ЕАТ", "source_page": None,
                            "source_field": direct["source_field"],
                            "fragment": direct["identifier"]}]
        if direct["ambiguous"]:
            return {"price_readiness_version": PRICE_READINESS_VERSION,
                    "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED,
                    "price_search_ready": False, "identifier": direct["identifier"],
                    "model_value": direct["identifier"],
                    "customer_required_model": direct["identifier"],
                    "customer_model_raw": direct.get("customer_model_raw"),
                    "customer_model_evidence": direct.get("customer_model_evidence"),
                    "price_justification_model": None, "model_source": direct["source"],
                    "identifier_source_field": direct["source_field"], "reason": direct["reason"],
                    "evidence": direct_evidence, "compliance_before_price_search": False}
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": PRICE_SEARCH_READY, "price_search_ready": True,
                "identifier": direct["identifier"], "model_value": direct["identifier"],
                "customer_required_model": direct["identifier"],
                "customer_model_raw": direct.get("customer_model_raw"),
                "customer_model_evidence": direct.get("customer_model_evidence"),
                "price_justification_model": None, "model_source": direct["source"],
                "identifier_source_field": direct["source_field"],
                "reason": "Конкретная модель/артикул указаны в позиции ЕАТ", "evidence": direct_evidence,
                "compliance_before_price_search": False}

    customer = _clean(resolved.get("customer_required_model") or resolved.get("original_model"))
    # Старый document resolver мог оборвать десятичную часть артикула на
    # запятой. Берём более полный идентификатор только когда он буквально есть
    # в сохранённом тексте документа — это доказательство, а не догадка.
    resolved_text = str(resolved.get("customer_specification_text") or "")
    documented: list[str] = []
    documented.extend(match.group(1) for match in BRAND_CYRILLIC_MODEL.finditer(resolved_text))
    documented.extend(match.group(1) for match in SPACED_BRAND_MODEL.finditer(resolved_text))
    documented.extend(match.group(1) for match in CYRILLIC_DECIMAL_SKU.finditer(resolved_text))
    documented.extend(match.group(1) for match in CYRILLIC_SKU.finditer(resolved_text))
    documented = [value for value in dict.fromkeys(_clean(value) for value in documented) if value]
    exact_documented = next((value for value in documented
                             if not identifier_is_ambiguous(value, resolved_text)[0]), None)
    if exact_documented and (not customer or customer.casefold() in exact_documented.casefold()):
        customer = exact_documented
    if customer:
        source = resolved.get("model_source") or "CONTRACT_DOCUMENT"
        evidence = str(resolved.get("customer_specification_text") or "")
        ambiguous, reason = identifier_is_ambiguous(customer, evidence)
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED if ambiguous else PRICE_SEARCH_READY,
                "price_search_ready": not ambiguous, "identifier": customer, "model_value": customer,
                "customer_required_model": customer, "price_justification_model": None,
                "model_source": source, "identifier_source_field": "resolved_customer_model",
                "reason": reason or "Конкретная модель/артикул указаны в документах заказчика",
                "evidence": resolved.get("model_evidence") or [],
                "compliance_before_price_search": False}

    document_candidates = [row for row in resolved.get("document_model_candidates") or []
                           if isinstance(row, dict)]
    # Contract/TZ/specification are materials of the customer. An exact model
    # found there is customer_required_model even if the legacy parser missed it.
    contract = _document_readiness(document_candidates, "CONTRACT_DOCUMENT")
    if contract:
        identifier = contract.get("identifier")
        ambiguous = contract["ambiguous"]
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED if ambiguous else PRICE_SEARCH_READY,
                "price_search_ready": not ambiguous, "identifier": identifier,
                "model_value": identifier, "customer_required_model": identifier,
                "price_justification_model": None, "model_source": "CONTRACT_DOCUMENT",
                "identifier_source_field": "document_model_candidates",
                "reason": contract.get("reason") or "Конкретная модель указана в документе заказчика",
                "evidence": contract.get("evidence") or [],
                "compliance_before_price_search": False}

    has_other_document_identity = any(
        isinstance(row, dict) and row.get("model_source") == "OTHER_DOCUMENT"
        for row in (resolved.get("document_model_candidates") or [])
    )
    if resolved.get("source_resolution_version", 0) >= 4 and not has_other_document_identity:
        valid = bool(resolved.get("requirements")) and not (resolved.get("source_conflicts") or resolved.get("source_warnings"))
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_DISCOVERY_REQUIRED if valid else MODEL_IDENTIFIER_REVIEW_REQUIRED,
                "price_search_ready": False, "identifier": None, "model_value": None,
                "customer_required_model": None, "price_justification_model": resolved.get("price_justification_model"),
                "model_source": "NOT_FOUND", "identifier_source_field": None,
                "reason": "Модель заказчиком не указана; требуется подбор по обязательным характеристикам" if valid else "Нет непротиворечивых обязательных требований из разрешённых источников",
                "evidence": [], "compliance_before_price_search": True}

    explicit_price = resolved.get("price_justification_model") or resolved.get("supplier_baseline_model")
    reference_price = (resolved.get("pricing_reference_model")
                       if not resolved.get("customer_required_model") and not resolved.get("original_model")
                       else None)
    price = _clean(explicit_price or (
        reference_price if resolved.get("model_search_mode") != "MODEL_MODE_REVIEW_REQUIRED" else None
    ))
    if price:
        ambiguous, reason = identifier_is_ambiguous(price)
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED if ambiguous else PRICE_SEARCH_READY,
                "price_search_ready": not ambiguous, "identifier": price, "model_value": price,
                "customer_required_model": None, "price_justification_model": price,
                "model_source": "PRICE_JUSTIFICATION", "identifier_source_field": "price_justification_model",
                "reason": reason or "Конкретная модель указана только в обосновании цены",
                "evidence": resolved.get("price_model_evidence") or [],
                "compliance_before_price_search": False}

    documented_price = _document_readiness(document_candidates, "PRICE_JUSTIFICATION")
    if documented_price:
        identifier = documented_price.get("identifier")
        ambiguous = documented_price["ambiguous"]
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED if ambiguous else PRICE_SEARCH_READY,
                "price_search_ready": not ambiguous, "identifier": identifier,
                "model_value": identifier, "customer_required_model": None,
                "price_justification_model": identifier, "model_source": "PRICE_JUSTIFICATION",
                "identifier_source_field": "document_model_candidates",
                "reason": documented_price.get("reason") or "Конкретная модель указана только в обосновании цены/КП",
                "evidence": documented_price.get("evidence") or [],
                "compliance_before_price_search": False}

    other = _document_readiness(document_candidates, "OTHER_DOCUMENT")
    if other:
        identifier = other.get("identifier")
        ambiguous = other["ambiguous"]
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED if ambiguous else PRICE_SEARCH_READY,
                "price_search_ready": not ambiguous, "identifier": identifier,
                "model_value": identifier, "customer_required_model": None,
                "price_justification_model": None, "model_source": "OTHER_DOCUMENT",
                "identifier_source_field": "document_model_candidates",
                "reason": other.get("reason") or "Конкретная модель найдена в приложенном документе; обязательность для заказчика не доказана",
                "evidence": other.get("evidence") or [],
                "compliance_before_price_search": False}

    if reference_price:
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED,
                "price_search_ready": False, "identifier": _clean(reference_price),
                "model_value": _clean(reference_price),
                "customer_required_model": None, "price_justification_model": _clean(reference_price),
                "model_source": "PRICE_JUSTIFICATION", "identifier_source_field": "pricing_reference_model",
                "reason": "Привязка модели из документа к позиции неоднозначна",
                "evidence": resolved.get("price_model_evidence") or [],
                "compliance_before_price_search": False}

    position_text = "\n".join(str(item.get(key) or "") for key in
                              ("description", "offerDescription", "offer_description", "name", "eatTitle"))
    if re.search(r"(?i)\b(?:модель|артикул|SKU)\b", position_text):
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED,
                "price_search_ready": False, "identifier": None, "model_value": None,
                "customer_required_model": None, "price_justification_model": None,
                "model_source": resolved.get("model_source") or "CUSTOMER_SPECIFICATION",
                "identifier_source_field": "position_text",
                "reason": "В позиции упомянут идентификатор, но конкретная модель не выделена однозначно",
                "evidence": [],
                "compliance_before_price_search": False}
    if resolved.get("source_conflicts"):
        return {"price_readiness_version": PRICE_READINESS_VERSION,
                "classification": MODEL_IDENTIFIER_REVIEW_REQUIRED,
                "price_search_ready": False, "identifier": None, "model_value": None,
                "customer_required_model": None, "price_justification_model": None,
                "model_source": "NOT_FOUND", "identifier_source_field": None,
                "reason": "Источники позиции содержат противоречия", "evidence": [],
                "compliance_before_price_search": False}
    return {"price_readiness_version": PRICE_READINESS_VERSION,
            "classification": MODEL_DISCOVERY_REQUIRED,
            "price_search_ready": False, "identifier": None, "model_value": None,
            "customer_required_model": None, "price_justification_model": None,
            "model_source": "NOT_FOUND", "identifier_source_field": None,
            "reason": "Конкретная модель нигде не указана; есть только характеристики", "evidence": [],
            "compliance_before_price_search": False}
