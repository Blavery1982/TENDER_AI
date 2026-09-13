"""Решение по реальным данным, отдельно от гипотетических сценариев."""
from __future__ import annotations


def calculator_decision(model_found: bool, suppliers_found: bool,
                        purchase_price: float | None, unknown_costs: list[str],
                        economics: dict | None = None,
                        minimum_profit_rub: float = 0) -> dict:
    if purchase_price is None and model_found and suppliers_found:
        return {"status":"ТРЕБУЕТСЯ ПОЛУЧИТЬ АКТУАЛЬНЫЕ ЦЕНЫ ПОСТАВЩИКОВ",
                "reason":"Модель и варианты поставщиков найдены, но подтверждённой закупочной цены нет."}
    if purchase_price is None:
        return {"status":"ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА",
                "reason":"Нет подтверждённой закупочной цены или достаточных исходных данных."}
    if unknown_costs:
        return {"status":"ТРЕБУЕТСЯ УТОЧНИТЬ РАСХОДЫ",
                "reason":"Неизвестны критичные расходы: " + ", ".join(unknown_costs)}
    if economics is None:
        return {"status":"ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА","reason":"Расчёт экономики отсутствует."}
    if economics["net_profit"] > minimum_profit_rub:
        return {"status":"ГОТОВО К УЧАСТИЮ","reason":"Подтверждённые данные показывают достаточную прибыль."}
    return {"status":"ЭКОНОМИКА НЕ ПРОХОДИТ","reason":"Подтверждённая чистая прибыль недостаточна."}

