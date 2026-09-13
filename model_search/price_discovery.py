"""Публичный ценовой ориентир после полного технического допуска.

Это не закупочная цена: реальную цену позже определяет supplier_search.
"""

def discover_prices(candidates, target):
    for candidate in candidates:
        if candidate["technical_status"] != "fully_compliant":
            candidate["price_discovery_status"] = "not_started_technical_check_failed"
        elif (candidate.get("production_status") != "in_production"
              or candidate.get("russia_availability") not in {"available", "available_to_order"}):
            candidate["price_discovery_status"] = "not_started_market_eligibility_failed"
        elif candidate.get("market_price") is None or not candidate.get("market_price_source"):
            candidate["price_check_status"] = "public_price_not_confirmed"
            candidate["price_discovery_status"] = "completed"
        else:
            candidate["public_price_reference"] = candidate["market_price"]
            candidate["public_price_source"] = candidate["market_price_source"]
            candidate["purchase_price"] = None
            candidate["price_check_status"] = "public_price_reference_only"
            candidate["indicative_20_percent_threshold"] = (
                candidate["market_price"] <= target if target is not None else None
            )
            candidate["price_discovery_status"] = "completed"
    return candidates
