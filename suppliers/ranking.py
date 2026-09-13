"""Формирование КП1/КП2/КП3 без подмены публичной цены закупочной."""
from __future__ import annotations

from suppliers.verification import PASSED, MANUAL

CATEGORY_TO_QUOTE = {"marketplace": "kp1", "federal_or_specialist": "kp2", "local": "kp3"}


def select_quotes(suppliers: list[dict], threshold: float) -> dict:
    quotes = {"kp1": None, "kp2": None, "kp3": None}
    for category, key in CATEGORY_TO_QUOTE.items():
        eligible = [x for x in suppliers if x.get("source_category") == category
                    and x.get("verification_status") == PASSED
                    and x.get("exact_model") is True
                    and x.get("product_page_available") is True
                    and x.get("public_price") is not None
                    and float(x["public_price"]) <= threshold]
        if eligible:
            winner = min(eligible, key=lambda x: float(x["public_price"]))
            quotes[key] = {"supplier_name": winner["supplier_name"],
                           "public_price": winner["public_price"],
                           "purchase_price": None, "product_url": winner["product_url"]}
    return quotes


def price_request_candidates(suppliers: list[dict], threshold: float, quantity: float,
                             limit: int = 3) -> list[dict]:
    result = []
    for x in suppliers:
        if x.get("verification_status", "").startswith("🔴"):
            continue
        price = x.get("public_price")
        reason = x.get("price_request_reason")
        if not reason and price is not None and float(price) > threshold:
            reason = f"Публичная цена выше порога; запросить актуальную цену на {quantity:g} шт."
        if not reason and x.get("availability") in {"price_on_request", "to_order"}:
            reason = f"Цена не опубликована; запросить предложение на {quantity:g} шт."
        if not reason and x.get("verification_status") == MANUAL:
            reason = "Требуется ручная проверка поставщика перед оплатой"
        if reason:
            if x.get("verification_status") == MANUAL:
                reason += "; ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА ПОСТАВЩИКА ПЕРЕД ОПЛАТОЙ"
            result.append({"supplier_name": x["supplier_name"], "product_url": x["product_url"],
                           "public_price": price, "purchase_price": None, "reason": reason,
                           "verification_status": x["verification_status"]})
    status_priority = {PASSED: 0, MANUAL: 1}
    result.sort(key=lambda x: (status_priority.get(x["verification_status"], 2),
                               x["public_price"] is None,
                               x["public_price"] if x["public_price"] is not None else float("inf")))
    return result[:limit]

