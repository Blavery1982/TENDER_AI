import unittest
from unittest.mock import Mock

from suppliers.economic_precheck import (current_analysis_recommendation,
                                          evaluate_price_request_candidate,
                                          economic_precheck,
                                          price_request_candidates)
from pipeline.live_e2e import _apply_supplier_verification


def offer(price, availability="in_stock", name="Магазин"):
    return {"exact_model": True, "public_price": price,
            "availability": availability, "supplier_name": name}


class EconomicPrecheckTests(unittest.TestCase):
    def test_uses_total_quantity_and_configured_commission(self):
        result = economic_precheck(200_000, 0.04, [
            {"quantity": 4, "offers": [offer(45_000)]}
        ])
        self.assertEqual(result["commission"], 8_000)
        self.assertEqual(result["maximum_purchase_cost"], 157_440)
        self.assertEqual(result["minimum_purchase_cost"], 180_000)
        self.assertFalse(result["passes"])

    def test_passing_offer_requires_supplier_verification(self):
        result = economic_precheck(200_000, 0.03, [
            {"quantity": 2, "offers": [offer(70_000)]}
        ])
        self.assertTrue(result["passes"])
        self.assertTrue(result["supplier_verification_required"])

    def test_unavailable_price_cannot_pass(self):
        result = economic_precheck(200_000, 0.03, [
            {"quantity": 2, "offers": [offer(10_000, "out_of_stock")]}
        ])
        self.assertEqual(result["status"], "insufficient_data")

    def test_dead_listings_are_not_call_candidates(self):
        rows = price_request_candidates([
            offer(None, "out_of_stock", "Нет товара"),
            offer(None, "unknown", "Позвонить"),
            offer(None, "archive", "Архив"),
        ])
        self.assertEqual([row["supplier_name"] for row in rows], ["Позвонить"])

    def test_missing_or_unconfirmed_price_is_call_candidate(self):
        rows = price_request_candidates([
            {**offer(None, "unknown", "Без цены"), "product_page_available": True},
            {**offer(90_000, "in_stock", "Старая цена"), "stale_price": True},
        ], 50_000)
        self.assertEqual({row["supplier_name"] for row in rows},
                         {"Без цены", "Старая цена"})

    def test_required_discount_up_to_twenty_percent_is_allowed(self):
        assessed = evaluate_price_request_candidate(offer(50_000), 40_000)
        self.assertTrue(assessed["call_candidate"])
        self.assertEqual(assessed["required_discount_percent"], 20.0)

    def test_required_discount_above_twenty_percent_is_excluded(self):
        assessed = evaluate_price_request_candidate(offer(50_001), 40_000)
        self.assertFalse(assessed["call_candidate"])
        self.assertGreater(assessed["required_discount_percent"], 20.0)

    def test_unavailable_is_excluded_even_without_price(self):
        for status in ("out_of_stock", "продажи прекращены", "снят с продажи",
                       "архив", "товар закончился"):
            with self.subTest(status=status):
                assessed = evaluate_price_request_candidate(offer(None, status), 40_000)
                self.assertFalse(assessed["call_candidate"])

    def test_needs_price_recommendation_contains_full_and_unit_limit(self):
        check = economic_precheck(200_000, 0.03, [
            {"quantity": 4, "offers": [offer(50_000, name="KNS")]}
        ])
        text = current_analysis_recommendation(check, [offer(50_000, name="KNS")],
                                               [{"quantity": 4}])
        self.assertIn("📞 НУЖНА ЦЕНА", text)
        self.assertIn("KNS", text)
        self.assertIn("не выше 39 770,00 ₽/шт.", text)

    def test_failed_precheck_skips_supplier_verifier(self):
        verifier = Mock(side_effect=AssertionError("verification must not run"))
        rows = _apply_supplier_verification([offer(50_000)], False, verifier)
        verifier.assert_not_called()
        self.assertTrue(rows[0]["verification_skipped"])

    def test_passed_precheck_runs_supplier_verifier(self):
        verifier = Mock(return_value={**offer(50_000), "verification_status": "checked"})
        rows = _apply_supplier_verification([offer(50_000)], True, verifier)
        verifier.assert_called_once()
        self.assertEqual(rows[0]["verification_status"], "checked")


if __name__ == "__main__":
    unittest.main()
