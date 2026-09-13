"""Cheap economic gate before supplier verification.

The gate uses product-page evidence only.  It never promotes an unverified
supplier into a final recommendation to bid.
"""
from __future__ import annotations

from typing import Any

from calculator.formulas import platform_commission

DEFAULT_RESERVE_PERCENT = 18.0
MAX_CALL_DISCOUNT_PERCENT = 20.0
UNAVAILABLE = {
    "out_of_stock", "sold_out", "unavailable", "sales_stopped", "discontinued",
    "archive", "archived",
    "нет в наличии", "продажи прекращены", "снят с продажи", "товар закончился",
    "снят с производства", "недоступен для заказа", "не продается", "не продаётся",
    "архив",
}
AVAILABLE = {"in_stock", "available", "to_order", "price_on_request",
             "в наличии", "под заказ", "доступно к заказу", "цена по запросу"}


def _availability(value: Any) -> str:
    return str(value or "unknown").strip().casefold()


def is_explicitly_unavailable(offer: dict[str, Any]) -> bool:
    value = _availability(offer.get("availability"))
    return value in UNAVAILABLE or any(marker in value for marker in UNAVAILABLE)


def is_confirmed_available(offer: dict[str, Any]) -> bool:
    return _availability(offer.get("availability")) in AVAILABLE


def _price_is_unconfirmed(offer: dict[str, Any]) -> bool:
    status = str(offer.get("price_status") or "").strip().casefold()
    return bool(
        offer.get("stale_price") is True
        or offer.get("stale_or_wrong") is True
        or offer.get("price_confirmed") is False
        or offer.get("price_verified") is False
        or status in {"stale", "outdated", "unconfirmed", "устарела", "не подтверждена"}
    )


def evaluate_price_request_candidate(
        offer: dict[str, Any], maximum_unit_price: float | None,
        max_discount_percent: float = MAX_CALL_DISCOUNT_PERCENT) -> dict[str, Any]:
    """Decide whether calling an exact-model seller is economically sensible."""
    result = dict(offer)
    result["maximum_acceptable_unit_price"] = maximum_unit_price
    result["required_discount_percent"] = None
    result["call_candidate"] = False
    result["call_candidate_reason"] = None
    if offer.get("exact_model") is not True:
        result["call_candidate_reason"] = "Не подтверждено точное совпадение модели"
        return result
    if is_explicitly_unavailable(offer):
        result["call_candidate_reason"] = "Товар однозначно недоступен для покупки"
        return result

    price = offer.get("public_price")
    unconfirmed = _price_is_unconfirmed(offer)
    may_be_available = (is_confirmed_available(offer)
                        or _availability(offer.get("availability")) == "unknown"
                        or offer.get("product_page_available") is True)
    if (price is None or unconfirmed) and may_be_available:
        result["call_candidate"] = True
        result["call_candidate_reason"] = (
            "Цена устарела или не подтверждена — запросить актуальную цену"
            if unconfirmed else "Цена не указана — запросить индивидуальную цену"
        )
        return result
    if price is None:
        result["call_candidate_reason"] = "Цена отсутствует и доступность товара не подтверждена"
        return result
    if maximum_unit_price is None or maximum_unit_price <= 0:
        result["call_candidate_reason"] = "Не определена максимально допустимая цена за единицу"
        return result
    try:
        price_value = float(price)
        required = max(0.0, (1 - float(maximum_unit_price) / price_value) * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        result["call_candidate_reason"] = "Опубликованная цена не распознана"
        return result
    # Четыре знака сохраняют корректную границу 20% в diagnostics: значение
    # немного выше 20% не должно визуально превращаться в 20,00%.
    result["required_discount_percent"] = round(required, 4)
    if required <= float(max_discount_percent) + 1e-9:
        result["call_candidate"] = True
        result["call_candidate_reason"] = (
            "Опубликованная цена уже проходит предварительный порог"
            if required == 0 else
            f"Для проходной цены нужна скидка {required:.2f}% (не более {max_discount_percent:g}%)"
        )
    else:
        result["call_candidate_reason"] = (
            f"Нужна скидка {required:.2f}% — больше допустимых {max_discount_percent:g}%"
        )
    return result


def price_request_candidates(
        offers: list[dict[str, Any]], maximum_unit_price: float | None = None,
        max_discount_percent: float = MAX_CALL_DISCOUNT_PERCENT) -> list[dict[str, Any]]:
    """Exact-model sellers worth a call after availability and discount checks."""
    assessed = [evaluate_price_request_candidate(row, maximum_unit_price,
                                                  max_discount_percent)
                for row in offers]
    rows = [row for row in assessed if row["call_candidate"]]
    rows.sort(key=lambda row: (row.get("public_price") is None,
                               row.get("required_discount_percent") or 0,
                               row.get("public_price") or float("inf"),
                               row.get("supplier_name") or ""))
    return rows


def economic_precheck(nmck: float | None, commission_rate: float,
                      positions: list[dict[str, Any]],
                      reserve_percent: float = DEFAULT_RESERVE_PERCENT) -> dict[str, Any]:
    """Compare the cheapest complete basket with the 18% preliminary gate."""
    if nmck is None:
        return {"status": "insufficient_data", "passes": False,
                "supplier_verification_required": False,
                "reason": "Не указана НМЦК"}
    try:
        nmck_value = float(nmck)
        margin = float(reserve_percent)
        commission = platform_commission(nmck_value, float(commission_rate))
    except (TypeError, ValueError):
        return {"status": "insufficient_data", "passes": False,
                "supplier_verification_required": False,
                "reason": "НМЦК или ставка комиссии не распознана"}
    available_revenue = round(nmck_value - commission, 2)
    multiplier = 1 - margin / 100
    maximum_purchase_cost = round(available_revenue * multiplier, 2)
    basket = []
    missing = []
    for number, position in enumerate(positions, 1):
        try:
            quantity = float(position.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0
        priced = [row for row in position.get("offers") or []
                  if is_confirmed_available(row) and row.get("public_price") is not None]
        priced.sort(key=lambda row: float(row["public_price"]))
        if quantity <= 0:
            missing.append(f"позиция {number}: не определено количество")
        elif not priced:
            missing.append(f"позиция {number}: нет подтверждённого доступного предложения с ценой")
        else:
            best = priced[0]
            basket.append({"position_number": position.get("position_number") or number,
                           "quantity": quantity, "supplier_name": best.get("supplier_name"),
                           "unit_price": float(best["public_price"]),
                           "total_cost": round(float(best["public_price"]) * quantity, 2)})
    minimum_purchase_cost = (round(sum(row["total_cost"] for row in basket), 2)
                             if not missing and basket else None)
    passes = bool(minimum_purchase_cost is not None
                  and minimum_purchase_cost <= maximum_purchase_cost)
    status = "passes" if passes else "insufficient_data" if missing else "needs_lower_price"
    reserve_rub = (round(available_revenue - minimum_purchase_cost, 2)
                   if minimum_purchase_cost is not None else None)
    reserve_actual_percent = (round(reserve_rub / available_revenue * 100, 2)
                              if reserve_rub is not None and available_revenue else None)
    return {
        "status": status, "passes": passes,
        "supplier_verification_required": passes,
        "reserve_percent_required": margin,
        "purchase_cost_multiplier": round(multiplier, 4),
        "commission_rate": float(commission_rate), "commission": commission,
        "available_revenue_after_commission": available_revenue,
        "maximum_purchase_cost": maximum_purchase_cost,
        "minimum_purchase_cost": minimum_purchase_cost,
        "preliminary_reserve_rub": reserve_rub,
        "preliminary_reserve_percent": reserve_actual_percent,
        "basket": basket, "missing_data": missing,
        "reason": "; ".join(missing) if missing else None,
    }


def _rub(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def current_analysis_recommendation(precheck: dict[str, Any],
                                    call_candidates: list[dict[str, Any]],
                                    positions: list[dict[str, Any]]) -> str:
    if precheck.get("status") == "insufficient_data":
        return f"⚪ НЕДОСТАТОЧНО ДАННЫХ: {precheck.get('reason') or 'невозможно выполнить предварительный расчёт'}."
    if precheck.get("passes"):
        return (f"🟡 ПЕРСПЕКТИВНО. Найдена закупочная стоимость "
                f"{_rub(precheck['minimum_purchase_cost'])}. Предварительный запас "
                f"{precheck['preliminary_reserve_percent']:.2f}%. Требуется проверка поставщика, "
                "расчёт логистики и остальных расходов.")
    names = list(dict.fromkeys(str(row.get("supplier_name") or "").strip()
                              for row in call_candidates if row.get("supplier_name")))
    maximum = precheck.get("maximum_purchase_cost")
    unit_maximum = None
    if len(positions) == 1:
        try:
            quantity = float(positions[0].get("quantity"))
            if quantity > 0:
                unit_maximum = maximum / quantity
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if names:
        limit = (f"не выше {_rub(unit_maximum)}/шт."
                 if unit_maximum is not None
                 else f"не выше {_rub(maximum)} за весь товар.")
        return ("📞 НУЖНА ЦЕНА. По опубликованным ценам экономика не проходит. "
                f"Нужно получить цену {limit} Запросить индивидуальную цену у: "
                f"{', '.join(names)}.")
    minimum = precheck.get("minimum_purchase_cost")
    return ("❌ НЕ ПОДАВАТЬСЯ. "
            + (f"Минимальная найденная закупочная стоимость — {_rub(minimum)}. " if minimum is not None else "")
            + "При текущих данных необходимой экономики нет.")
