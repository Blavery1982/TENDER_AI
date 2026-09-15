"""Legacy-сценарные формулы CALCULATOR.

Они сохранены для независимых контрольных расчётов. Production business
decision использует ``calculator.business_decision`` и фактическую комиссию
ЕАТ, поэтому ``platform_commission`` не является runtime fallback.
"""
from __future__ import annotations


def money(value: float) -> float:
    return round(float(value), 2)


def platform_commission(nmck: float, rate: float = 0.03) -> float:
    """Комиссия = НМЦК × 3%; она не меняется при снижении цены подачи."""
    return money(nmck * rate)


def tax_amount(submission_price: float, purchase_cost: float,
               operating_costs: float, rate: float = 0.15) -> tuple[float, float]:
    """Налоговая база = подача − закупка − логистика − допрасходы.

    Комиссия площадки в налоговую базу по принятой управленческой формуле не входит.
    """
    base = money(submission_price - purchase_cost - operating_costs)
    return base, money(max(base, 0) * rate)


def calculate_economics(nmck: float, submission_price: float, purchase_cost: float,
                        operating_costs: float, commission_rate: float = 0.03,
                        tax_rate: float = 0.15) -> dict:
    commission = platform_commission(nmck, commission_rate)
    tax_base, tax = tax_amount(submission_price, purchase_cost, operating_costs, tax_rate)
    margin = money(submission_price - purchase_cost)
    total = money(purchase_cost + operating_costs + commission + tax)
    profit = money(submission_price - total)
    return {
        "revenue": money(submission_price), "purchase_cost": money(purchase_cost),
        "commission": commission, "operating_costs": money(operating_costs),
        "tax_base": tax_base, "tax": tax, "total_costs": total,
        "net_profit": profit, "margin_rub": margin,
        "margin_percent": money(margin / submission_price * 100) if submission_price else None,
        # Управленческая рентабельность: чистая прибыль / все затраты × 100%.
        "profitability_percent": money(profit / total * 100) if total else None,
    }


def break_even_submission_price(nmck: float, purchase_cost: float, operating_costs: float,
                                commission_rate: float = 0.03, tax_rate: float = 0.15) -> float:
    """Цена подачи, при которой чистая прибыль равна нулю.

    При положительной налоговой базе:
    S = закупка + расходы + комиссия / (1 − ставка налога).
    """
    commission = platform_commission(nmck, commission_rate)
    return money(purchase_cost + operating_costs + commission / (1 - tax_rate))


def reserve_to_zero_percent(nmck: float, break_even_price: float) -> float | None:
    return money((nmck - break_even_price) / nmck * 100) if nmck else None


def minimum_submission_for_target_profit(nmck: float, purchase_cost: float,
                                         operating_costs: float,
                                         target_profit_rub: float | None = None,
                                         target_profit_percent: float | None = None,
                                         commission_rate: float = 0.03,
                                         tax_rate: float = 0.15) -> float | None:
    commission = platform_commission(nmck, commission_rate)
    base_cost = purchase_cost + operating_costs
    if target_profit_rub is not None:
        return money(base_cost + (target_profit_rub + commission) / (1 - tax_rate))
    if target_profit_percent is not None:
        ratio = target_profit_percent / 100
        denominator = 1 - tax_rate - ratio
        return money(((1 - tax_rate) * base_cost + commission) / denominator) if denominator > 0 else None
    return None
