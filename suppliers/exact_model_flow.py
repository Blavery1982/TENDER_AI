"""Две цены ≤ 82% НМЦК → TOP-3 → проверки выбранных поставщиков."""
from __future__ import annotations

import math

from calculator.business_decision import _full_quote_price
from suppliers.price_search_flow import confirmed_price_ranking, select_verified_top3


def price_signal(lot, positions, rankings=None):
    """Предварительный gate без комиссии, налогов и расходов."""
    rankings = rankings if rankings is not None else [
        confirmed_price_ranking(p.get("source_offers") or [], quantity=p.get("quantity")) for p in positions]
    try:
        nmck = float(lot.get("price"))
        valid = math.isfinite(nmck) and nmck > 0 and bool(positions)
    except (TypeError, ValueError):
        nmck, valid = None, False
    maximum = round(nmck * .82, 2) if valid else None
    details = []
    for position, ranked in zip(positions, rankings):
        try:
            quantity = float(position.get("quantity"))
            valid_quantity = math.isfinite(quantity) and quantity > 0
        except (TypeError, ValueError):
            quantity, valid_quantity = None, False
        if len(positions) == 1:
            budget = maximum
        else:
            try:
                budget = float(position.get("customer_unit_price")) * quantity * .82
                if not math.isfinite(budget) or budget <= 0:
                    budget = None
            except (TypeError, ValueError):
                budget = None
        qualifying = []
        for row in ranked:
            total = _full_quote_price(row, quantity) if valid_quantity else None
            if total is not None and budget is not None and total <= budget:
                qualifying.append(row)
        details.append({"position_number": position["position_number"],
                        "maximum_purchase_cost": budget,
                        "maximum_unit_price": budget / quantity if budget is not None and valid_quantity else None,
                        "qualifying_offers": qualifying, "qualifying_supplier_count": len(qualifying),
                        "second_quote_total": _full_quote_price(qualifying[1], quantity) if len(qualifying) >= 2 else None})
    passed = valid and bool(details) and all(d["qualifying_supplier_count"] >= 2 for d in details)
    # Позиционные бюджеты не заменяют проверку полной корзины.
    if passed:
        passed = sum(d["second_quote_total"] for d in details) <= maximum
    return {"status": "ECONOMIC_SIGNAL" if passed else "NO_ECONOMIC_SIGNAL", "passes": passed,
            "maximum_purchase_cost": maximum, "formula": "НМЦК × 0,82", "positions": details}


def _price_snapshot(lot, positions, rankings, commission, gate):
    """Справка по публичной корзине; финальную экономику здесь не считаем."""
    basket, missing = [], []
    for position, ranked in zip(positions, rankings):
        quantity = position.get("quantity")
        priced = [(total, row) for row in ranked
                  if (total := _full_quote_price(row, quantity)) is not None and total > 0]
        if not priced:
            missing.append(f"позиция {position['position_number']}: нет подтверждённой цены/количества")
            continue
        total, best = min(priced, key=lambda pair: pair[0])
        basket.append({"position_number": position["position_number"], "quantity": quantity,
                       "supplier_name": best.get("supplier_name"), "unit_price": best.get("public_price"),
                       "total_cost": total})
    cost = sum(row["total_cost"] for row in basket) if basket and not missing else None
    reserve = float(lot["price"]) - cost if cost is not None else None
    return {"status": "passes" if gate["passes"] else "insufficient_data" if missing or gate['status']=='NOT_EVALUATED' else "needs_lower_price",
            "passes": gate["passes"], "supplier_verification_required": gate["passes"],
            "maximum_purchase_cost": gate["maximum_purchase_cost"], "minimum_purchase_cost": cost,
            "preliminary_reserve_rub": reserve,
            "preliminary_reserve_percent": reserve / float(lot["price"]) * 100 if reserve is not None else None,
            "calculation_basis": "Ценовой сигнал без комиссии, налогов и дополнительных расходов",
            "commission": commission, "commission_source": "raw.lot.commissionFee",
            "basket": basket, "missing_data": missing}


def exact_supplier_flow(lot, positions, *, verifier, reserve_percent=18, on_economics=None,
                       require_top3=False, prices_ready=True):
    nmck = float(lot["price"])
    try:
        value = float(lot["commissionFee"]) if lot.get("commissionFee") is not None else None
        commission = value if value is not None and math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        commission = None
    rankings = [confirmed_price_ranking(p.get("source_offers") or [], quantity=p.get("quantity")) for p in positions]
    signal = price_signal(lot, positions, rankings)
    top3_complete = bool(rankings) and all(len(r)>=3 for r in rankings)
    if not prices_ready or require_top3 and not top3_complete:
        signal.update(status='NOT_EVALUATED', passes=False)
    checks_ready = prices_ready and signal['passes'] and (top3_complete or not require_top3)
    public = _price_snapshot(lot, positions, rankings, commission, signal)
    source = "ЕАТ: lot.commissionFee" if commission is not None else "ЕАТ: lot.commissionFee не получена"
    if on_economics is not None:
        on_economics({"public_economics_before_supplier_approval": public,
                      "price_gate": signal, "commission": commission, "commission_source": source,
                      "maximum_purchase_cost": signal["maximum_purchase_cost"]})
    selected_positions, history, candidates = [], [], []
    for position, ranked, gate in zip(positions, rankings, signal["positions"]):
        shortlist = ranked[:3] if checks_ready else []
        # Проверяются только уже сформированные TOP-3, без обхода других сайтов.
        top, checks = select_verified_top3(shortlist, verifier=verifier) if shortlist else ([], [])
        candidates.append({"position_number": position["position_number"],
                           "maximum_unit_price": gate["maximum_unit_price"],
                           "ranked_offers": shortlist, "public_ranked_offers": ranked,
                           "eligible_offers": gate["qualifying_offers"], "shortlist": shortlist,
                           "selected_offers": top})
        history.extend({**r, "position_number": position["position_number"]} for r in checks)
        selected_positions.append({**position, "offers": top})
    return {"price_gate": signal, "status": signal["status"], "top3_complete":top3_complete, "candidates": candidates,
            "antifraud_history": history, "positions": selected_positions,
            "economics": _price_snapshot(lot, positions, [p["offers"] for p in selected_positions], commission, signal),
            "public_economics_before_supplier_approval": public, "commission_source": source,
            "commission": commission, "maximum_purchase_cost": signal["maximum_purchase_cost"]}
