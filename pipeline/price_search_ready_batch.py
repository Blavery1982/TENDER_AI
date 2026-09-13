"""Live supplier-price batch over saved PRICE_SEARCH_READY regression rows; no EAT I/O."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from filters.eat_filters import load_config
from model_search.live_price_search import search_exact_model_prices
from suppliers.price_search_flow import build_price_search_result
from suppliers.verification import verify_supplier

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/mvp_exact_reclassification.json"
OUTPUT = ROOT / "data/price_search_ready_batch.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _cards() -> dict[str, dict[str, Any]]:
    result = {}
    for path in (ROOT / "data").glob("eat_single_*.json"):
        card = _load(path)
        raw = card.get("raw") or {}
        trade = str(raw.get("tradeNumber") or (raw.get("lot") or {}).get("tradeNumber") or "")
        if trade:
            result[trade] = card
    return result


def _position(row: dict[str, Any], cards: dict[str, dict[str, Any]]) -> dict[str, Any]:
    card = cards.get(str(row["trade_number"])) or {}
    raw = card.get("raw") or card
    lot = raw.get("lot") or raw
    items = lot.get("lotItems") or []
    number = int(row["item_number"])
    item = items[number - 1] if 0 < number <= len(items) else {}
    quantity = item.get("quantity")
    unit_price = item.get("unitPrice")
    if unit_price is None and quantity not in (None, 0) and item.get("sum") is not None:
        unit_price = float(item["sum"]) / float(quantity)
    return {"procurement_id": row.get("purchase_id"), "trade_number": row.get("trade_number"),
            "item_number": number, "item_name": row.get("position_name") or row.get("item_name"),
            "quantity": quantity, "customer_unit_price": unit_price,
            "exact_model": row.get("found_identifier"), "model_source": row.get("model_source")}


def run(*, search: Callable[..., dict[str, Any]] = search_exact_model_prices,
        verifier: Callable[[dict[str, Any]], dict[str, Any]] = verify_supplier,
        output_path: Path = OUTPUT, use_saved_price_results: bool = False) -> dict[str, Any]:
    regression = _load(SOURCE)
    ready = [row for row in regression.get("items") or []
             if row.get("new_classification") == "PRICE_SEARCH_READY"]
    cards = _cards()
    rate = float((load_config().get("calculator") or {}).get("eat_commission_rate", .03))
    results = []
    for row in ready:
        position = _position(row, cards)
        try:
            price_path = ROOT / "data/price_search_ready" / (
                f"{position['trade_number']}_{position['item_number']}.json")
            price_result = (_load(price_path) if use_saved_price_results and price_path.exists()
                            else search(position["exact_model"], output_path=price_path))
            result = build_price_search_result(position, price_result,
                                               verifier=verifier, commission_rate=rate)
        except Exception as exc:
            result = {**position, "offer_1": None, "offer_2": None, "offer_3": None,
                      "best_safe_public_price": None, "preliminary_economics": None,
                      "status": "EXACT_MODEL_NOT_FOUND", "comments":
                      f"Ошибка позиции изолирована: {type(exc).__name__}: {exc}",
                      "candidate_urls_found": 0, "actual_prices_confirmed": 0,
                      "unique_suppliers_with_confirmed_price": 0,
                      "ranked_offers": [], "antifraud_history": [], "source_errors": []}
        results.append(result)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"source": str(SOURCE), "eat_requested": False,
                                           "model_discovery_performed": False,
                                           "positions": results}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return {"source": str(SOURCE), "eat_requested": False,
            "model_discovery_performed": False, "positions": results}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
