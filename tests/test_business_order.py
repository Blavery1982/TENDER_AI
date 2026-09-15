"""Порядок бизнес-этапов без сети, credentials и записи в рабочую таблицу."""
import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from calculator.result_decision import attach_business_decision
from eat.single_purchase import download_purchase_documents, fetch_purchase_card
from model_search.live_price_search import inspect_product_url
from pipeline.single_purchase_test import run_check
from suppliers.exact_model_flow import exact_supplier_flow, price_signal
from suppliers.price_search_flow import confirmed_price_ranking
from suppliers.verification import PASSED, HIGH_RISK

PID = "93ffd454-9d9e-49f5-a4d7-acbcf8cdbe8b"
MODEL = "Pantum M6607NW"


def offer(seller, price, **kwargs):
    return {"seller": seller, "url": f"https://{seller}.ru/product/printer",
            "model": MODEL, "price": price, "exact_model_match": True,
            "price_confirmed_on_product_page": True, "availability": "В наличии", **kwargs}


def card(model=MODEL):
    item = {"name": "Принтер", "quantity": 2, "unitPrice": 500, "position_kind": "goods"}
    if model:
        item["model"] = model
    return {"purchase_id": PID, "raw": {"id": PID, "tradeNumber": "test-business-order",
            "lot": {"price": 1000, "commissionFee": 0, "lotItems": [item]}},
            "documents": [{"file_name": "Проект контракта.txt", "document_type": 15,
                           "download_status": "deferred", "text": "Порядок поставки\nТиповая поставка."}]}


class Journal:
    def write_many(self, result):
        self.result = copy.deepcopy(result)

    def verify(self, result):
        return {"rows_verified": len(result["events"])}


class BusinessOrderTests(unittest.TestCase):
    def run_route(self, source, offers, *, discovery=None, journal=None):
        calls = []
        def loader(card, purpose):
            calls.append("documents:" + purpose)
            return [{**d, "download_status": "downloaded"} for d in card["documents"]]
        def extraction(card, docs):
            calls.append("read")
            results = [{**d, "document_name": d["file_name"],
                        "source_document_type": d["document_type"],
                        "document_type": ["contract_draft" if d["document_type"] == 15 else "price_justification"],
                        "status": "analyzed", "text_available": True, "pages": []} for d in docs]
            return {"procurement_id": PID, "documents_found": len(docs), "documents_processed": len(docs),
                    "documents_failed": 0, "document_results": results, "warnings": [],
                    "combined_text": "\n".join(d["text"] for d in docs),
                    "extraction_summary": {"partial_documents": 0, "ocr_documents": 0, "cache_hits": 0}}
        def search(model, output_path, **kwargs):
            calls.append("prices:" + model)
            self.assertEqual(kwargs["target_max_unit_price"], 410)
            return {"offers": copy.deepcopy(offers)}
        def verify(row):
            calls.append("verify:" + row["seller"])
            return {**row, "verification_status": PASSED}
        from documents.pipeline import audit_from_extraction
        def audit(*args, **kwargs):
            calls.append("contract-analysis")
            return audit_from_extraction(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test.tender_folder", return_value=Path(directory)), \
             patch("pipeline.single_purchase_test.process_procurement_documents", side_effect=extraction), \
             patch("pipeline.single_purchase_test.audit_from_extraction", side_effect=audit):
            result = run_check(PID, journal=journal or Journal(), card_loader=lambda _: source,
                price_search=search, model_discovery=discovery, supplier_verifier=verify,
                document_loader=loader, output_dir=Path(directory))
        return result, calls

    def test_card_model_collects_requirements_before_price_search(self):
        result, calls = self.run_route(card(), [offer("one", 400)])
        self.assertEqual(calls, ["documents:requirements", "read", "prices:" + MODEL])
        self.assertEqual(result["status"], "PRICE_SEARCH_FAILED")
        self.assertEqual(result["model"]["selected_model"], MODEL)

    def test_one_good_price_never_runs_antifraud_kad_or_contract(self):
        result, calls = self.run_route(card(), [offer("one", 400), offer("expensive", 411)])
        self.assertFalse(result["price_gate"]["passes"])
        self.assertEqual(result["exact_supplier_flow"]["antifraud_history"], [])
        self.assertEqual(calls, ["documents:requirements", "read", "prices:" + MODEL])

    def test_good_signal_top3_then_only_selected_suppliers_then_contract(self):
        result, calls = self.run_route(card(), [offer("d", 405), offer("c", 400),
                                                offer("a", 380), offer("b", 390)])
        self.assertEqual(calls, ["documents:requirements", "read", "prices:" + MODEL, "verify:a", "verify:b", "verify:c",
                                 "documents:contract", "read", "contract-analysis"])
        self.assertTrue(result["price_gate"]["passes"])
        self.assertEqual(result["final_economics"]["average_purchase_price"], 780)
        self.assertEqual(result["additional_expenses"], 0)
        stages = [e["stage"] for e in result["events"]]
        self.assertLess(stages.index("TOP-3"), stages.index("Проверка поставщиков"))
        self.assertLess(stages.index("Проверка поставщиков"), stages.index("Анализ условий контракта"))

    def test_document_model_skips_characteristics_and_discovery(self):
        source = card(None)
        source["documents"].insert(0, {"file_name": "Обоснование цены.txt", "document_type": 14,
            "download_status": "deferred", "text": MODEL})
        discovery = MagicMock(side_effect=AssertionError("Подбор не нужен"))
        result, calls = self.run_route(source, [offer("one", 400)], discovery=discovery)
        self.assertEqual(calls[:3], ["documents:requirements", "read", "prices:" + MODEL])
        self.assertNotIn("contract-analysis", calls)
        self.assertEqual(result["positions"][0]["model_source"], "PRICE_JUSTIFICATION")
        discovery.assert_not_called()

    def test_requirements_only_then_cheapest_fully_compliant_model_is_locked(self):
        source = card(None)
        source["raw"]["lot"]["lotItems"][0]["characteristics"] = [
            {"name": "Мощность", "value": "10 Вт", "operator": "minimum"},
            {"name": "Цвет", "value": "белый"}]
        def discover(item):
            self.assertEqual({r["requirement_name"] for r in item["requirements"]}, {"Мощность", "Цвет"})
            return {"candidates": [
                {"exact_model": "Cheap X100", "status": "non_compliant", "public_price": 1, "russia_availability": "available"},
                {"exact_model": "Unknown X200", "status": "partially_compliant", "public_price": 2, "russia_availability": "available"},
                {"exact_model": MODEL, "status": "fully_compliant", "public_price": 10, "russia_availability": "available"},
                {"exact_model": "Other X300", "status": "fully_compliant", "public_price": 20, "russia_availability": "available"}]}
        result, calls = self.run_route(source, [offer("one", 400)], discovery=discover)
        self.assertEqual(result["model"]["selected_model"], MODEL)
        self.assertEqual(calls[-1], "prices:" + MODEL)

    def test_contract_obligations_leave_unpriced_expenses_unknown(self):
        source = card()
        source["documents"][0]["text"] = "Поставщик обязан выполнить монтаж и разгрузку товара."
        result, calls = self.run_route(source, [offer("a", 380), offer("b", 390), offer("c", 400)])
        self.assertIsNone(result["additional_expenses"])
        self.assertFalse(result["final_economics"]["complete"])
        self.assertEqual(result["business_decision"]["status"], "manual_review")

    def test_sheets_failure_preserves_completed_calculation_without_research(self):
        journal = MagicMock()
        journal.write_many.side_effect = ConnectionError("secret")
        result, calls = self.run_route(card(), [offer("a", 380), offer("b", 390), offer("c", 400)], journal=journal)
        self.assertEqual(result["google_sheets"]["status"], "EXPORT_ERROR")
        self.assertTrue(result["final_economics"]["complete"])
        self.assertEqual(sum(c.startswith("prices:") for c in calls), 1)
        self.assertNotIn("secret", str(result))

    def test_local_purchase_error_does_not_prevent_next_purchase(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            bad = run_check(PID, journal=Journal(), card_loader=lambda _: {}, output_dir=Path(directory))
        good, _ = self.run_route(card(), [offer("one", 400)])
        self.assertEqual(bad["status"], "CARD_NOT_RECEIVED")
        self.assertEqual(good["status"], "PRICE_SEARCH_FAILED")


class ProductAndGateTests(unittest.TestCase):
    def test_bergauf_full_identity_not_other_series_or_packaging(self):
        model = "Bergauf Keramik PLUS 25 кг"
        def inspect(name):
            body = f'<h1>{name}</h1><p>Цена: 100 руб.</p><p>В наличии</p>'
            return inspect_product_url({"product_url": "https://shop.ru/product/glue"}, model,
                fetcher=lambda url: (body, url, 200))
        self.assertIsNone(inspect("Bergauf Other PLUS 25 л"))
        self.assertIsNone(inspect("Bergauf Keramik PLUS 25 л"))
        self.assertIsNotNone(inspect(model))

    def test_marketplace_requires_confirmed_distinct_real_sellers(self):
        rows = [offer(s, 100+i, url=f"https://market.yandex.ru/product--printer/{i}",
                      seller_identity_confirmed=True) for i, s in enumerate(("ООО А", "ООО Б", "ООО В"))]
        self.assertEqual(len(confirmed_price_ranking(rows)), 3)
        self.assertEqual(len(confirmed_price_ranking([{**r, "seller_identity_confirmed": False} for r in rows])), 0)
        from calculator.business_decision import supplier_domain
        self.assertEqual(len({supplier_domain(r) for r in rows}), 3)

    def test_gate_ignores_missing_or_large_commission_and_uses_boundary_quantity(self):
        positions = [{"position_number": 1, "quantity": 2,
                      "source_offers": [offer("a", 410), offer("b", 410)]}]
        for fee in (None, 900):
            self.assertTrue(price_signal({"price": 1000, "commissionFee": fee}, positions)["passes"])
        positions[0]["source_offers"][1]["price"] = 410.01
        self.assertFalse(price_signal({"price": 1000}, positions)["passes"])

    def test_red_top3_supplier_does_not_trigger_checks_of_remaining_sellers(self):
        calls = []
        def verify(row):
            calls.append(row["seller"])
            return {"verification_status": HIGH_RISK if row["seller"] == "a" else PASSED}
        flow = exact_supplier_flow({"price": 1000}, [{"position_number": 1, "quantity": 2,
            "source_offers": [offer(s, p) for s, p in (("a", 380), ("b", 390), ("c", 400), ("d", 405))]}], verifier=verify)
        self.assertEqual(calls, ["a", "b", "c"])
        self.assertEqual(len(flow["positions"][0]["offers"]), 2)

    def test_total_quote_is_not_multiplied_again(self):
        positions = [{"position_number": 1, "quantity": 2, "source_offers": [
            offer("a", 800, full_quote_price=800), offer("b", 810, full_quote_price=810)]}]
        self.assertTrue(price_signal({"price": 1000}, positions)["passes"])

    def test_changed_quantity_quotes_and_expenses_recompute_saved_economics(self):
        flow = exact_supplier_flow({"price": 1000, "commissionFee": 0}, [{"position_number": 1,
            "quantity": 2, "source_offers": [offer("a", 380), offer("b", 390), offer("c", 400)]}],
            verifier=lambda row: {"verification_status": PASSED})
        result = {"procurement": {"nmck": 1000, "commission_fee": 0}, "item": {"quantity": 2},
                  "exact_supplier_flow": flow, "additional_expenses": 0}
        attach_business_decision(result)
        self.assertEqual(result["final_economics"]["average_purchase_price"], 780)
        result["item"]["quantity"] = 3
        result["additional_expenses"] = 50
        flow["candidates"][0]["selected_offers"][0]["public_price"] = 300
        attach_business_decision(result)
        self.assertEqual(result["final_economics"]["average_purchase_price"], 1090)
        self.assertEqual(result["final_economics"]["additional_expenses"], 50)


class BatchAndReaderTests(unittest.TestCase):
    def test_batch_processes_next_purchase_after_local_error_and_exports_selected_model(self):
        import pipeline.mvp_exact_batch as batch
        from datetime import timedelta
        lower, upper = batch.deadline_window()
        good = card()
        good["raw"]["applicationFillingEndDate"] = (lower + timedelta(hours=1)).isoformat()
        bad_raw = {**good["raw"], "id": "00000000-0000-4000-8000-000000000001", "tradeNumber": "bad"}
        good["raw"]["lot"]["subject"] = "Принтер"
        pagination = {"unique_records": [bad_raw, good["raw"]], "raw_count": 2}
        sheets = []
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch.object(batch, "sync_playwright"), patch.object(batch, "open_authorized_eat_browser"), \
             patch.object(batch, "_capture_and_collect", return_value=(pagination, {})), \
             patch.object(batch, "filter_purchase_v2", return_value={"filter_result": "passed"}), \
             patch("pipeline.single_purchase_test.filter_purchase_v2", return_value={"filter_result": "passed"}), \
             patch.object(batch, "fetch_purchase_card", side_effect=[RuntimeError("secret"), good]) as fetch, \
             patch.object(batch, "_atomic_json"), patch.object(batch, "_write_reports"), patch.object(batch, "_logger"), \
             patch("model_search.playwright_provider.PlaywrightResearch") as research, \
             patch("model_search.yandex_provider.YandexSearchProvider"), \
             patch("pipeline.single_purchase_test.ROOT", Path(directory)):
            research.return_value.prices_exact.return_value = {"offers": [offer("one", 400)]}
            result = batch.run(sheet_writer=lambda payload: sheets.append(payload) or {"status": "saved"})
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(c.kwargs["download_documents"] is False for c in fetch.call_args_list))
        good_item = next(i for i in result["items"] if i["purchase_id"] == PID)
        self.assertEqual(good_item["business_order_result"]["status"], "NO_ECONOMIC_SIGNAL")
        self.assertEqual(next(p for p in sheets if p["procurement"]["id"] == PID)["model"]["selected_model"], MODEL)
        self.assertNotIn("secret", str(result))

    def test_primary_price_search_stops_at_bound_without_two_good_prices(self):
        from model_search.playwright_provider import PlaywrightResearch
        research = PlaywrightResearch(MagicMock())
        provider = MagicMock()
        provider.search.return_value = [{"url": f"https://shop{i}.ru/product/printer"} for i in range(8)]
        def inspect(row, model, **kwargs):
            return offer("shop", 500, url=row["product_url"], exact_product_name=MODEL)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("model_search.playwright_provider.inspect_product_url", side_effect=inspect):
            result = research.prices_exact(MODEL, provider, Path(directory)/"prices.json",
                target_max_unit_price=410, price_gate_source_limit=3)
        self.assertEqual(result["sources_checked"], 3)
        self.assertIn("три подтверждённых", result["stop_reason"])

    def test_cli_export_error_returns_local_result_without_key_error(self):
        import main
        with patch("sys.argv", ["main.py", "--single-purchase-test", PID]), \
             patch("pipeline.single_purchase_test.run", return_value={"status": "COMPLETED_WITH_LIMITATIONS",
                   "google_sheets": {"status": "EXPORT_ERROR", "local_result": "/tmp/result.json"}}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 1)

    def test_good_signal_continues_past_primary_limit_to_find_cheapest_top3(self):
        from model_search.playwright_provider import PlaywrightResearch
        research = PlaywrightResearch(MagicMock())
        provider = MagicMock()
        provider.search.return_value = [{"url": f"https://shop{i}.ru/product/printer"} for i in range(5)]
        def inspect(row, model, **kwargs):
            index = int(row["product_url"].split("shop")[1].split(".")[0])
            return offer(f"shop{index}", 400-index, url=row["product_url"], exact_product_name=MODEL)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("model_search.playwright_provider.inspect_product_url", side_effect=inspect):
            result = research.prices_exact(MODEL, provider, Path(directory)/"prices.json",
                target_max_unit_price=410, price_gate_source_limit=2, stop_after_top3=False)
        self.assertEqual(result["sources_checked"], 5)
        self.assertEqual([r["price"] for r in result["top3_confirmed_prices"]], [396, 397, 398])

    def test_full_quote_and_unit_prices_are_ranked_by_cost_of_required_quantity(self):
        rows = [offer("a", 400), offer("b", 750, full_quote_price=750),
                offer("c", 390), offer("d", 500)]
        flow = exact_supplier_flow({"price": 1000}, [{"position_number": 1, "quantity": 2,
            "source_offers": rows}], verifier=lambda row: {"verification_status": PASSED})
        self.assertEqual([r["seller"] for r in flow["candidates"][0]["shortlist"]], ["b", "c", "a"])

    def test_changed_selected_model_does_not_reuse_previous_quote_economics(self):
        flow = exact_supplier_flow({"price": 1000, "commissionFee": 0}, [{"position_number": 1,
            "quantity": 2, "source_offers": [offer("a", 380), offer("b", 390), offer("c", 400)]}],
            verifier=lambda row: {"verification_status": PASSED})
        result = {"procurement": {"nmck": 1000, "commission_fee": 0}, "item": {"quantity": 2},
                  "exact_supplier_flow": flow, "additional_expenses": 0, "model": {"selected_model": MODEL}}
        attach_business_decision(result)
        self.assertTrue(result["final_economics"]["complete"])
        result["model"]["selected_model"] = "Pantum M6500"
        attach_business_decision(result)
        self.assertFalse(result["final_economics"]["complete"])

    def test_batch_card_filter_stops_before_price_or_document_calls(self):
        search, loader = MagicMock(), MagicMock()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test.filter_purchase_v2", return_value={"filter_result": "rejected", "rejection_reasons": ["region"]}):
            result = run_check(PID, journal=Journal(), card_loader=lambda _: card(), price_search=search,
                               document_loader=loader, output_dir=Path(directory), mode="LIVE — batch")
        self.assertEqual(result["status"], "FILTERED_OUT")
        search.assert_not_called()
        loader.assert_not_called()

    def test_systemic_auth_error_is_not_local_purchase_skip(self):
        from eat.browser_auth import BrowserAuthError
        def loader(_):
            raise BrowserAuthError("Авторизация потеряна")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(BrowserAuthError):
                run_check(PID, journal=Journal(), card_loader=loader, output_dir=Path(directory), mode="LIVE — batch")
