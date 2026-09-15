from datetime import date, datetime
import unittest
from zoneinfo import ZoneInfo

from pipeline.mvp_exact_batch import (MAX_SUPPLIER_SEARCHES, _classify_model,
                                      _deadline_in_window, _preliminary_economics,
                                      _blocked_items,
                                      _sheet_write_with_timeout,
                                      apply_server_deadline_prefilter,
                                      deadline_window)
from unittest.mock import patch
import time
from tests.test_batch_orchestrator import fixture


MOSCOW = ZoneInfo("Europe/Moscow")


class MvpExactBatchTests(unittest.TestCase):
    def test_deadline_window_uses_two_workdays_after_weekend_and_holiday(self):
        now = datetime(2026, 9, 11, 18, 0, tzinfo=MOSCOW)
        lower, upper = deadline_window(now, {date(2026, 9, 14)})
        self.assertEqual(lower, now)
        self.assertEqual(upper.date(), date(2026, 9, 16))
        self.assertEqual((upper.hour, upper.minute), (23, 59))

    def test_local_deadline_filter_is_strict_on_both_boundaries(self):
        lower = datetime(2026, 9, 12, 10, 0, tzinfo=MOSCOW)
        upper = datetime(2026, 9, 15, 23, 59, 59, tzinfo=MOSCOW)
        self.assertFalse(_deadline_in_window({"applicationFillingEndDate": "2026-09-12T07:00:00"}, lower, upper))
        self.assertTrue(_deadline_in_window({"applicationFillingEndDate": "2026-09-12T07:00:01"}, lower, upper))
        self.assertFalse(_deadline_in_window({"applicationFillingEndDate": "2026-09-15T21:00:00"}, lower, upper))

    def test_server_prefilter_updates_dates_without_claiming_local_semantics(self):
        body = {"filter": {"applicationFillingStartDate": None,
                           "applicationFillingEndDate": None}, "page": 1, "size": 10}
        lower = datetime(2026, 9, 12, 10, 0, tzinfo=MOSCOW)
        upper = datetime(2026, 9, 15, 23, 59, 59, tzinfo=MOSCOW)
        result = apply_server_deadline_prefilter(body, lower, upper)
        self.assertEqual(result["filter"]["applicationFillingStartDate"], "2026-09-12T07:00:00")
        self.assertEqual(result["filter"]["applicationFillingEndDate"], "2026-09-15T20:59:59")
        self.assertIsNone(body["filter"]["applicationFillingStartDate"])

    def test_customer_model_routes_to_exact_without_warnings(self):
        result = _classify_model({"customer_required_model": "ABC-123", "model_search_mode": "EXACT_MODEL_ONLY",
                                  "model_source": "CUSTOMER_SPECIFICATION", "source_warnings": [], "source_conflicts": []}, {"name": "Товарная позиция"})
        self.assertEqual(result["route"], "exact")
        self.assertEqual(result["mode"], "EXACT_MODEL_ONLY")

    def test_price_justification_model_is_labeled_separately(self):
        result = _classify_model({"price_justification_model": "ABC-123", "model_search_mode": "MODEL_DISCOVERY_REQUIRED",
                                  "source_warnings": [], "source_conflicts": []}, {"name": "Товарная позиция"})
        self.assertEqual(result["route"], "exact")
        self.assertEqual(result["mode"], "PRICE_JUSTIFICATION_MODEL")
        self.assertEqual(result["status_ru"], "МОДЕЛЬ ИЗ ОБОСНОВАНИЯ ЦЕНЫ")

    def test_document_warning_does_not_block_direct_exact_model(self):
        result = _classify_model({"customer_required_model": "ABC-123", "model_search_mode": "MODEL_MODE_REVIEW_REQUIRED",
                                  "source_warnings": ["Неоднозначно"], "source_conflicts": []}, {"name": "Товарная позиция"})
        self.assertEqual(result["route"], "exact")

    def test_non_sku_product_property_is_not_sent_to_exact_price_search(self):
        result = _classify_model({"customer_required_model": "FASTON F2", "model_search_mode": "EXACT_MODEL_ONLY",
                                  "model_source": "CUSTOMER_SPECIFICATION", "source_warnings": [], "source_conflicts": []})
        self.assertEqual(result["route"], "manual_review")

    def test_no_model_goes_to_discovery_without_running_it(self):
        result = _classify_model({"model_search_mode": "MODEL_DISCOVERY_REQUIRED", "source_warnings": [], "source_conflicts": []}, {"name": "Товарная позиция"})
        self.assertEqual(result["route"], "discovery")
        self.assertEqual(result["status_ru"], "ТРЕБУЕТСЯ ПОДБОР МОДЕЛИ")

    def test_public_price_economics_never_becomes_purchase_price(self):
        result = _preliminary_economics({"quantity": 2, "customer_unit_price": 100_000, "customer_sum": 200_000},
                                        {"public_price": 70_000}, 6_000)
        self.assertEqual(result["public_total_for_quantity"], 140_000)
        self.assertIsNone(result["purchase_price"])
        self.assertIn("ПУБЛИЧНОЙ ЦЕНЕ", result["calculation_basis"])

    def test_supplier_search_cap_is_exactly_ten(self):
        self.assertEqual(MAX_SUPPLIER_SEARCHES, 10)

    def test_sheet_writer_timeout_is_finite(self):
        with patch("pipeline.mvp_exact_batch.SHEETS_WRITE_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(TimeoutError):
                _sheet_write_with_timeout(lambda payload: time.sleep(1), {})

    def test_blocked_card_materializes_rows_without_supplier_search(self):
        card = fixture(count=2)
        reason = "работа с закупкой НЕ автоматизирована - ошибка этапа card_documents - просчет делать в ручную!"
        items = _blocked_items(card, {"documents_found": 1, "documents_processed": 0}, reason, "card_documents")
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["processing_blocked"] and item["supplier_search"] is None for item in items))
