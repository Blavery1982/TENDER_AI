"""Детерминированное решение по сохранённым ценам и показателям просчёта."""
from __future__ import annotations

import math
from urllib.parse import urlsplit

from filters.eat_filters import load_config
from suppliers.economic_precheck import UNAVAILABLE
from suppliers.verification import HIGH_RISK, MANUAL, PASSED

LABELS = {"bid": "🟢 ПОДАВАТЬСЯ", "manual_review": "🟡 РУЧНАЯ ПРОВЕРКА",
          "do_not_bid": "🔴 НЕ ПОДАВАТЬСЯ"}

# Минимальная чистая прибыль от средней закупочной цены трёх КП.
NET_PROFIT_THRESHOLD_PERCENT = 12.0
SUBMISSION_MARKUP_PERCENT = 18.0
USN_RATE = 0.15


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _format(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def calculate_bid_economics(average_purchase_price, additional_expenses,
                            eat_commission, nmck, *, markup_percent=SUBMISSION_MARKUP_PERCENT,
                            usn_rate=USN_RATE,
                            minimum_net_profit_percent=NET_PROFIT_THRESHOLD_PERCENT):
    """Рассчитать цену подачи по трём уже выбранным КП.

    Функция не ищет поставщиков и не меняет их порядок. Она принимает только
    сохранённые структурированные входы бизнес-решения.
    """
    values = {"average_purchase_price": number(average_purchase_price),
              "additional_expenses": number(additional_expenses),
              "eat_commission": number(eat_commission), "nmck": number(nmck)}
    if any(value is None for value in values.values()):
        return {"complete": False, "missing_inputs": [key for key, value in values.items() if value is None]}
    if values["average_purchase_price"] <= 0 or values["additional_expenses"] < 0 or values["eat_commission"] < 0:
        raise ValueError("Средняя закупочная цена и расходы имеют недопустимые значения")
    average = values["average_purchase_price"]
    bid_price = average * (1 + markup_percent / 100)
    total_expenses = average + values["additional_expenses"] + values["eat_commission"]
    tax_base = max(bid_price - total_expenses, 0)
    usn_tax = tax_base * usn_rate
    net_profit = bid_price - total_expenses - usn_tax
    net_profit_percent = net_profit / average * 100
    return {"complete": True, **values,
            "markup_percent": float(markup_percent), "bid_price": bid_price,
            "total_expenses": total_expenses, "tax_base": tax_base,
            "usn_rate": float(usn_rate), "usn_tax": usn_tax,
            "net_profit": net_profit, "net_profit_percent": net_profit_percent,
            "operating_costs": values["additional_expenses"],
            "mandatory_expenses_included": True,
            "passes_nmck": bid_price <= values["nmck"],
            "passes_net_profit": net_profit_percent >= minimum_net_profit_percent,
            "minimum_net_profit_percent": float(minimum_net_profit_percent),
            "passes": bid_price <= values["nmck"] and net_profit_percent >= minimum_net_profit_percent}


def _full_quote_price(offer, quantity):
    """Вернуть полную стоимость КП без двойного умножения на количество."""
    for key in ("full_quote_price", "quote_total", "total_price", "purchase_total"):
        total = number(offer.get(key))
        if total is not None:
            return total
    price = number(offer.get("confirmed_price", offer.get("public_price", offer.get("price"))))
    if price is None:
        return None
    if offer.get("price_is_total") is True or offer.get("price_scope") in {"total", "full_quote"}:
        return price
    qty = number(quantity)
    return price * qty if qty is not None and qty > 0 else price


def calculate_bid_economics_from_offers(offers, additional_expenses, eat_commission,
                                        nmck, *, quantity=None,
                                        minimum_net_profit_percent=NET_PROFIT_THRESHOLD_PERCENT):
    """Посчитать A как среднее полных стоимостей трёх переданных КП."""
    prices = [_full_quote_price(offer, quantity) for offer in offers]
    prices = [price for price in prices if price is not None and price > 0]
    if len(prices) < 3:
        return {"complete": False, "missing_inputs": ["three_confirmed_supplier_prices"]}
    average = sum(prices[:3]) / 3
    return calculate_bid_economics(average, additional_expenses, eat_commission, nmck,
                                   minimum_net_profit_percent=minimum_net_profit_percent)


def supplier_domain(offer):
    from suppliers.price_search_flow import _supplier_key
    url = offer.get("source_url") or offer.get("product_url") or offer.get("url") or ""
    domain = offer.get("domain") or urlsplit(url).hostname or ""
    domain = str(domain).strip().casefold().rstrip(".").removeprefix("www.")
    if any(domain == host or domain.endswith("." + host) for host in
           ("market.yandex.ru", "ozon.ru", "wildberries.ru")):
        return _supplier_key(offer)
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def _antifraud(offer):
    value = offer.get("antifraud_status", offer.get("verification_status"))
    if offer.get("verification_status") == HIGH_RISK or str(offer.get("verification_status")).startswith("🔴"):
        return "red"
    if value == HIGH_RISK or str(value).startswith("🔴") or value == "red":
        return "red"
    if offer.get("verification_skipped") is True:
        return "unknown"
    if offer.get("verification_status") == MANUAL or str(offer.get("verification_status")).startswith("🟡"):
        return "yellow"
    if value == PASSED or str(value).startswith(("🟢", "✅")) or value == "green":
        return "green"
    if value == MANUAL or str(value).startswith("🟡") or value == "yellow":
        return "yellow"
    return "unknown"


def economic_snapshot(offer, default_basis="profitability_percent"):
    """Разделить итоговую рентабельность и промежуточный запас без пересчёта."""
    economics = offer.get("final_economics") or offer.get("economics") or {}
    basis = offer.get("profitability_basis", default_basis)
    final = number(economics.get("net_profit_percent"))
    if final is None:
        final = number(economics.get("profitability_percent"))
    if final is None:
        final = number(offer.get("profitability_percent"))
    if final is None and basis == "profitability_percent":
        final = number(offer.get("profitability"))
    preliminary = number(offer.get("preliminary_reserve_percent", economics.get("preliminary_reserve_percent")))
    if preliminary is None and basis == "preliminary_reserve_percent":
        preliminary = number(offer.get("profitability"))
    # Снимок calculate_economics содержит денежные входы комиссии и расходов.
    bid_inputs_known = all(number(economics.get(key)) is not None for key in
                           ("average_purchase_price", "additional_expenses", "eat_commission", "nmck",
                            "bid_price", "total_expenses", "tax_base", "usn_tax", "net_profit"))
    inputs_known = bid_inputs_known or all(number(economics.get(key)) is not None for key in
                       ("purchase_cost", "commission", "operating_costs", "total_costs", "net_profit"))
    complete = offer.get("economics_complete", economics.get("economics_complete", economics.get("complete", inputs_known))) is True
    included = offer.get("mandatory_expenses_included",
                         economics.get("mandatory_expenses_included", inputs_known)) is True
    if economics.get("unknown_costs") or economics.get("missing_data") or economics.get("all_costs_known") is False:
        complete = included = False
    bid_price = number(economics.get("bid_price"))
    nmck = number(economics.get("nmck"))
    # Для новой формулы проверка НМЦК обязательна: одна чистая прибыль без
    # рассчитанной цены подачи не является завершённым бизнес-просчётом.
    if number(economics.get("net_profit_percent")) is not None and (bid_price is None or nmck is None):
        complete = False
    passes_nmck = economics.get("passes_nmck")
    if passes_nmck is None and bid_price is not None and nmck is not None:
        passes_nmck = bid_price <= nmck
    passes_net_profit = economics.get("passes_net_profit")
    if passes_net_profit is None and final is not None:
        passes_net_profit = final >= NET_PROFIT_THRESHOLD_PERCENT
    return {"final": final, "preliminary": preliminary,
            "complete": complete and final is not None, "expenses_included": included,
            "bid_price": bid_price, "nmck": nmck,
            "passes_nmck": passes_nmck,
            "passes_net_profit": passes_net_profit}


def _normalize(offer, basis):
    availability = str(offer.get("availability_normalized") or offer.get("availability") or "unknown").casefold().strip()
    if availability in UNAVAILABLE:
        availability = "out_of_stock"
    elif availability in {"to_order", "order", "под заказ"}:
        availability = "order"
    elif availability in {"in_stock", "в наличии", "available"}:
        availability = "in_stock"
    else:
        availability = "unknown"
    price = number(offer.get("confirmed_price", offer.get("public_price", offer.get("price"))))
    confirmation = offer.get("price_confirmed", offer.get("price_confirmed_on_product_page"))
    confirmed = price is not None and price > 0 and confirmation is True
    if (offer.get("price_confirmed_on_product_page") is False or offer.get("price_verified") is False
            or offer.get("stale_price") is True or offer.get("stale_or_wrong") is True):
        confirmed = False
    economics = economic_snapshot(offer, basis)
    exact = offer.get("exact_model_confirmed", offer.get("exact_model_match", offer.get("exact_model")))
    return {"domain": supplier_domain(offer), "supplier": offer.get("supplier_name") or offer.get("seller"),
            "source_url": offer.get("source_url") or offer.get("product_url") or offer.get("url"),
            "confirmed_price": price, "profitability": economics["final"],
            "preliminary_reserve_percent": economics["preliminary"],
            "economics_complete": economics["complete"], "expenses_included": economics["expenses_included"],
            "availability_normalized": availability, "antifraud_status": _antifraud(offer),
            "exact": exact is True, "exact_known": isinstance(exact, bool),
            "confirmed": confirmed, "critical_supplier": offer.get("critical_supplier") is True,
            "warnings": list(offer.get("live_checks_missing") or []) + list(offer.get("unavailable_checks") or [])
                        + list(offer.get("risk_flags") or [])}


def decide_business(ranked_offers, *, config=None, manual_review_reasons=(),
                    profitability_basis="profitability_percent", calculation_complete=None,
                    mandatory_expenses_included=None, final_economics=None,
                    average_purchase_price=None, additional_expenses=None,
                    eat_commission=None, nmck=None):
    """Сохранить порядок входа; не выполнять поиск, проверку или расчёт экономики."""
    # Порог 12% относится именно к чистой прибыли по новой формуле подачи.
    threshold = NET_PROFIT_THRESHOLD_PERCENT
    if threshold is None or threshold < 0:
        raise ValueError("Некорректный production-порог")
    rows = [_normalize(offer, profitability_basis) for offer in ranked_offers]
    confirmed = [row for row in rows if row["confirmed"]]
    possible = [row for row in rows if (row["exact"] or not row["exact_known"])
                and row["availability_normalized"] != "out_of_stock" and row["antifraud_status"] != "red"]
    viable = [row for row in possible if row["confirmed"] and row["exact"]]
    warnings = list(dict.fromkeys(str(x) for x in manual_review_reasons if x))
    # Финальная экономика сделки общая для выбранных КП. Она определяет
    # финансовый проход, но не должна уменьшать число валидных поставщиков
    # только из-за отсутствия индивидуального поля profitability.
    if final_economics is None and all(value is not None for value in
                                       (average_purchase_price, additional_expenses, eat_commission, nmck)):
        final_economics = calculate_bid_economics(average_purchase_price, additional_expenses,
                                                  eat_commission, nmck)
    final_snapshot = economic_snapshot({"economics": final_economics or {}})
    aggregate_ready = (final_snapshot["complete"] and final_snapshot["final"] is not None
                       and final_snapshot["passes_nmck"] is not None)
    eligible = (viable if aggregate_ready else
                [row for row in viable if row["profitability"] is not None and row["profitability"] >= threshold])
    # Неизвестный домен не создаёт фиктивный резерв независимых магазинов.
    count = len({row["domain"] for row in eligible if row["domain"]})
    best = eligible[0] if eligible else None
    # Первый вариант каждого домена сохраняет порядок полученного ранжирования.
    critical, seen = [], set()
    for row in eligible:
        if row["critical_supplier"] or (row["domain"] not in seen and len(seen) < 3):
            critical.append(row)
        seen.add(row["domain"])
    critical.extend(row for row in possible if row["critical_supplier"] and row not in critical)
    for row in critical:
        warnings.extend(str(flag) for flag in row["warnings"] if flag)
    warnings = list(dict.fromkeys(warnings))
    final_percent = final_snapshot["final"]
    row_complete = bool(possible) and all(row["confirmed"] and row["exact"]
        and row["economics_complete"] and row["expenses_included"] for row in possible)
    complete = row_complete if calculation_complete is None else calculation_complete is True
    # Явно переданная неполная итоговая экономика блокирует зелёный статус,
    # даже если у отдельных КП остались старые положительные показатели.
    if final_economics and not final_snapshot["complete"]:
        complete = False
    if final_percent is not None:
        complete = complete and final_snapshot["complete"] and final_snapshot["expenses_included"]
    if mandatory_expenses_included is False:
        complete = False
    # Явная незавершённость любого возможного варианта не исчезает от общего флага.
    aggregate_formula_complete = (final_snapshot["complete"]
                                  and final_snapshot["bid_price"] is not None
                                  and final_snapshot["nmck"] is not None
                                  and calculation_complete is True)
    if possible and not row_complete and not aggregate_formula_complete:
        complete = False
    global_complete = (final_percent is not None and final_snapshot["complete"]
                       and final_snapshot["expenses_included"] and calculation_complete is not False
                       and mandatory_expenses_included is not False)
    price_rejected = global_complete and final_snapshot["passes_nmck"] is False
    economics_rejected = ((global_complete and final_percent < threshold)
                          or price_rejected
                          or (final_percent is None and complete and bool(possible) and not eligible))
    terminal_rejection = bool(rows) and not possible and all(
        row["availability_normalized"] == "out_of_stock" or row["antifraud_status"] == "red"
        or row["economics_complete"] for row in rows)
    no_variants = not rows and calculation_complete is True
    if terminal_rejection or no_variants or economics_rejected:
        status = "do_not_bid"
    elif (complete and best and count >= 3 and not warnings
          and all(row["availability_normalized"] == "in_stock" and row["antifraud_status"] == "green"
                  for row in critical)):
        status = "bid"
    else:
        status = "manual_review"

    display = best or (viable[0] if viable else confirmed[0] if confirmed else None)
    reasons = [f"Лучшая {'подходящая' if best else 'найденная'} цена {_format(display['confirmed_price'])} ₽."
               if display else "Завершённый просчёт не нашёл подтверждённых предложений." if no_variants
               else "Подтверждённой актуальной цены нет; оценить закупку пока нельзя."]
    percent = final_percent if final_percent is not None else display["profitability"] if display else None
    if percent is not None:
        economic_reason = f"Итоговая чистая прибыль {_format(percent)}% от средней закупки при минимуме {_format(threshold)}%"
        economic_reason += (f" — цена подачи {_format(final_snapshot['bid_price'])} ₽ выше НМЦК {_format(final_snapshot['nmck'])} ₽." if price_rejected
                            else " — полный расчёт не проходит минимум." if economics_rejected
                            else " не отменяет отсутствие подходящих вариантов." if terminal_rejection
                            else " — экономика проходит минимум." if complete else "; окончательный просчёт ещё не завершён.")
    else:
        economic_reason = ("Итоговая рентабельность не определяет отказ: подходящих вариантов поставки нет."
                           if terminal_rejection or no_variants else
                           "Итоговая рентабельность не рассчитана; требуется завершить экономику.")
        if display and display["preliminary_reserve_percent"] is not None:
            economic_reason += f" Предварительный запас {_format(display['preliminary_reserve_percent'])}% не определяет итог."
    if mandatory_expenses_included is False:
        economic_reason += " Обязательные дополнительные расходы ещё не полностью учтены."
    if warnings:
        economic_reason += " Требуется уточнение: " + "; ".join(warnings) + "."
    reasons.append(economic_reason)
    if rows and all(row["availability_normalized"] == "out_of_stock" for row in rows):
        reasons.append("Все найденные варианты отсутствуют в наличии; выполнить поставку нельзя.")
    elif any(row["availability_normalized"] != "in_stock" for row in critical) or (display and display["availability_normalized"] != "in_stock"):
        reasons.append("Наличие: под заказ или не подтверждено у важного поставщика; нужно подтвердить возможность поставки.")
    else:
        reasons.append("Наличие подтверждено у важных поставщиков." if critical
                       else "Наличие: в наличии у найденного поставщика." if display
                       else "Возможность поставки по подходящему варианту не подтверждена.")
    if rows and all(row["antifraud_status"] == "red" for row in rows):
        reasons.append("Все найденные поставщики получили красный статус проверки; использовать их нельзя.")
    elif any(row["antifraud_status"] != "green" or row["warnings"] for row in critical) or (display and (display["antifraud_status"] != "green" or display["warnings"])):
        reasons.append("Проверка важного поставщика: жёлтый статус или не завершена; требуется ручная проверка.")
    else:
        reasons.append("Обязательных предупреждений проверки важных поставщиков нет." if critical
                       else "Проверка найденного поставщика: зелёный статус." if display
                       else "Безопасный подходящий поставщик пока не подтверждён.")
    count_reason = f"Подходящих уникальных поставщиков: {count}"
    count_reason += (" — резерв не исправляет непроходную итоговую экономику." if count >= 3 and economics_rejected
                     else " — резерв достаточен." if count >= 3 else "; для устойчивого участия нужно минимум 3.")
    if terminal_rejection or no_variants:
        count_reason += " Подходящих вариантов нет: " + ("точная модель не подтверждена или поставка невозможна." if rows else "полный просчёт завершён без вариантов.")
    reasons.append(count_reason)
    effective_basis = "profitability_percent" if percent is not None else "preliminary_reserve_percent" if any(
        row["preliminary_reserve_percent"] is not None for row in rows) else "profitability_percent"
    return {"status": status, "label": LABELS[status], "decision_version": 2,
            "profitability_threshold": threshold, "profitability_basis": effective_basis,
            "final_profitability": percent,
            "eligible_supplier_count": count,
            "confirmed_supplier_count": len({row['domain'] for row in confirmed if row['domain']}),
            "economics_complete": complete, "rule_candidates": [],
            "rejection_basis": ("economics" if economics_rejected else "no_variants" if no_variants
                                else "supply_or_model" if terminal_rejection else None),
            "recommended_supplier": ({key: best[key] for key in ("domain", "supplier", "source_url", "confirmed_price",
                "profitability", "preliminary_reserve_percent", "availability_normalized", "antifraud_status")} if best else None),
            "manual_review_reasons": warnings, "decision_reasons": reasons}


def decision_text(decision):
    return decision["label"] + "\n" + "\n".join(f"• {reason}" for reason in decision["decision_reasons"])


def decision_markdown(decision):
    return "# Бизнес-решение\n\n" + decision["label"] + "\n\n" + "\n".join(
        f"- {reason}" for reason in decision["decision_reasons"]) + "\n\n"
