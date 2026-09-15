"""Адаптация сохранённого EXACT_MODEL просчёта без сетевых действий."""
from __future__ import annotations

from calculator.business_decision import (LABELS, decide_business, decision_text,
                                          calculate_bid_economics,
                                          calculate_bid_economics_from_offers,
                                          economic_snapshot, number, supplier_domain,
                                          _normalize)


def _url(row):
    return row.get("source_url") or row.get("product_url") or row.get("url")


def _matches(row, economics, quantity=None):
    basket = economics.get("basket") or []
    price = number(row.get("confirmed_price", row.get("public_price", row.get("price"))))
    if len(basket) == 1 and price is not None:
        best = basket[0]
        name = str(best.get("supplier_name") or "").removeprefix("www.").casefold()
        return price == number(best.get("unit_price")) and bool(name) and name in {
            supplier_domain(row), str(row.get("supplier_name") or row.get("seller") or "").removeprefix("www.").casefold()}
    # Сопоставление денежного входа со строкой, а не новый расчёт экономики.
    qty, purchase = number(quantity), number(economics.get("purchase_cost"))
    return bool(qty is not None and qty > 0 and price is not None and purchase is not None
                and abs(price * qty - purchase) < .005)


def _rows(ranked, verified, precheck, *, single_position, final_economics=None, quantity=None):
    checks = {_url(row): row for row in verified if _url(row)}
    rows = []
    for source in ranked:
        row = dict(source)
        checked = checks.get(_url(source))
        if checked:
            # Antifraud не вправе подменять цену, URL и свидетельства модели.
            for key in ("verification_status", "antifraud_status", "verification_skipped",
                        "risk_flags", "live_checks_missing", "unavailable_checks"):
                if key in checked:
                    row[key] = checked[key]
        if single_position:
            final = final_economics or {}
            if economic_snapshot({"economics": final})["final"] is not None:
                normalized = _normalize(row, "profitability_percent")
                # Общая экономика корзины относится ко всем трём валидным КП;
                # не прикрепляем её к отсутствующим/красным предложениям.
                if (normalized["confirmed"] and normalized["exact"]
                        and normalized["availability_normalized"] != "out_of_stock"
                        and normalized["antifraud_status"] != "red"):
                    row["final_economics"] = final
            if _matches(row, precheck or {}):
                row["preliminary_reserve_percent"] = precheck.get("preliminary_reserve_percent")
        rows.append(row)
    return rows


_EAT_COMMISSION_SOURCES = {"raw.lot.commissionFee", "procurement.commission_fee",
                           "ЕАТ: lot.commissionFee"}


def _final_uses_confirmed_commission(data, procurement):
    """Проверить, что сохранённая экономика привязана к комиссии ЕАТ."""
    canonical = procurement.get("commission_fee") if isinstance(procurement, dict) else None
    canonical = number(canonical)
    if canonical is None:
        return False
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    source = data.get("commission_source") or provenance.get("commission_source")
    verified = data.get("commission_verified") is True or provenance.get("commission_verified") is True
    if source not in _EAT_COMMISSION_SOURCES or not verified:
        return False
    supplied = data.get("eat_commission", data.get("commission_fee", data.get("commission")))
    supplied = number(supplied)
    return supplied is not None and abs(supplied - canonical) < 0.005


def _final_economics(result, flow):
    procurement = result.get("procurement") or {}
    for data in (result.get("final_economics"), result.get("economics"),
                 (flow or {}).get("final_economics"), (flow or {}).get("economics")):
        if not data or not _final_uses_confirmed_commission(data, procurement):
            continue
        required = ("average_purchase_price", "additional_expenses", "eat_commission", "nmck",
                    "bid_price", "total_expenses", "tax_base", "usn_tax", "net_profit",
                    "net_profit_percent")
        if not all(number(data.get(key)) is not None for key in required):
            continue
        procurement_nmck = number(procurement.get("nmck"))
        if procurement_nmck is not None and abs(number(data["nmck"]) - procurement_nmck) >= 0.01:
            continue
        expected = calculate_bid_economics(data["average_purchase_price"],
                                           data["additional_expenses"],
                                           procurement["commission_fee"], data["nmck"])
        if all(abs(number(data[key]) - expected[key]) < 0.01 for key in required):
            return data
    return {}


def _context(result, final):
    warnings = list(result.get("warnings") or [])
    audit = result.get("audit") or result.get("procurement_audit") or {}
    state = audit.get("additional_expense_state") or {}
    complete = result.get("calculation_complete", result.get("economics_complete"))
    included = result.get("mandatory_expenses_included")
    if final.get("mandatory_expenses_included") is False:
        included = False
    if state:
        amount = number(state.get("amount"))
        if state.get("ready") is not True or amount is None or amount < 0:
            included = False
            warnings.append(state.get("reason") or "Дополнительные расходы требуют расчёта")
        elif amount > 0:
            operating = number(final.get("operating_costs"))
            if operating is not None and operating < amount:
                included = False
                warnings.append("Известные обязательные расходы не полностью включены в итоговую экономику")
            elif included is False:
                warnings.append("Учёт обязательных дополнительных расходов в экономике ещё не завершён")
            elif included is not True and final.get("mandatory_expenses_included") is not True:
                included = operating is not None and operating >= amount
                if not included:
                    warnings.append("Включение обязательных дополнительных расходов в экономику не подтверждено")
        elif included is not False:
            included = True
    if audit.get("special_conditions") and included is not True and final.get("mandatory_expenses_included") is not True:
        included = False
        warnings.append("Влияние особых условий ещё не включено в окончательную экономику")
    unknown = result.get("unknown_costs") or (result.get("costs") or {}).get("unknown_costs") or final.get("unknown_costs")
    if unknown or (result.get("costs") or {}).get("all_costs_known") is False:
        complete = included = False
        warnings.append("Остались неоценённые обязательные расходы")
    return complete, included, list(dict.fromkeys(warnings))


def _derive_bid_economics(result, groups, precheck):
    """Собрать новую формулу из уже сохранённых входов, без сетевых действий."""
    if len(groups) != 1:
        return {}
    procurement = result.get("procurement") or {}
    audit = result.get("audit") or result.get("procurement_audit") or {}
    state = audit.get("additional_expense_state") or {}
    additional = result.get("additional_expenses")
    if additional is None and state.get("ready") is True:
        additional = state.get("amount")
    commission = procurement.get("commission_fee")
    nmck = result.get("nmck") or procurement.get("nmck")
    missing = [name for name, value in (("additional_expenses", additional),
                                        ("eat_commission", commission), ("nmck", nmck))
               if number(value) is None]
    if missing:
        return {"complete": False, "missing_inputs": missing}
    raw = (groups[0].get("selected_offers") if "selected_offers" in groups[0]
           else groups[0].get("ranked_offers")) or []
    selected_model = (result.get("model") or {}).get("selected_model")
    if not selected_model:
        selected_model = next((p.get("selected_model") for p in
                               (result.get("exact_supplier_flow") or {}).get("positions") or []
                               if p.get("position_number") == groups[0].get("position_number")), None)
    suitable = []
    for offer in raw:
        if selected_model and offer.get("model"):
            from model_search.product_evidence import same_exact_model
            if not same_exact_model(selected_model, offer["model"]):
                continue
        row = _normalize(offer, "profitability_percent")
        if row["confirmed"] and row["exact"] and row["availability_normalized"] != "out_of_stock" \
                and row["antifraud_status"] != "red":
            suitable.append(offer)
    quantity = (result.get("item") or {}).get("quantity")
    if quantity is None:
        quantity = next((p.get("quantity") for p in (result.get("exact_supplier_flow") or {}).get("positions") or []
                         if p.get("position_number") == groups[0].get("position_number")), None)
    economics = calculate_bid_economics_from_offers(
        suitable, additional, commission, nmck, quantity=quantity)
    economics["quantity"] = quantity
    economics["commission_source"] = "procurement.commission_fee"
    economics["commission_verified"] = True
    return economics


def attach_business_decision(result, *, config=None):
    """Добавить решение в итоговый JSON; исходные предложения не изменять."""
    flow = result.get("exact_supplier_flow")
    if flow:
        groups = flow.get("candidates") or []
        precheck = flow.get("public_economics_before_supplier_approval") or {}
        verified = flow.get("antifraud_history") or []
    else:
        search = result.get("supplier_search") or {}
        ranked = search.get("ranked_offers")
        if ranked is None:
            ranked = search.get("confirmed_offers") or []
        groups = [{"position_number": (result.get("item") or {}).get("position_number", 1),
                   "ranked_offers": ranked}]
        precheck = result.get("economic_precheck") or {}
        verified = search.get("all_verified_offers") or search.get("confirmed_offers") or []
    # В сквозной цепочке всегда считаем по текущим модели, количеству, TOP-3 и расходам.
    # Сохранённый числовой результат не может заменять изменившиеся входы.
    if flow:
        final = _derive_bid_economics(result, groups, precheck)
        result.pop("final_economics", None)
        if final:
            result["final_economics"] = final
        result["calculation_complete"] = final.get("complete") is True
    else:
        final = _final_economics(result, flow)
        current = _derive_bid_economics(result, groups, precheck)
        if current.get("complete") is True or not final:
            final = current
            if final:
                result["final_economics"] = final
                result["calculation_complete"] = final.get("complete") is True
    complete, included, warnings = _context(result, final)
    positions = {str(p["position_number"]): p for p in (flow or {}).get("positions") or []}
    decisions = []
    for group in groups:
        number_key = group["position_number"]
        quantity = (result.get("item") or {}).get("quantity") if len(groups) == 1 else positions.get(str(number_key), {}).get("quantity")
        rows = _rows(group.get("ranked_offers") or [], verified, precheck,
                     single_position=len(groups) == 1, final_economics=final, quantity=quantity)
        decision = decide_business(rows, config=config, manual_review_reasons=warnings,
                                   calculation_complete=complete, mandatory_expenses_included=included,
                                   final_economics=final if len(groups) == 1 else None)
        decisions.append({"position_number": number_key, **decision})
    if not decisions:
        decisions = [decide_business([], config=config, calculation_complete=complete,
                                     mandatory_expenses_included=included, manual_review_reasons=warnings,
                                     final_economics=final)]
    severity = {"bid": 0, "manual_review": 1, "do_not_bid": 2}
    overall = dict(max(decisions, key=lambda d: severity[d["status"]]))
    if len(decisions) > 1:
        snapshot = economic_snapshot({"economics": final})
        overall["position_decisions"] = decisions
        overall["recommended_supplier"] = None
        overall["eligible_supplier_count"] = min(d["eligible_supplier_count"] for d in decisions)
        overall["confirmed_supplier_count"] = min(d["confirmed_supplier_count"] for d in decisions)
        overall["count_scope"] = "per_position_minimum"
        overall["economics_complete"] = (all(d["economics_complete"] for d in decisions)
            and snapshot["complete"] and snapshot["expenses_included"] and complete is not False and included is not False)
        if snapshot["complete"] and snapshot["expenses_included"] and complete is not False and included is not False:
            overall["final_profitability"] = snapshot["final"]
            overall["profitability_basis"] = "profitability_percent"
            percent, threshold = snapshot["final"], overall["profitability_threshold"]
            overall["decision_reasons"] = list(overall["decision_reasons"])
            overall["decision_reasons"][1] = f"Итоговая рентабельность всей закупки {percent:g}% при пороге {threshold:g}%."
            if percent < threshold:
                overall["status"] = "do_not_bid"
                overall["rejection_basis"] = "economics"
                overall["decision_reasons"][1] += " Полная экономика не проходит минимум."
            elif overall["status"] == "do_not_bid" and all(
                    d["rejection_basis"] == "economics" for d in decisions if d["status"] == "do_not_bid"):
                # Позиционный бюджет не перебивает рассчитанную общую экономику.
                overall["status"] = "manual_review"
                overall["rejection_basis"] = None
                overall["decision_reasons"][1] += " Нужно подтвердить подходящие варианты по каждой позиции."
        if overall["status"] == "bid" and not overall["economics_complete"]:
            overall["status"] = "manual_review"
            overall["decision_reasons"] = list(overall["decision_reasons"])
            overall["decision_reasons"][1] += " Общая корзина закупки ещё не рассчитана полностью."
        overall["label"] = LABELS[overall["status"]]
        overall["decision_reasons"] = list(overall["decision_reasons"])
        overall["decision_reasons"][4] = "; ".join(
            f"Позиция {d['position_number']}: {d['label']}, подходящих поставщиков {d['eligible_supplier_count']}" for d in decisions)
    result["business_decision"] = overall
    result["current_analysis_result"] = decision_text(overall)
    return overall
