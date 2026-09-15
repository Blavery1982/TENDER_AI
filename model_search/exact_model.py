"""Первичный поиск цены указанной модели, в том числе при разрешённой замене."""
from __future__ import annotations

import re
from filters.position_kind import goods_position, classify_position

from model_search.price_readiness import (identifier_is_ambiguous, extract_direct_identifier,
                                          PACKAGED_CUSTOMER_DESIGNATION)
from model_search.product_evidence import same_exact_model, extract_skus
from model_search.search_mode import MODEL, LABEL, PERMISSION, DENIAL, UNCERTAIN
from documents.requirement_sources import popup_requirement_text

EXACT_MODEL = "EXACT_MODEL"
REPLACEMENT = re.compile(
    r"(?i)(?:допуска\w*|разреш\w*|можно)\s+(?:предлож\w*|постав\w*|использ\w*)?\s*"
    r"(?:товар\w*\s+)?(?:друг\w*|ин\w*)\s+(?:модел\w*|марк\w*)|"
    r"(?:замена(?:\s+модели)?|другая\s+модель)\s+(?:допускается|разрешена)"
)


def _same_identity(left, right):
    if same_exact_model(left, right):
        return True
    left_skus, right_skus = extract_skus(left), extract_skus(right)
    if len(left_skus) != 1 or len(right_skus) != 1 or not same_exact_model(left_skus[0], right_skus[0]):
        return False
    # SKU из карточки может быть короче полного названия из документа.
    # При этом одинаковый SKU у двух разных марок не снимает конфликт.
    left_brand = left.split()[0] if len(left.split()) > 1 else None
    right_brand = right.split()[0] if len(right.split()) > 1 else None
    return not (left_brand and right_brand and left_brand.casefold() != right_brand.casefold())


def resolve_exact_model(item, documents, *, item_number=1, items=None):
    """Точная ветка определяется после общего сбора требований позиции."""
    if not goods_position(item):
        return None
    from documents.item_sources import resolve_item_sources
    resolved = resolve_item_sources(item, documents, item_number=item_number, items=items)
    return resolved if resolved.get('model_search_mode') == EXACT_MODEL else None
