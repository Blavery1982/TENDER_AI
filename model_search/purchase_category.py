"""Детерминированная категория закупки до поиска моделей и цен."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from filters.position_kind import goods_position

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = json.loads((ROOT / "config/customer_product_context.json").read_text(encoding="utf-8"))

CATEGORY_EXACT_MODEL = "Закупка по модели"
CATEGORY_MODEL_DISCOVERY = "Закупка с определением модели по параметрам"
CATEGORY_NO_MODEL = "Закупка товара без существующей модели"
ALLOWED_CATEGORIES = (CATEGORY_EXACT_MODEL, CATEGORY_MODEL_DISCOVERY, CATEGORY_NO_MODEL)


def _terms() -> tuple[str, ...]:
    return tuple(str(value).casefold().strip() for value in CONTEXT.get("non_model_goods", ()) if str(value).strip())


def _contains_term(text: str, term: str) -> bool:
    # Термины конфигурации могут быть фразами; границы не дают «кирпичу»
    # совпасть с частью другого слова.
    return bool(re.search(r"(?<![а-яёa-z0-9])" + re.escape(term) + r"(?![а-яёa-z0-9])", text, re.I))


def is_non_model_good(item: dict[str, Any], resolved: dict[str, Any] | None = None) -> bool:
    """Вернуть True только для явно известных массовых/оптовых товаров."""
    text = " ".join(str(item.get(key) or "") for key in
                    ("name", "item_name", "description", "eatTitle", "offerDescription"))
    if resolved:
        text += " " + str(resolved.get("product_name") or "")
    folded = text.casefold()
    return any(_contains_term(folded, term) for term in _terms())


def classify_purchase_category(item: dict[str, Any], resolved: dict[str, Any] | None = None) -> str:
    """Определить одну из трёх пользовательских категорий закупки.

    Для нетоварных позиций вызывающий контур сохраняет собственный position-kind
    stop. Категория 3 применяется только по явному семантическому словарю,
    поэтому отсутствие модели само по себе не превращает позицию в массовый товар.
    """
    resolved = resolved or {}
    explicit_fields = ("customer_required_model", "model", "article", "trademark",
                       "commercial_designation", "brand")
    explicit_in_item = any(item.get(field) for field in explicit_fields)
    evidence = resolved.get("customer_model_evidence") or {}
    evidence_source = str(evidence.get("model_source") or "")
    evidence_field = str(evidence.get("source_field") or "")
    explicit_document = evidence_source in {"PRICE_JUSTIFICATION", "CONTRACT_DOCUMENT",
                                            "EAT_SPECIFICATION"}
    explicit_label = evidence_field in {"model", "brand", "article", "trademark",
                                        "commercial_designation", "popup"}
    if explicit_in_item or explicit_document or explicit_label:
        return CATEGORY_EXACT_MODEL
    if is_non_model_good(item, resolved):
        return CATEGORY_NO_MODEL
    return CATEGORY_MODEL_DISCOVERY


def validate_purchase_category(value: Any) -> str:
    if value not in ALLOWED_CATEGORIES:
        raise ValueError(f"Недопустимая категория закупки: {value!r}")
    return value


__all__ = [
    "CATEGORY_EXACT_MODEL", "CATEGORY_MODEL_DISCOVERY", "CATEGORY_NO_MODEL",
    "ALLOWED_CATEGORIES", "classify_purchase_category", "is_non_model_good",
    "validate_purchase_category",
]
