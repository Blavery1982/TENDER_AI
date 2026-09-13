"""Price-first TOP-3 flow for an already confirmed exact product identifier."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from calculator.formulas import platform_commission
from suppliers.economic_precheck import is_explicitly_unavailable
from suppliers.verification import HIGH_RISK, INSUFFICIENT, MANUAL, verify_supplier


def _supplier_key(offer: dict[str, Any]) -> str:
    host = (urlsplit(str(offer.get("url") or offer.get("product_url") or "")).hostname or "")
    host = host.lower().removeprefix("www.")
    seller = str(offer.get("seller") or offer.get("supplier_name") or "").strip().casefold()
    # Marketplace cards can represent different independent merchants on the
    # same host; their explicitly extracted merchant name is the identity.
    if host in {"market.yandex.ru", "ozon.ru", "wildberries.ru"} and seller:
        return f"{host}:{seller}"
    return host or seller


def confirmed_price_ranking(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep current product-page prices only, one cheapest card per supplier."""
    by_supplier: dict[str, dict[str, Any]] = {}
    for source in offers:
        if (source.get("exact_model_match") is not True or source.get("price") is None
                or not (source.get("url") or source.get("product_url"))
                or is_explicitly_unavailable(source)):
            continue
        row = {**source, "supplier_name": source.get("seller") or _supplier_key(source),
               "product_url": source.get("url") or source.get("product_url"),
               "public_price": float(source["price"]), "purchase_price": None,
               "exact_model": True, "price_confirmed_on_product_page": True}
        key = _supplier_key(row)
        current = by_supplier.get(key)
        if current is None or row["public_price"] < current["public_price"]:
            by_supplier[key] = row
    return sorted(by_supplier.values(), key=lambda row: (row["public_price"], _supplier_key(row)))


def select_verified_top3(ranked: list[dict[str, Any]], *,
                         verifier: Callable[[dict[str, Any]], dict[str, Any]] = verify_supplier,
                         limit: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify cheapest candidates in order and stop after three non-red suppliers."""
    top: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for row in ranked:
        try:
            checked = verifier(row)
        except Exception as exc:
            checked = {**row, "verification_status": INSUFFICIENT,
                       "verification_comment": f"Проверка частично недоступна: {type(exc).__name__}",
                       "risk_flags": [], "purchase_price": None}
        history.append(checked)
        if checked.get("verification_status") == HIGH_RISK:
            continue
        top.append(checked)
        if len(top) >= limit:
            break
    return top, history


def preliminary_public_economics(*, customer_unit_price: Any, quantity: Any,
                                 best_offer: dict[str, Any] | None,
                                 commission_rate: float) -> dict[str, Any]:
    """Informational economics; a public price never becomes purchase_price."""
    try:
        unit = float(customer_unit_price)
        qty = float(quantity)
    except (TypeError, ValueError):
        return {"status": "Недостаточно данных", "calculation_basis":
                "ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ ПО ПУБЛИЧНОЙ ЦЕНЕ",
                "public_unit_price": None, "purchase_price": None}
    customer_total = round(unit * qty, 2)
    commission = platform_commission(customer_total, commission_rate)
    public_price = best_offer.get("public_price") if best_offer else None
    public_total = round(float(public_price) * qty, 2) if public_price is not None else None
    reserve = round(customer_total - commission - public_total, 2) if public_total is not None else None
    return {"status": "Предварительно рассчитано" if reserve is not None else "Цена не подтверждена",
            "calculation_basis": "ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ ПО ПУБЛИЧНОЙ ЦЕНЕ",
            "customer_total": customer_total, "quantity": qty,
            "eat_commission_rate": commission_rate, "eat_commission": commission,
            "public_unit_price": public_price, "public_total_for_quantity": public_total,
            "preliminary_reserve_before_other_costs": reserve, "purchase_price": None}


def build_price_search_result(position: dict[str, Any], price_search: dict[str, Any], *,
                              verifier: Callable[[dict[str, Any]], dict[str, Any]] = verify_supplier,
                              commission_rate: float = .03) -> dict[str, Any]:
    source_offers = price_search.get("offers") or []
    confirmed_pages = [row for row in source_offers
                       if row.get("exact_model_match") is True and row.get("price") is not None
                       and (row.get("url") or row.get("product_url"))
                       and not is_explicitly_unavailable(row)]
    ranked = confirmed_price_ranking(source_offers)
    top, history = select_verified_top3(ranked, verifier=verifier)
    offers = []
    for row in top:
        offers.append({"supplier": row.get("supplier_name"), "price": row.get("public_price"),
                       "url": row.get("product_url"), "availability": row.get("availability"),
                       "antifraud_status": row.get("verification_status")})
    while len(offers) < 3:
        offers.append(None)
    if not price_search.get("offers"):
        status = "EXACT_MODEL_NOT_FOUND"
    elif not ranked:
        status = "PRICE_NOT_CONFIRMED"
    elif not top:
        status = "NO_VALID_SUPPLIER"
    elif len(top) == 1:
        status = "ONLY_ONE_VALID_OFFER"
    elif any(row.get("verification_status") in {MANUAL, INSUFFICIENT} for row in top):
        status = "PRICE_FOUND_MANUAL_SUPPLIER_CHECK"
    else:
        status = "GOOD_PRICE_VERIFIED_SUPPLIER"
    economics = preliminary_public_economics(
        customer_unit_price=position.get("customer_unit_price"), quantity=position.get("quantity"),
        best_offer=top[0] if top else None, commission_rate=commission_rate)
    return {**position, "offer_1": offers[0], "offer_2": offers[1], "offer_3": offers[2],
            "best_safe_public_price": top[0].get("public_price") if top else None,
            "preliminary_economics": economics, "status": status,
            "comments": ("НАЙДЕНО МЕНЕЕ 3 пригодных предложений" if len(top) < 3 else ""),
            "candidate_urls_found": (price_search.get("candidate_discovery") or {}).get("candidate_count", 0),
            "actual_prices_confirmed": len(confirmed_pages),
            "unique_suppliers_with_confirmed_price": len(ranked), "ranked_offers": ranked,
            "antifraud_history": history, "source_errors": price_search.get("source_errors") or []}
