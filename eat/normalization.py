"""Нормализация реальных позиций спецификации ЕАТ."""

from __future__ import annotations

from typing import Any


def normalize_lot_item(item: dict[str, Any]) -> dict[str, Any]:
    """Сохранить точные товарные поля позиции без данных интерфейса ЕАТ."""
    eat_title = item.get("eatTitle")
    item_name = item.get("name")
    display_parts = [
        value.strip().rstrip(".")
        for value in (eat_title, item_name)
        if isinstance(value, str) and value.strip()
    ]
    return {
        "display_name": ". ".join(display_parts),
        "offer_description": item.get("description"),
        "eat_code": item.get("eatCode"),
        "eat_title": eat_title,
        "okpd2": item.get("okpd2Code"),
        "okpd2_title": item.get("okpd2Title"),
        "unit": item.get("okeiTitle"),
        "unit_code": item.get("okeiCode"),
        "quantity": item.get("quantity"),
        "unit_price": item.get("unitPrice"),
        "total_price": item.get("sum"),
        "quantity_undefined": item.get("quantityUndefined"),
        "country_of_origin": item.get("countryOfOrigin"),
        "country_code_of_origin": item.get("countryCodeOfOrigin"),
        "characteristics": item.get("characteristics") or [],
    }
