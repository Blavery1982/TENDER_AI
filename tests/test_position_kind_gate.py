"""Профильные проверки типов позиций и допуска к поиску товара без сети/AI."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from filters.position_kind import classify_position
from filters.semantic_bad_words import filter_purchase_v2, procurement_kind
from documents.item_sources import resolve_item_sources
from documents.pipeline import audit_from_extraction
from model_search.exact_model import resolve_exact_model
from model_search.price_readiness import extract_direct_identifier, classify_price_search_readiness
from model_search.search_mode import determine_model_search_mode
from model_search.live_discovery import discover_models
from pipeline.mvp_exact_batch import _classify_model, _process_suppliers, _customer_exact_supplier_result
from pipeline.single_purchase_test import run_check, STAGES
from google_sheets import workbook

LAW = "Закупка по Закону №44-ФЗ"


def purchase(items):
    return {"id": "bfb7719b-656f-42dc-a15c-908589dc7108", "tradeNumber": "200909993126100032",
            "subject": items[0].get("name", ""), "price": 270000, "purchaseTypeTitle": LAW,
            "lotItems": items, "deliveryInfos": [{"deliveryAddress": {"regionName": "Московская область"}}]}


def repair_position():
    return {"name": "Ремонт", "description": "Экскаватора-бульдозера марки ЭО-2621Е ОР 6508",
            "eat": {"code": "29", "title": "РАБОТЫ"}, "quantity": 1, "unitPrice": 270000}


def extraction():
    return {"procurement_id": "local", "document_results": [], "combined_text": "", "warnings": [],
            "documents_found": 0, "documents_processed": 0, "documents_failed": 0,
            "extraction_summary": {"partial_documents": 0, "ocr_documents": 0, "cache_hits": 0}}


class MemoryJournal:
    def write(self, run, event):
        pass

    def verify(self, run):
        return {"rows_verified": len(run["events"])}


class PositionKindGateTests(unittest.TestCase):
    def assert_blocked(self, item, kind):
        self.assertEqual(classify_position(item)["position_kind"], kind)
        self.assertIsNone(resolve_exact_model(item, []))
        self.assertIsNone(extract_direct_identifier(item))
        resolved = resolve_item_sources(item, [])
        readiness = classify_price_search_readiness(item, resolved)
        self.assertFalse(readiness["price_search_ready"])
        self.assertIsNone(readiness["identifier"])
        self.assertNotEqual(_classify_model(resolved, item)["route"], "exact")
        self.assertFalse(determine_model_search_mode(item)["model_discovery_allowed"])

    def test_repair_equipment_is_works(self):
        self.assert_blocked({"name": "Ремонт экскаватора ЭО-2621Е"}, "works")

    def test_printer_maintenance_is_services(self):
        self.assert_blocked({"name": "Техническое обслуживание принтера HP LaserJet M404dn"}, "services")

    def test_air_conditioner_diagnostics_is_services(self):
        self.assert_blocked({"name": "Диагностика кондиционера Ballu BSL-09HN1"}, "services")

    def test_printer_supply_allows_exact_model(self):
        item = {"name": "Поставка принтера HP LaserJet M404dn"}
        self.assertEqual(classify_position(item)["position_kind"], "goods")
        resolved = resolve_exact_model(item, [])
        self.assertEqual(resolved["model_search_mode"], "EXACT_MODEL")
        self.assertTrue(classify_price_search_readiness(item, resolved)["price_search_ready"])

    def test_air_conditioner_supply_keeps_installation_conditions(self):
        item = {"name": "Поставка кондиционера Ballu BSL-09HN1 с монтажом"}
        decision = filter_purchase_v2(purchase([item]), LAW)
        self.assertEqual(decision["filter_result"], "passed")
        self.assertEqual(decision["procurement_kind"], "goods")
        audit = audit_from_extraction({"raw": purchase([item])}, extraction())
        self.assertIn("монтажом", audit["special_conditions"])
        self.assertEqual(audit["items"][0]["model_search_mode"], "EXACT_MODEL")
        described = {"name": "Кондиционер Ballu BSL-09HN1", "description": "Монтаж включён в поставку"}
        self.assertEqual(classify_position(described)["position_kind"], "goods")
        self.assertEqual(filter_purchase_v2(purchase([described]), LAW)["filter_result"], "passed")
        self.assert_blocked({"name": "Монтаж кондиционера Ballu BSL-09HN1"}, "works")

    def test_official_works_overrides_goods_assumption(self):
        item = {"name": "Поставка HP LaserJet M404dn", "eat": {"title": "РАБОТЫ"}, "position_kind": "goods"}
        self.assert_blocked(item, "works")
        self.assertEqual(classify_position(item)["position_kind_source"], "eat.title")

    def test_flattened_official_services_overrides_equipment(self):
        self.assert_blocked({"name": "HP LaserJet M404dn", "eatTitle": "УСЛУГИ"}, "services")

    def test_official_goods_has_priority(self):
        item = {"name": "Ремонтный комплект", "eat": {"title": "ТОВАРЫ"}}
        self.assertEqual(classify_position(item)["position_kind"], "goods")

    def test_unknown_position_requires_manual_without_identifier(self):
        item = {"name": "В соответствии с техническим заданием", "model": "HP LaserJet M404dn"}
        self.assert_blocked(item, "uncertain")
        self.assertEqual(filter_purchase_v2(purchase([item]), LAW)["filter_result"], "manual_check")

    def test_standalone_repair_is_decisive_in_both_fields(self):
        decision = filter_purchase_v2(purchase([{"name": "Ремонт", "description": "Экскаватор ЭО-2621Е"}]), LAW)
        self.assertEqual(decision["filter_result"], "rejected")
        self.assertEqual(decision["procurement_kind"], "works")
        matches = [m for m in decision["contextual_exclusion_matches"] if m["matched_word"] == "ремонт"]
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(m["semantic_role"] == "service_or_work" and m["affects_decision"] for m in matches))

    def test_mixed_has_no_goods_majority_and_models_are_scoped(self):
        items = [{"name": "Поставка HP LaserJet M404dn"}, {"name": "Бумага"},
                 {"name": "Техническое обслуживание HP LaserJet M404dn"}]
        self.assertEqual(procurement_kind(purchase(items)), "mixed")
        audit = audit_from_extraction({"raw": purchase(items)}, extraction())
        self.assertEqual(audit["items"][0]["model_search_mode"], "EXACT_MODEL")
        self.assertEqual(audit["items"][2]["position_kind"], "services")
        self.assertIsNone(audit["items"][2]["original_model"])
        raw = purchase([{**item, "quantity": 1, "unitPrice": 90000} for item in items])
        search = MagicMock(return_value={"offers": []})
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
                patch("pipeline.single_purchase_test.tender_folder", return_value=Path(directory)), \
                patch("pipeline.single_purchase_test.process_procurement_documents", return_value=extraction()), \
                patch("pipeline.single_purchase_test.build_passport"), \
                patch("pipeline.single_purchase_test.detect_brands", return_value={"status": "review_required"}), \
                patch("pipeline.single_purchase_test.save_brand_audit"):
            result = run_check(raw["id"], journal=MemoryJournal(), card_loader=lambda _: {"raw": raw},
                               price_search=search, supplier_verifier=MagicMock(),
                               output_dir=Path(directory), mode="UNIT — локальная проверка")
        self.assertNotEqual(result["status"], "RUN_ERROR")
        search.assert_called_once()
        self.assertEqual(search.call_args.args[0], "HP LaserJet M404dn")
        self.assertIsNone(result["positions"][2]["model"])

    def test_unknown_member_prevents_automatic_purchase_pass(self):
        decision = filter_purchase_v2(purchase([{"name": "Принтер"}, {}]), LAW)
        self.assertEqual(decision["filter_result"], "manual_check")

    def test_direct_identifier_cannot_bypass_type_with_stale_exact_result(self):
        stale = {"model_search_mode": "EXACT_MODEL", "original_model": "ОР 6508", "model_source": "CUSTOMER_SPECIFICATION"}
        item = repair_position()
        readiness = classify_price_search_readiness(item, stale)
        self.assertFalse(readiness["price_search_ready"])
        self.assertIsNone(readiness["identifier"])
        self.assertEqual(_classify_model(stale, item)["route"], "blocked")

    def test_no_service_model_from_technical_document(self):
        item = {"name": "Диагностика кондиционера"}
        doc = {"document_type": ["technical_specification"], "text": "Ballu BSL-09HN1"}
        self.assertIsNone(resolve_exact_model(item, [doc]))
        self.assertEqual(resolve_item_sources(item, [doc])["model_evidence"], [])

    def test_no_discovery_network_for_non_goods(self):
        provider = MagicMock()
        result = discover_models({"name": "Ремонт HP LaserJet M404dn", "requirements": [{"parameter": "Мощность", "value": "100 Вт"}]}, provider=provider)
        self.assertEqual(result["live_queries_count"], 0)
        provider.search.assert_not_called()

    def test_direct_batch_search_entry_points_are_blocked(self):
        item = {**repair_position(), "model": {"model": "ОР 6508", "mode": "EXACT_MODEL_ONLY"}}
        search = MagicMock()
        with self.assertRaises(ValueError):
            _customer_exact_supplier_result(item, {}, price_search=search, verifier=MagicMock())
        with patch("pipeline.mvp_exact_batch.search_exact_model_prices", search), self.assertRaises(ValueError):
            _process_suppliers(item, {})
        search.assert_not_called()

    def test_target_purchase_stops_before_price_search(self):
        raw = purchase([repair_position()])
        card = {"raw": {"id": raw["id"], "tradeNumber": raw["tradeNumber"], "purchaseTypeTitle": LAW, "lot": raw}}
        search, discovery = MagicMock(), MagicMock()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
                patch("pipeline.single_purchase_test.process_procurement_documents") as process:
            result = run_check(raw["id"], journal=MemoryJournal(), card_loader=lambda _: card,
                               price_search=search, model_discovery=discovery, exact_price_search=search,
                               output_dir=Path(directory), mode="UNIT — локальная проверка")
        self.assertEqual(result["filter"]["procurement_kind"], "works")
        self.assertEqual(result["filter"]["filter_result"], "rejected")
        self.assertIn("procurement_kind_works", result["filter"]["rejection_reasons"])
        self.assertFalse(any(e["stage"] == STAGES[9] and e["status"] == "ВЫПОЛНЕНО" for e in result["events"]))
        search.assert_not_called()
        discovery.assert_not_called()
        process.assert_not_called()

    def test_target_purchase_is_not_exported_to_active(self):
        raw = purchase([repair_position()])
        decision = filter_purchase_v2(raw, LAW)
        record = {**decision, "raw": raw, "normalized": {"number": raw["tradeNumber"]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            for name, records in (("eat_filtered_semantic_v2.json", [record]), ("eat_enrichment_test.json", []),
                                  ("eat_traceability_200_test.json", []), ("eat_confidential_links.json", [])):
                (root / "data" / name).write_text(json.dumps({"purchases": records}), encoding="utf-8")
            with patch.object(workbook, "ROOT", root):
                payload = workbook.build()
        self.assertEqual(len(payload[workbook.ACTIVE]), 1)

    def test_goods_ignores_repair_in_contract_terms(self):
        item = {"name": "Поставка принтера HP LaserJet M404dn"}
        raw = {**purchase([item]), "additionalConditions": "Гарантийный ремонт оборудования"}
        self.assertEqual(filter_purchase_v2(raw, LAW)["filter_result"], "passed")
        raw = purchase([{"name": "Набор инструментов для ремонта экскаватора"}])
        self.assertEqual(filter_purchase_v2(raw, LAW)["filter_result"], "passed")

    def test_recovery_and_work_performance_are_works(self):
        for name in ("Восстановление экскаватора", "Выполнение работ по восстановлению оборудования"):
            self.assert_blocked({"name": name}, "works")

    def test_uncertain_pipeline_never_calls_product_search(self):
        raw = purchase([{"name": "В соответствии с техническим заданием", "model": "HP M404dn"}])
        search = MagicMock()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = run_check(raw["id"], journal=MemoryJournal(), card_loader=lambda _: {"raw": raw},
                               price_search=search, exact_price_search=search, model_discovery=search,
                               output_dir=Path(directory), mode="UNIT — локальная проверка")
        self.assertEqual(result["filter"]["filter_result"], "manual_check")
        self.assertEqual(result["status"], "POSITION_KIND_BLOCKED")
        search.assert_not_called()
