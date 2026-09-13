"""Нормализация известных и неизвестных расходов."""
from __future__ import annotations

COST_FIELDS = (
    "delivery_cost", "logistics_cost", "unloading_cost", "assembly_cost",
    "installation_cost", "packaging_removal_cost", "other_costs",
)


def analyze_costs(costs: dict) -> dict:
    known, unknown = {}, []
    for field in COST_FIELDS:
        value = costs.get(field)
        if value is None:
            unknown.append(field)
        else:
            known[field] = round(float(value), 2)
    return {"known_costs": known, "unknown_costs": unknown,
            "additional_costs_total": round(sum(known.values()), 2),
            "all_costs_known": not unknown}

