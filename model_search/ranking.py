"""Ранжирование моделей перед отдельным, будущим supplier_search."""
from __future__ import annotations

from typing import Any

from model_search.market_eligibility import market_eligible


def supplier_search_eligible(candidate: dict[str, Any]) -> bool:
    """Цена не является условием допуска модели к поиску поставщиков."""
    return candidate.get("technical_status") == "fully_compliant" and market_eligible(candidate)


def fully_eligible(candidate: dict[str, Any]) -> bool:
    """Обратная совместимость: технический и рыночный допуск, но не ценовой."""
    return supplier_search_eligible(candidate)


def _public_price(candidate: dict[str, Any]) -> float | None:
    value = candidate.get("public_price_reference", candidate.get("market_price"))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rank_models(candidates: list[dict[str, Any]], customer_unit_price: float, limit: int = 5) -> list[dict[str, Any]]:
    """Ранжирует модели по публичному ориентиру, который не является ценой закупки."""
    ranked = [dict(source) for source in candidates if supplier_search_eligible(source)]
    prices = sorted(_public_price(x) for x in ranked if _public_price(x) is not None)
    low_cut = prices[max(0, (len(prices) - 1) // 3)] if prices else None
    high_cut = prices[max(0, (2 * len(prices) - 1) // 3)] if prices else None
    for item in ranked:
        price = _public_price(item)
        item["public_price_reference"] = price
        item["public_price_source"] = item.get("public_price_source") or item.get("market_price_source")
        item["purchase_price"] = None
        if price is None:
            category = "невозможно определить"
        elif len(prices) == 1 or price <= low_cut:
            category = "бюджетная"
        elif price <= high_cut:
            category = "средняя"
        else:
            category = "дорогая"
        item["relative_price_category"] = category
        if price is not None and customer_unit_price:
            reserve = round(customer_unit_price - price, 2)
            item["indicative_reserve_rub"] = reserve
            item["indicative_reserve_percent"] = round(reserve / customer_unit_price * 100, 2)
            item["indicative_20_percent_threshold"] = price <= customer_unit_price * .8
            # Старые имена оставлены только для совместимости ранее сохранённых тестов.
            item["reserve_rub"] = item["indicative_reserve_rub"]
            item["reserve_percent"] = item["indicative_reserve_percent"]
            item["passes_20_percent_threshold"] = item["indicative_20_percent_threshold"]
        else:
            item["indicative_reserve_rub"] = None
            item["indicative_reserve_percent"] = None
            item["indicative_20_percent_threshold"] = None
    ranked.sort(key=lambda x: (_public_price(x) is None,
                               _public_price(x) if _public_price(x) is not None else float("inf"),
                               x.get("brand") or "", x.get("model") or ""))
    for priority, item in enumerate(ranked[:limit], 1):
        item["supplier_search_priority"] = priority
        item["request_current_supplier_price"] = True
    return ranked[:limit]


def procurement_business_status(candidates: list[dict[str, Any]], customer_unit_price: float,
                                search_coverage: dict[str, Any], tz_impossible: bool = False,
                                manual_review: bool = False) -> dict[str, Any]:
    top=rank_models(candidates, customer_unit_price)
    if tz_impossible:
        status="НЕ ТРАТИТЬ ВРЕМЯ — доказанная невыполнимость ТЗ"
    elif manual_review:
        status="НУЖНА РУЧНАЯ ПРОВЕРКА"
    elif top:
        status="ГОТОВО К ПОИСКУ ПОСТАВЩИКОВ — найдены полностью соответствующие модели"
    else:
        status="ПРОДОЛЖИТЬ ПОИСК МОДЕЛЕЙ — недостаточно полностью подтверждённых кандидатов"
    return {"business_status":status,"models_for_supplier_search":top,
            "search_coverage":search_coverage,
            "supplier_search_ready":bool(top) and not tz_impossible and not manual_review}
