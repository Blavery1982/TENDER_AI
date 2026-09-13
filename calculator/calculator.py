"""Сценарный расчёт и подготовка результата CALCULATOR."""
from __future__ import annotations

from calculator.formulas import (break_even_submission_price, calculate_economics,
                                 minimum_submission_for_target_profit,
                                 reserve_to_zero_percent)
from calculator.schemas import analyze_costs


def scenario(unit_purchase_price: float, quantity: float, nmck: float,
             costs: dict, reductions=(0, 5, 10, 15)) -> dict:
    cost_state = analyze_costs(costs)
    if cost_state["unknown_costs"]:
        raise ValueError("Сценарий требует явных значений всех расходов, включая нулевые")
    purchase_cost = round(unit_purchase_price * quantity, 2)
    operating = cost_state["additional_costs_total"]
    break_even = break_even_submission_price(nmck, purchase_cost, operating)
    calculations = []
    for reduction in reductions:
        submission = round(nmck * (1 - reduction / 100), 2)
        calculations.append({"reduction_percent": reduction,
                             "submission_price": submission,
                             **calculate_economics(nmck, submission, purchase_cost, operating)})
    return {
        "scenario_only": True,
        "scenario_notice":"Гипотетическая закупочная цена — только для проверки экономики",
        "hypothetical_unit_purchase_price": unit_purchase_price,
        "purchase_price": None,
        "purchase_cost": purchase_cost,
        "assumed_costs": costs,
        "break_even_submission_price": break_even,
        "reserve_to_zero_percent": reserve_to_zero_percent(nmck, break_even),
        "minimum_submission_for_target_profit_10_percent":
            minimum_submission_for_target_profit(nmck, purchase_cost, operating,
                                                 target_profit_percent=10),
        "calculations": calculations,
    }

