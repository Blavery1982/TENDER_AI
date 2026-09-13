"""Нормализация, дедупликация и ранжирование результатов поиска рынка."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from suppliers.verification import HIGH_RISK, INSUFFICIENT, MANUAL, PASSED
from model_search.product_evidence import manufacturer_model_in_text

PRICE_FIRE = "🔥 ПРИОРИТЕТ — публичная цена уже проходит целевой порог"
PRICE_HIGH = "🟢 ВЫСОКИЙ ПРИОРИТЕТ ДЛЯ ЗАПРОСА ЦЕНЫ"
PRICE_VOLUME = "🟡 ЗАПРОСИТЬ СКИДКУ НА ОБЪЁМ"
PRICE_RESERVE = "⚪ РЕЗЕРВНЫЙ ПОСТАВЩИК"
SUPPLIER_TARGET_DISCOUNT_PERCENT = 15

MARKETPLACES = {"ozon.ru", "wildberries.ru", "market.yandex.ru"}
FEDERAL = {"vseinstrumenti.ru", "citilink.ru", "xcom-shop.ru", "komus.ru"}


def supplier_target_price(customer_unit_price: float,
                          discount_percent: float = SUPPLIER_TARGET_DISCOUNT_PERCENT) -> float:
    return round(float(customer_unit_price) * (1 - float(discount_percent) / 100), 2)


def normalize_model(value: str | None) -> str:
    """Сохраняет значимые буквы/цифры модели и выравнивает дефисы."""
    value = (value or "").upper().replace("–", "-").replace("—", "-")
    return re.sub(r"[^A-Z0-9]", "", value)


def exact_model_match(text: str | None, target: str) -> bool:
    """Match the manufacturer model, not a seller's internal SKU."""
    return manufacturer_model_in_text(text,target)


def normalize_product_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port else ""
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "yclid", "gclid", "from", "ref"}
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if k.lower() not in tracking))
    return urlunsplit(((parts.scheme or "https").lower(), host + port, path, query, ""))


def classify_source(domain: str, *, local: bool = False) -> str:
    domain = domain.lower().removeprefix("www.")
    if domain in MARKETPLACES:
        return "marketplace"
    if domain in FEDERAL:
        return "federal"
    if local:
        return "local"
    return "professional"


def deduplicate_offers(offers: list[dict]) -> list[dict]:
    unique = {}
    for offer in offers:
        item = dict(offer)
        item["product_url"] = normalize_product_url(item["product_url"])
        key = (item.get("domain", "").lower().removeprefix("www."), item["product_url"],
               normalize_model(item.get("model")))
        current = unique.get(key)
        if current is None or (current.get("public_price") is None and item.get("public_price") is not None):
            unique[key] = item
    return list(unique.values())


def price_priority(offer: dict, threshold: float) -> str:
    price = offer.get("public_price")
    if price is not None and float(price) <= threshold:
        return PRICE_FIRE
    status = offer.get("verification_status")
    availability = offer.get("availability")
    if price is not None and float(price) <= threshold * 1.20:
        return PRICE_HIGH
    if status == PASSED and availability in {"in_stock", "available", "to_order"}:
        return PRICE_VOLUME
    if price is None and availability in {"in_stock", "available", "to_order", "price_on_request"}:
        return PRICE_VOLUME
    return PRICE_RESERVE


def prepare_offers(offers: list[dict], target_model: str, threshold: float) -> list[dict]:
    prepared = []
    for source in deduplicate_offers(offers):
        item = dict(source)
        item["exact_model"] = exact_model_match(item.get("product_name") or item.get("model"), target_model)
        item["purchase_price"] = None
        item["price_priority"] = price_priority(item, threshold)
        price = item.get("public_price")
        item["target_price_difference"] = (round(float(price) - threshold, 2)
                                           if price is not None else None)
        item["target_price_difference_percent"] = (
            round((float(price) / threshold - 1) * 100, 2)
            if price is not None and threshold else None)
        prepared.append(item)
    return prepared


def build_call_lists(offers: list[dict]) -> dict:
    eligible = [x for x in offers if x.get("exact_model") and not x.get("stale_or_wrong")]
    red = [x for x in eligible if x.get("verification_status") == HIGH_RISK]
    yellow = [x for x in eligible if x.get("verification_status") in {MANUAL, INSUFFICIENT}]
    green = [x for x in eligible if x.get("verification_status") == PASSED]
    priority_order = {PRICE_FIRE: 0, PRICE_HIGH: 1, PRICE_VOLUME: 2, PRICE_RESERVE: 3}
    def key(x):
        kad_risk = {"informational": 0, "attention": 1, "unknown": 2,
                    "elevated": 3, "bankruptcy": 4}.get(
                        (x.get("arbitration_cases") or {}).get("risk_level"), 2)
        return (kad_risk, priority_order.get(x.get("price_priority"), 9),
                x.get("availability") not in {"in_stock", "available"},
                x.get("public_price") is None,
                x.get("public_price") or float("inf"))
    return {"suppliers_for_best_price_request": sorted(green, key=key),
            "requires_verification_before_work": sorted(yellow, key=key),
            "excluded_high_risk": red}
