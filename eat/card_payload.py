"""Сбор структурированных данных полной карточки ЕАТ без DOM/OCR."""
from __future__ import annotations

import json
from typing import Any


def _is_purchase(value: Any, purchase_id: str) -> bool:
    return isinstance(value, dict) and (
        str(value.get("id")) == purchase_id
        or str(value.get("tradeLotId")) == purchase_id
    )


def card_purchase_payloads(value: Any, purchase_id: str) -> list[dict[str, Any]]:
    """Вернуть варианты объекта закупки, сохранив соседний блок customer.

    API карточки имеет форму ``{trade, customer, changes}``. Старый сборщик
    находил вложенный ``trade`` по UUID, но терял расположенный рядом
    структурированный объект ``customer``.
    """
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if _is_purchase(value, purchase_id):
            found.append(value)

        trade = value.get("trade")
        if _is_purchase(trade, purchase_id):
            combined = dict(trade)
            customer = value.get("customer")
            if isinstance(customer, dict):
                combined["customer"] = customer
            found.append(combined)

        for child in value.values():
            found.extend(card_purchase_payloads(child, purchase_id))
    elif isinstance(value, list):
        for child in value:
            found.extend(card_purchase_payloads(child, purchase_id))
    return found


def fullest_card_purchase(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Выбрать самый полный безопасно собранный вариант карточки."""
    return max(
        payloads,
        key=lambda item: len(json.dumps(item, ensure_ascii=False)),
        default={},
    )
