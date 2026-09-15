"""Контроль одной закупки с результатом каждого этапа в Google Sheets."""
from __future__ import annotations

import time
import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from calculator.result_decision import attach_business_decision
from calculator.business_decision import decision_markdown
from documents.pipeline import audit_from_extraction, process_procurement_documents
from documents.tender_archive import tender_folder, write_json
from filters.eat_filters import load_config
from filters.semantic_bad_words import filter_purchase_v2
from google_sheets.test_journal import GoogleTestJournal
from suppliers.price_search_flow import confirmed_price_ranking
from suppliers.verification import verify_supplier
from model_search.exact_model import EXACT_MODEL, resolve_exact_model
from documents.item_sources import resolve_item_sources
from model_search.live_discovery import select_cheapest_compliant_model
from model_search.customer_model import commercial_name
from model_search.purchase_category import CATEGORY_NO_MODEL, classify_purchase_category
from eat.browser_auth import BrowserAuthError
from eat.single_purchase import document_for_route
from filters.position_kind import goods_position, blocked_position
from suppliers.exact_model_flow import exact_supplier_flow

ROOT = Path(__file__).resolve().parent.parent
STAGES = ["Получение карточки ЕАТ", "Бизнес-фильтры", "Документы и архив",
          "Чтение документов / OCR", "Марки и бренды", "Требования ТЗ и условия контракта",
          "Проверка заказчика", "Определение модели", "Подбор модели по ТЗ",
          "Поиск публичных цен", "Экономический предаудит", "Проверка поставщиков",
          "Расходы и итоговый просчёт"]


def now():
    return datetime.now(timezone.utc).isoformat()


def display(value):
    return "не подтверждено" if value is None else str(value)


class TestRun:
    __test__ = False

    def __init__(self, purchase_id, journal, output_dir=None):
        UUID(purchase_id)
        self.journal = journal
        self.data = {"run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
                     "purchase_id": purchase_id, "mode": "LIVE — одна закупка",
                     "card_url": f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info",
                     "started_at": now(), "events": [], "positions": []}
        self.path = (output_dir or ROOT / "data/test_runs") / (self.data["run_id"] + ".json")
        self._pending_events = []

    def event(self, stage, status, result="", limitation="", *, position=None,
              source=None, seconds=0, mode=None):
        event = {"checked_at": now(), "stage": stage, "status": status,
                 "result": result, "limitation": limitation, "position": position,
                 "source": source, "seconds": round(seconds, 2), "mode": mode}
        self.data["events"].append(event)
        self._pending_events.append(event)
        write_json(self.path, self.data)
        print(f"{stage}: {status}. {result} {limitation}", flush=True)

    def finish(self):
        if self.data.get("exact_supplier_flow"):
            decision = self.data.get("business_decision") or attach_business_decision(self.data)
            self.path.with_suffix(".md").write_text(decision_markdown(decision), encoding="utf-8")
            from reports.price_search_report import write_price_search_markdown
            for position in self.data.get("positions") or []:
                prices = position.get("prices")
                if isinstance(prices, dict):
                    prices["business_decision"] = decision
                    prices["selected_model"] = position.get("selected_model")
                    prices["price_gate"] = self.data.get("price_gate")
                    path = price_path(self, position["position_number"])
                    write_json(path, prices)
                    write_price_search_markdown(path)
        self.data["finished_at"] = now()
        # Локальный результат готов до экспорта. Ошибка Sheets не повторяет вычисления.
        write_json(self.path, self.data)
        try:
            if self._pending_events:
                if hasattr(self.journal, "write_many"):
                    self.journal.write_many(self.data)
                else:
                    for event in self._pending_events:
                        self.journal.write(self.data, event)
                self._pending_events = []
            self.data["google_sheets"] = self.journal.verify(self.data)
        except Exception as exc:
            self.data["google_sheets"] = {"status": "EXPORT_ERROR", "error": type(exc).__name__,
                                         "local_result": str(self.path)}
        write_json(self.path, self.data)
        return self.data


def run_check(purchase_id, *, journal, card_loader, price_search=None,
              model_discovery=None, supplier_verifier=verify_supplier, output_dir=None,
              mode="LIVE — одна закупка", exact_price_search=None, exact_supplier_verifier=None,
              document_loader=None):
    """Использовать доменные функции; отсутствие входа сохранять явно."""
    run = TestRun(purchase_id, journal, output_dir)
    run.data["mode"] = mode
    run.document_loader = document_loader
    start = time.monotonic()
    try:
        card = card_loader(purchase_id)
        raw = card.get("raw") or {}
        lot = raw.get("lot") or raw
        if not raw.get("tradeNumber") or not lot.get("lotItems"):
            raise ValueError("Карточка не содержит номер ЕАТ или товарные позиции")
    except BrowserAuthError:
        raise
    except Exception as exc:
        # Не сохраняем текст произвольной сетевой ошибки: в нём могут быть secrets.
        reason = ("ЕАТ не вернул номер закупки или позиции; новый архив по UUID не создаётся"
                  if isinstance(exc, ValueError) else
                  f"Получение карточки не завершено ({type(exc).__name__})")
        run.event(STAGES[0], "ОШИБКА", limitation=reason, seconds=time.monotonic() - start)
        for stage in STAGES[1:]:
            run.event(stage, "НЕ ЗАПУЩЕН", limitation="Нет достоверной карточки закупки")
        run.data["status"] = "CARD_NOT_RECEIVED"
        run.event("Итог контрольного теста", "ОСТАНОВЛЕН", "Остановка на получении карточки ЕАТ",
                  "Цены, поставщики и прибыль не подтверждены")
        return run.finish()

    try:
        return _run_loaded(run, card, price_search, model_discovery, supplier_verifier, start,
                           exact_price_search, exact_supplier_verifier)
    except BrowserAuthError:
        raise
    except Exception as exc:
        run.event("Сбой контрольного теста", "ОШИБКА",
                  limitation=f"Этап не завершён ({type(exc).__name__}); результат не подменён успехом")
        finished = {e["stage"] for e in run.data["events"]}
        for stage in STAGES:
            if stage not in finished:
                run.event(stage, "НЕ ЗАПУЩЕН", limitation="Предыдущий этап не завершён")
        run.data["status"] = "RUN_ERROR"
        run.event("Итог контрольного теста", "ОСТАНОВЛЕН", limitation="Контрольный проход завершился с ошибкой")
        return run.finish()


def _run_loaded(run, card, price_search, model_discovery, supplier_verifier, start,
                exact_price_search=None, exact_supplier_verifier=None):
    purchase_id = run.data["purchase_id"]
    raw = card["raw"]
    lot = raw.get("lot") or raw
    run.data["tender_number"] = raw["tradeNumber"]
    run.data["procurement"] = {
        "id": purchase_id, "trade_number": raw.get("tradeNumber"),
        "url": run.data.get("card_url"), "subject": lot.get("subject") or raw.get("subject"),
        "nmck": lot.get("price"), "commission_fee": lot.get("commissionFee"),
        "commission_source": "raw.lot.commissionFee",
    }
    run.event(STAGES[0], "ВЫПОЛНЕНО", f"Закупка {raw['tradeNumber']}; {lot.get('subject') or raw.get('subject') or ''}; "
              f"позиций: {len(lot['lotItems'])}; НМЦК: {lot.get('price')}", seconds=time.monotonic() - start)
    run.data["procurement"]["commission_verified"] = lot.get("commissionFee") is not None
    config = load_config()
    normalized = {**raw, **lot, "id": purchase_id}
    decision = filter_purchase_v2(normalized, raw.get("purchaseTypeTitle") or card.get("purchase_type_title"), config)
    run.data["filter"] = decision
    run.event(STAGES[1], "ВЫПОЛНЕНО", f"Решение фильтра: {decision['filter_result']}",
              "; ".join(decision.get("rejection_reasons") or []) +
              "; диагностический просмотр не означает допуск к участию")
    if run.data["mode"] == "LIVE — batch" and decision.get("filter_result") != "passed":
        run.data["status"] = "FILTERED_OUT"
        run.event("Итог обработки", "ОСТАНОВЛЕН", "Карточка не прошла действующие бизнес-фильтры",
                  "; ".join(decision.get("rejection_reasons") or []))
        return run.finish()
    if not any(goods_position(item) for item in lot["lotItems"]):
        for stage in STAGES[2:]:
            run.event(stage, "НЕ ЗАПУЩЕН", limitation="Нет подтверждённых товарных позиций; товарный поиск запрещён")
        run.data["status"] = "POSITION_KIND_BLOCKED"
        run.data["supplier_search"] = {"ranked_offers": []}
        business = attach_business_decision(run.data)
        run.data["final_decision"] = {"status": business["label"],
                                       "reason": "; ".join(business["decision_reasons"])}
        run.event("Итог контрольного теста", "ОСТАНОВЛЕН", business["label"],
                  run.data["final_decision"]["reason"])
        return run.finish()
    items = lot["lotItems"]
    # Из документов до поиска берутся только товарные сведения, без юридического аудита.
    extraction = _read_route_documents(run, card, "requirements")
    docs = extraction["document_results"]
    resolved = [resolve_item_sources(item, docs, item_number=n, items=items)
                if goods_position(item) else blocked_position(item) for n,item in enumerate(items,1)]
    categories = [classify_purchase_category(item, row) if goods_position(item)
                  else None for item, row in zip(items, resolved)]
    known_categories = [category for category in categories if category]
    if known_categories and len(set(known_categories)) == 1:
        run.data["purchase_category"] = known_categories[0]
    for row, category in zip(resolved, categories):
        if category:
            row["purchase_category"] = category
    audit = {"items": resolved, "additional_expenses": None, "special_conditions": "",
             "contract_analysis": "Отложен до ценового gate и проверки TOP-3",
             "additional_expense_state": {"ready": False, "amount": None,
                                           "reason": "Условия контракта ещё не проверены"}}
    run.data["audit"] = audit
    positions = []
    for number, (item, row) in enumerate(zip(items, resolved), 1):
        category = categories[number - 1]
        model = row.get("original_model") if row.get("model_search_mode") == EXACT_MODEL else None
        discovered = None
        if (category != CATEGORY_NO_MODEL and not model and goods_position(item) and row.get("requirements")
                and row.get("model_discovery_allowed") is True and model_discovery is not None):
            run.event("Извлечённые характеристики ТЗ", "ВЫПОЛНЕНО",
                      "\n".join(f"{r.get('parameter') or r.get('requirement_name')}: "
                                f"{r.get('required_value', r.get('value'))}"
                                for r in row["requirements"]), position=number)
            discovered = model_discovery({**row, "item_number": number,
                                          "item_name": item.get("name") or item.get("description")})
            # Выбор перепроверяется по результатам всех кандидатов, а не имени selected_model.
            selected = select_cheapest_compliant_model(discovered.get("candidates") or [])
            if selected:
                designation = selected.get("exact_model") or selected.get("model_name") or selected.get("model")
                if selected.get('brand') and str(selected['brand']).casefold() not in str(designation).casefold():
                    designation = f"{selected['brand']} {designation}"
                model = commercial_name(row['product_name'], designation)
                row["selected_model"] = model
                row['customer_product']['selected_model'] = model
            run.event(STAGES[8], "ВЫПОЛНЕНО" if model else "НЕПОЛНО",
                      f"Проверено моделей: {len(discovered.get('candidates') or [])}; выбрана: {model or 'нет'}",
                      "; ".join(discovered.get("warnings") or []), position=number)
        elif model:
            run.event(STAGES[8], "НЕ ТРЕБУЕТСЯ", "Есть точная модель; аналоги не подбираются", position=number)
        run.event(STAGES[7], "ВЫПОЛНЕНО" if model else "НЕПОЛНО",
                  f"ВЫБРАННАЯ МОДЕЛЬ: {model or 'не определена'}",
                  row.get("model_mode_reason") or "", position=number)
        prices = {"offers": []}
        search = exact_price_search or price_search
        search_performed = False
        if category != CATEGORY_NO_MODEL and model and goods_position(item) and search is not None:
            try:
                signature = inspect.signature(search)
                accepts_target = ("target_max_unit_price" in signature.parameters or
                                  any(v.kind == inspect.Parameter.VAR_KEYWORD for v in signature.parameters.values()))
                kwargs = {"output_path": price_path(run, number)}
                quantity = float(item.get("quantity") or 0)
                budget = float(lot.get("price") or 0) if len(items) == 1 else float(item.get("unitPrice") or 0) * quantity
                if accepts_target:
                    kwargs["target_max_unit_price"] = budget * .82 / quantity if quantity > 0 else None
                if 'requirements' in signature.parameters or any(v.kind==inspect.Parameter.VAR_KEYWORD for v in signature.parameters.values()):
                    kwargs['requirements'] = row['requirements']
                if 'product_name' in signature.parameters or any(v.kind==inspect.Parameter.VAR_KEYWORD for v in signature.parameters.values()):
                    kwargs['product_name'] = row['product_name']
                search_performed = True
                prices = search(model, **kwargs)
                # Кэши/поиск не вправе вернуть предложение другой модели.
                from model_search.product_evidence import same_exact_model
                prices["offers"] = [o for o in prices.get("offers") or []
                                    if not o.get("model") or same_exact_model(o["model"], model)]
                run.event(STAGES[9], "ВЫПОЛНЕНО" if prices.get("offers") else "НЕПОЛНО",
                          f"{model}; подтверждённых предложений: {len(confirmed_price_ranking(prices.get('offers') or []))}",
                          prices.get("stop_reason") or "Ограниченный поиск по выбранной модели", position=number)
                for offer in prices.get("offers") or []:
                    run.event("Товарное предложение", "ПОДТВЕРЖДЕНО" if offer.get("price") is not None else "НЕПОЛНО",
                              f"{offer.get('seller')}; {model}; цена: {display(offer.get('price'))}; "
                              f"наличие: {offer.get('availability') or 'не подтверждено'}",
                              position=number, source=offer.get("url"))
            except Exception as exc:
                prices.update(search_status='failed', error=type(exc).__name__)
                run.event(STAGES[9], "ОШИБКА", limitation=f"Поиск цены не завершён ({type(exc).__name__})", position=number)
        else:
            limitation = ("Для категории «Закупка товара без существующей модели» ценовой поиск не выполняется"
                          if category == CATEGORY_NO_MODEL
                          else "Нет подтверждённой модели/требований или поиска")
            run.event(STAGES[9], "НЕ ЗАПУЩЕН", limitation=limitation, position=number)
        run.data["positions"].append({"position_number": number, "quantity": item.get("quantity"),
                                     "model": model, "selected_model": model, "prices": prices,
                                     "price_search_performed": search_performed,
                                     "purchase_category": category,
                                     "customer_product":row.get('customer_product'),
                                     "model_discovery": discovered, "model_source": row.get("model_source")})
        positions.append({"position_number": number, "quantity": item.get("quantity"),
                          "customer_unit_price": item.get("unitPrice"), "selected_model": model,
                          "purchase_category": category,
                          "source_offers": prices.get("offers") or []})
    run.data["model_search_mode"] = EXACT_MODEL if all(r.get("model_search_mode") == EXACT_MODEL for r in resolved) else "SELECTED_MODEL"
    if len(positions) == 1:
        run.data["item"] = {"position_number": 1, "quantity": items[0].get("quantity")}
        run.data["model"] = {"selected_model": positions[0]["selected_model"]}
    def verify(row):
        checked = (exact_supplier_verifier or supplier_verifier)(row)
        run.event(STAGES[11], "ВЫПОЛНЕНО", f"{row['supplier_name']}: {checked.get('verification_status')}",
                  "; ".join(checked.get("live_checks_missing") or checked.get("unavailable_checks") or []),
                  source=row.get("product_url"))
        return checked
    def report_gate(flow):
        gate = flow["price_gate"]
        if gate['status']=='NOT_EVALUATED':
            run.event(STAGES[10], 'НЕ ЗАПУЩЕН', 'Модель/подтверждённые цены не готовы для ценового gate')
            return
        run.event(STAGES[10], "ВЫПОЛНЕНО", f"{gate['status']}; порог НМЦК × 0,82 = {display(gate['maximum_purchase_cost'])}",
                  "; ".join(f"позиция {r['position_number']}: {r['qualifying_supplier_count']} разных поставщиков ниже порога"
                            for r in gate["positions"]))
        if gate["passes"] and all(len(confirmed_price_ranking(p['source_offers'], quantity=p['quantity']))>=3 for p in positions):
            run.event("TOP-3", "СФОРМИРОВАН", "Три минимальные подтверждённые цены каждой выбранной модели",
                      "При нехватке третьего предложения итог требует ручной проверки")
    price_ready = all(p['selected_model'] and any(o.get('price') is not None
                      and o.get('price_confirmed_on_product_page') is not False
                      and o.get('exact_model_match') is True for o in p['source_offers']) for p in positions)
    flow = exact_supplier_flow(lot, positions, verifier=verify, on_economics=report_gate,
                               require_top3=True, prices_ready=bool(price_ready))
    run.data["exact_supplier_flow"] = flow
    run.data["price_gate"] = flow["price_gate"]
    run.data["economic_precheck"] = flow["public_economics_before_supplier_approval"]
    unresolved = any(not p['selected_model'] for p in positions)
    if unresolved or not price_ready or not flow["price_gate"]["passes"] or not flow['top3_complete']:
        run.data["status"] = ('MODEL_NOT_RESOLVED' if unresolved else 'PRICE_SEARCH_FAILED' if not price_ready or not flow['top3_complete'] else 'NO_ECONOMIC_SIGNAL')
        reason = {'MODEL_NOT_RESOLVED':'Товарная модель не установлена', 'PRICE_SEARCH_FAILED':'Поиск не дал необходимых подтверждённых цен', 'NO_ECONOMIC_SIGNAL':'Реальные цены найдены, ценовой gate не пройден'}[run.data['status']]
        run.event(STAGES[11], "НЕ ЗАПУЩЕН", limitation=reason)
        run.event("Анализ условий контракта", "НЕ ЗАПУЩЕН", limitation=reason)
    else:
        # Загружаем только контракт. Ранее прочитанные для модели файлы повторно не скачиваются.
        contracts = _read_route_documents(run, card, "contract")
        audit = audit_from_extraction(card, contracts)
        # Источники и уже выбранная модель закреплены до анализа условий.
        audit["items"] = resolved
        run.data["audit"] = audit
        complete = bool(contracts["document_results"]) and all(
            d.get("status") == "analyzed" and d.get("text_available") for d in contracts["document_results"])
        explicit_amount = card.get("additional_expenses")
        from calculator.business_decision import number as monetary_number
        amount = monetary_number(explicit_amount)
        if amount is None and complete and not audit.get("special_conditions"):
            amount = 0
        ready = complete and amount is not None and amount >= 0
        audit["additional_expense_state"] = {"ready": ready, "amount": amount if ready else None,
                                            "reason": "Расходы проверены" if ready else "Условия/стоимость специальных обязанностей не подтверждены"}
        run.data["additional_expenses"] = amount if ready else None
        run.data["mandatory_expenses_included"] = ready
        run.event("Анализ условий контракта", "ВЫПОЛНЕНО" if complete else "НЕПОЛНО",
                  audit.get("contract_analysis") or "", audit.get("special_conditions") or "")
        run.event("Дополнительные расходы", "ВЫПОЛНЕНО" if ready else "НЕПОЛНО",
                  f"{display(run.data['additional_expenses'])} ₽", audit["additional_expense_state"]["reason"])
        run.data["status"] = "COMPLETED_WITH_LIMITATIONS"
    run.data["supplier_search"] = {"ranked_offers": [o for c in flow["candidates"] for o in c["selected_offers"]]}
    if any(category == CATEGORY_NO_MODEL for category in categories):
        run.data["purchase_category"] = CATEGORY_NO_MODEL
        run.data["category_3_unimplemented"] = True
        run.data["current_analysis_result"] = "Алгоритм просчета не доработан"
    warnings = list(run.data.get('warnings') or [])
    for row in resolved:
        if row.get('requirements_complete') is False:
            warnings.append('Требования заказчика прочитаны не полностью или противоречивы')
    for candidate in flow['candidates']:
        position = run.data['positions'][candidate['position_number']-1]
        for offer in candidate.get('shortlist') or []:
            if position['customer_product']['requirements'] and offer.get('customer_requirements_status') != 'fully_compliant':
                warnings.append('Не все существенные характеристики выбранного товара подтверждены')
    run.data['warnings'] = list(dict.fromkeys(warnings))
    business = attach_business_decision(run.data)
    if any(category == CATEGORY_NO_MODEL for category in categories):
        run.data["current_analysis_result"] = "Алгоритм просчета не доработан"
    run.data["final_decision"] = {"status": business["label"], "reason": "; ".join(business["decision_reasons"])}
    run.event(STAGES[12], "НЕПОЛНО" if business["status"] == "manual_review" else "ВЫПОЛНЕНО",
              business["label"], run.data["final_decision"]["reason"])
    run.event("Бизнес-решение", business["label"], business["label"], run.data["final_decision"]["reason"])
    run.path.with_suffix(".md").write_text(decision_markdown(business), encoding="utf-8")
    return run.finish()


def _read_route_documents(run, card, purpose):
    if getattr(run, "document_loader", None) is not None:
        card["documents"] = run.document_loader(card, purpose=purpose)
    selected = [d for d in card.get("documents") or [] if document_for_route(d, purpose)]
    run.event(STAGES[2], "ВЫПОЛНЕНО" if selected else "НЕПОЛНО",
              f"Этап {purpose}; нужных документов: {len(selected)}; "
              f"скачано: {sum(d.get('download_status') == 'downloaded' for d in selected)}")
    extraction = process_procurement_documents(card, selected)
    folder = tender_folder(run.data["purchase_id"], tender_number=run.data["tender_number"])
    write_json(folder / "document_extraction.json", extraction)
    run.event(STAGES[3], "ВЫПОЛНЕНО" if selected and not extraction.get("automation_blocked") else "НЕПОЛНО",
              f"Прочитано: {extraction['documents_processed']}; ошибок: {extraction['documents_failed']}",
              "; ".join(extraction.get("warnings") or []))
    return extraction


def price_path(run, position):
    return run.path.parent / f"{run.data['run_id']}_price_{position}.json"


def run(purchase_id):
    from playwright.sync_api import sync_playwright
    from eat.single_purchase import fetch_purchase_card, download_purchase_documents
    from model_search.playwright_provider import PlaywrightResearch, YandexBrowserSearch
    from model_search.live_discovery import discover_models
    from suppliers.live_verification import verify_live_supplier

    journal = GoogleTestJournal()
    journal.clear_live_rows()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # Публичная карточка не требует входа. Чистый контекст исключает
        # влияние истёкших auth cookies; SPA предварительно загружает главную.
        context = browser.new_context(locale="ru-RU", accept_downloads=True)
        research_context = browser.new_context(locale="ru-RU")
        research = PlaywrightResearch(research_context)
        provider = YandexBrowserSearch(research, max_requests=24)
        page = context.new_page()
        def load_card(pid):
            page.goto("https://agregatoreat.ru/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(15000)
            return fetch_purchase_card(context, page, pid, wait_ms=15000, analyze_documents=False, download_documents=False)
        try:
            return run_check(purchase_id, journal=journal,
                             card_loader=load_card,
                             model_discovery=lambda item: discover_models(item, provider=provider, query_limit=2, use_cache=False),
                             price_search=lambda model, output_path: research.prices(model, provider, output_path),
                             exact_price_search=lambda model, output_path, **kwargs: research.prices_exact(model, provider, output_path, **kwargs),
                             exact_supplier_verifier=lambda row: verify_live_supplier(row, reader=research.read),
                             document_loader=lambda card, purpose: download_purchase_documents(context, card, purpose=purpose))
        finally:
            research_context.close()
            context.close()
            browser.close()
