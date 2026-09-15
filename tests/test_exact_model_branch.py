"""Профильные проверки EXACT_MODEL без сети и Google credentials."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from documents.pipeline import audit_from_extraction
from model_search.exact_model import EXACT_MODEL, resolve_exact_model
from model_search.live_discovery import discover_models
from model_search.playwright_provider import PlaywrightResearch
from model_search.price_readiness import classify_price_search_readiness
from pipeline.single_purchase_test import run_check
from pipeline.mvp_exact_batch import _classify_model, _customer_exact_supplier_result
from suppliers.exact_model_flow import exact_supplier_flow
from suppliers.live_verification import verify_live_supplier
from suppliers.verification import HIGH_RISK, INSUFFICIENT, PASSED

MODEL = "Kyocera Ecosys MA3500X"
PID = "93ffd454-9d9e-49f5-a4d7-acbcf8cdbe8b"


def doc(text=MODEL, kind="contract_draft", page=11):
    if kind in ("contract_draft", "contract_appendix"):
        text = "Техническое задание\n" + text
    return {"document_name": "Проект контракта.pdf", "document_type": [kind],
            "status": "analyzed", "text_available": True, "text": text,
            "pages": [{"page_number": page, "text": text}], "source": "https://eat.example/document"}


def extraction(documents):
    return {"procurement_id": PID, "document_results": documents, "combined_text": "\n".join(d["text"] for d in documents),
            "documents_found": len(documents), "documents_processed": len(documents),
            "documents_failed": 0, "warnings": [],
            "extraction_summary": {"partial_documents": 0, "ocr_documents": 0, "cache_hits": 0}}


def offer(seller, price):
    return {"seller": seller, "url": f"https://{seller}.example/product/ma3500x", "price": price,
            "exact_model_match": True, "availability": "В наличии"}


class ExactModelBranchTests(unittest.TestCase):
    def test_packaged_customer_span_enters_exact_branch_without_shortening(self):
        item = {"name": "Клей плиточный 25 кг",
                "description": "Клей для керамической плитки Bergauf Keramik PLUS 25 кг",
                "quantity": 400}
        resolved = resolve_exact_model(item, [])
        self.assertEqual(resolved["model_search_mode"], EXACT_MODEL)
        self.assertEqual(resolved["original_model"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(resolved["customer_model_raw"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(resolved["model_evidence"][0]["evidence"], "Bergauf Keramik PLUS 25 кг")

    def test_short_eat_sku_and_full_document_name_preserve_full_name(self):
        resolved = resolve_exact_model({"name": "МФУ", "model": "MA3500X"}, [doc()])
        self.assertEqual(resolved["model_search_mode"], EXACT_MODEL)
        self.assertEqual(resolved["original_model"], MODEL)

    def test_same_sku_of_different_brands_does_not_remove_conflict(self):
        self.assertIsNone(resolve_exact_model({"name": MODEL}, [doc("Canon Ecosys MA3500X")]))

    def test_customer_named_model_without_equivalent_is_exact_and_price_ready(self):
        resolved = resolve_exact_model({"name": "МФУ"}, [doc()])
        self.assertEqual(resolved["model_search_mode"], EXACT_MODEL)
        self.assertEqual(classify_price_search_readiness({"model": "MA3500X"}, resolved)["identifier"], MODEL)
        self.assertFalse(resolved["compliance_required"])

    def test_equivalent_or_analogue_prices_original_model_first(self):
        for permission in ("или эквивалент", "эквивалент", "аналог", "допускается другая модель",
                           "разрешается предложение иной модели", "замена модели допускается"):
            with self.subTest(permission=permission):
                result = resolve_exact_model({"name": "МФУ"}, [doc(MODEL + " " + permission)])
                self.assertEqual(result["original_model"], MODEL)
                self.assertEqual(result["model_search_mode"], EXACT_MODEL)
                self.assertFalse(result["model_discovery_allowed"])

    def test_permission_in_another_document_preserves_original_model(self):
        self.assertEqual(resolve_exact_model({"name": "МФУ"}, [doc(), doc("Допускается другая модель")])["original_model"], MODEL)

    def test_pantum_equivalent_skips_full_requirements_and_model_discovery(self):
        card = {"raw": {"lotItems": [{"name": "Pantum M6607NW или эквивалент"}]}}
        with patch("documents.pipeline.resolve_item_sources", wraps=__import__("documents.item_sources", fromlist=["resolve_item_sources"]).resolve_item_sources) as resolver:
            audit = audit_from_extraction(card, extraction([doc("Pantum M6607NW или эквивалент")]))
        resolver.assert_not_called()
        self.assertEqual(audit["items"][0]["model_search_mode"], EXACT_MODEL)
        self.assertEqual(classify_price_search_readiness({}, audit["items"][0])["identifier"], "Pantum M6607NW")

    def test_contract_or_specification_provides_model_with_page_and_fragment(self):
        for kind in ("contract_draft", "specification", "technical_specification"):
            with self.subTest(kind=kind):
                result = resolve_exact_model({"name": "МФУ"}, [doc("Товарный знак Kyocera Ecosys\nMA3500X", kind)])
                self.assertEqual(result["original_model"], MODEL)
                self.assertEqual(result["model_evidence"][0]["source_page"], 11)
                self.assertIn("MA3500X", result["model_evidence"][0]["evidence"])

    def test_official_price_attachment_provides_initial_pricing_model(self):
        result = resolve_exact_model({"name": "МФУ"}, [doc(MODEL, "price_justification")])
        self.assertEqual(result["original_model"], MODEL)
        self.assertEqual(result["model_source"], "PRICE_JUSTIFICATION")

    def test_different_customer_models_require_original_workflow(self):
        self.assertIsNone(resolve_exact_model({"name": "МФУ"}, [doc(MODEL + "\nKyocera Ecosys MA3500FX")]))

    def test_unread_table_does_not_block_named_customer_model(self):
        missing = {**doc(""), "status": "missing"}
        self.assertEqual(resolve_exact_model({"name": MODEL}, [missing])["original_model"], MODEL)

    def test_unrelated_electronic_signature_equivalence_is_not_model_permission(self):
        result = resolve_exact_model({"name": "МФУ"}, [doc(MODEL + "\nКЭП эквивалентно подписи на бумаге")])
        self.assertEqual(result["model_search_mode"], EXACT_MODEL)

    def test_missing_model_keeps_original_workflow(self):
        self.assertIsNone(resolve_exact_model({"name": "МФУ"}, [doc("Скорость печати: 40 стр/мин")]))

    def test_partial_characteristic_table_is_skipped_without_parsing(self):
        card = {"raw": {"lotItems": [{"name": "МФУ"}]}}
        documents = [doc(MODEL + "\nНаименование характеристики\nЗначение характеристики\nНечитаемая структура таблицы")]
        with patch("documents.pipeline.resolve_item_sources", side_effect=AssertionError("Полный разбор не нужен")):
            result = audit_from_extraction(card, extraction(documents))
        self.assertEqual(result["items"][0]["requirements"], [])
        self.assertEqual(result["document_warnings"], [])
        self.assertIn("не требуется", result["tz_status"])

    def test_discovery_cannot_search_analogues_for_exact_mode(self):
        provider = MagicMock()
        result = discover_models(resolve_exact_model({"name": "МФУ"}, [doc()]), provider=provider)
        provider.search.assert_not_called()
        self.assertEqual(result["queries_used"], [])

    def test_partial_table_goes_directly_to_full_name_price_search_then_antifraud(self):
        card = {"raw": {"tradeNumber": "200909083126100184", "lot": {"price": 66900,
                "commissionFee": 2040.45, "lotItems": [{"name": "МФУ", "quantity": 2, "unitPrice": 33450}]}},
                "documents": [{"file_name": "Проект контракта.pdf", "document_type": 15, "download_status": "downloaded"}]}
        ex = extraction([doc(MODEL + "\nНаименование характеристики\nЗначение характеристики")])
        journal = MagicMock()
        journal.verify.side_effect = lambda run: {"rows_verified": len(run["events"])}
        calls = []
        def search(model, output_path):
            calls.append("price")
            self.assertEqual(model, MODEL)
            return {"offers": [offer("low", 25000), offer("low2", 25001)], "minimum_price": 25000}
        def verify(row):
            calls.append("antifraud")
            return {**row, "verification_status": PASSED}
        discovery = MagicMock(side_effect=AssertionError("Аналоги запрещены"))
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test.tender_folder", return_value=Path(directory)), \
             patch("pipeline.single_purchase_test.process_procurement_documents", return_value=ex), \
             patch("pipeline.single_purchase_test.audit_from_extraction", wraps=audit_from_extraction):
            result = run_check(PID, journal=journal, card_loader=lambda _: card,
                               price_search=search, model_discovery=discovery,
                               supplier_verifier=verify, output_dir=Path(directory))
        self.assertEqual(result["model_search_mode"], EXACT_MODEL)
        self.assertEqual(calls, ["price", "antifraud", "antifraud"])
        discovery.assert_not_called()
        stages = [e["stage"] for e in result["events"]]
        self.assertLess(stages.index("Экономический предаудит"), stages.index("Проверка поставщиков"))
        self.assertNotEqual(result["final_decision"]["status"], "ГОТОВО К УЧАСТИЮ")


class ExactPriceAndSupplierTests(unittest.TestCase):
    def test_missing_inn_is_not_reported_as_multiple_conflicting_inns(self):
        with patch("suppliers.live_verification.rdap_check", return_value={"whois_status": "unavailable"}), \
             patch("suppliers.live_verification.wayback_check", return_value={"wayback_status": "unavailable"}):
            result = verify_live_supplier({"product_url": "https://shop.example/product/x"},
                                          reader=lambda url: ("<html>Карточка товара</html>", url, 200))
        self.assertIsNone(result["verification_checks"]["requisites_consistent"])
        self.assertNotIn("среди нескольких ИНН", result["verification_comment"])

    def test_live_mvp_preserves_exact_mode_and_uses_same_price_first_flow(self):
        resolved = resolve_exact_model({"name": "МФУ"}, [doc()])
        mode = _classify_model(resolved, {"model": "MA3500X"})
        self.assertEqual(mode["mode"], EXACT_MODEL)
        self.assertEqual(mode["model"], MODEL)
        self.assertEqual(_classify_model({**resolved, "model_source": "PRICE_JUSTIFICATION"})["mode"], EXACT_MODEL)
        calls = []
        def search(model, output_path):
            self.assertEqual(model, MODEL)
            calls.append("price")
            return {"offers": [offer("good", 25000), offer("good2", 25001)]}
        def verify(row):
            calls.append("antifraud")
            return {**row, "verification_status": PASSED}
        result = _customer_exact_supplier_result({"name": "МФУ", "position_kind": "goods", "model": mode,
            "quantity": 2, "customer_unit_price": 33450,
            "trade_number": "200909083126100184", "item_number": 1, "nmck": 66900, "commission_fee": 2040.45},
            {"calculator": {"eat_commission_rate": .03}}, price_search=search, verifier=verify)
        self.assertEqual(calls, ["price", "antifraud", "antifraud"])
        self.assertEqual(result["model_search_mode"], EXACT_MODEL)
        self.assertEqual(result["preliminary_economics"]["public_total_for_quantity"], 50000)
        self.assertIsNone(result["preliminary_economics"]["purchase_price"])

    def test_multiple_queries_check_later_cheaper_supplier_and_deduplicate(self):
        research = PlaywrightResearch(MagicMock())
        provider = MagicMock()
        provider.search.side_effect = [[{"url": offer("a", 30000)["url"]}],
                                       [{"url": offer("a", 30000)["url"]}, {"url": offer("b", 25000)["url"]}],
                                       [{"url": offer("c", 20000)["url"]}]]
        def inspect(row, model, fetcher, **kwargs):
            seller = row["product_url"].split("//")[1].split(".")[0]
            return {**offer(seller, {"a": 30000, "b": 25000, "c": 20000}[seller]), "exact_product_name": MODEL}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("model_search.playwright_provider.inspect_product_url", side_effect=inspect) as checker:
            result = research.prices_exact(MODEL, provider, Path(directory)/"prices.json")
        # Все уникальные URL из нескольких запросов должны быть проверены;
        # количество вызовов inspect не является production-контрактом.
        self.assertGreaterEqual(checker.call_count, 3)
        self.assertEqual(result["minimum_price"], 20000)
        self.assertEqual(len(result["offers"]), 3)
        self.assertTrue(all(MODEL in call.args[0] for call in provider.search.call_args_list))
        self.assertGreaterEqual(len(result["queries_used"]), 3)
        self.assertLessEqual(len(result["queries_used"]), 8)

    def test_expensive_prices_never_trigger_antifraud_and_red_supplier_is_excluded(self):
        calls = []
        def verify(row):
            calls.append(row["supplier_name"])
            return {**row, "verification_status": HIGH_RISK if row["supplier_name"] == "bad" else PASSED}
        result = exact_supplier_flow({"price": 66900, "commissionFee": 2040.45},
            [{"position_number": 1, "quantity": 2, "source_offers": [offer("expensive", 50000),
              offer("bad", 20000), offer("good", 25000)]}], verifier=verify)
        self.assertEqual(calls, ["bad", "good"])
        self.assertEqual(result["economics"]["minimum_purchase_cost"], 50000)
        self.assertEqual(result["commission"], 2040.45)
        self.assertEqual(result["candidates"][0]["selected_offers"][0]["supplier_name"], "good")

    def test_supplier_live_checks_do_not_invent_registry_or_kad_success(self):
        def read(url):
            return "<html><body>ИНН 7701234567</body></html>", url, 200
        with patch("suppliers.live_verification.rdap_check", return_value={"whois_status": "unavailable"}), \
             patch("suppliers.live_verification.wayback_check", return_value={"wayback_status": "unavailable"}):
            result = verify_live_supplier({"product_url": "https://shop.example/product/x"}, reader=read)
        self.assertTrue(str(result["verification_status"]).startswith("🟡"))
        self.assertIn("КАД", result["live_checks_missing"])
        self.assertIsNone(result["verification_checks"]["company"]["active"])


if __name__ == "__main__":
    unittest.main()
