import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from model_search.search_mode import (
    CUSTOMER_EXACT_MODEL_RU, customer_exact_model_policy,
    determine_model_search_mode, EXACT_MODEL_ONLY, EXACT_MODEL_OR_EQUIVALENT,
    MODEL_DISCOVERY_REQUIRED, MODEL_MODE_REVIEW_REQUIRED,
)
from model_search.live_discovery import discover_models
from pipeline.batch_orchestrator import process_item


class ModelSearchModeTests(unittest.TestCase):
    def mode(self, text):
        return determine_model_search_mode({"description": text})["model_search_mode"]

    def test_exact_model(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN"), EXACT_MODEL_ONLY)

    def test_equivalent(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN или эквивалент"), EXACT_MODEL_OR_EQUIVALENT)

    def test_characteristics(self):
        self.assertEqual(self.mode("Кондиционер, мощность 2.8 кВт, цвет белый"), MODEL_DISCOVERY_REQUIRED)

    def test_codes_not_models(self):
        for text in ("Артикул RC-TWN28HN", "Код позиции AB-123", "ОКПД2 28.25.12.130", "Номер закупки 100205573126100053", "Внутренний код ABC-123"):
            with self.subTest(text=text):
                self.assertEqual(self.mode(text), MODEL_DISCOVERY_REQUIRED)

    def test_uncertain(self):
        self.assertEqual(self.mode("Желательно Royal Clima RC-TWN28HN, замена по согласованию"), MODEL_MODE_REVIEW_REQUIRED)

    def test_unidentified_token_review(self):
        self.assertEqual(self.mode("Кондиционер ABC-123"), MODEL_MODE_REVIEW_REQUIRED)

    def test_explicit_denial(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN, аналоги не допускаются"), EXACT_MODEL_ONLY)

    def test_denial_before_permission_word(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN, не допускаются аналоги"), EXACT_MODEL_ONLY)

    def test_analog_technical_adjective_is_not_permission(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN, аналоговый вход"), EXACT_MODEL_ONLY)

    def test_conflicting_permission_review(self):
        self.assertEqual(self.mode("Royal Clima RC-TWN28HN или аналог; замена запрещена"), MODEL_MODE_REVIEW_REQUIRED)

    def test_structured_model(self):
        result = determine_model_search_mode({"requirements": [{"parameter": "Модель", "required_value": "RC-TWN28HN"}]})
        self.assertEqual(result["model_search_mode"], EXACT_MODEL_ONLY)

    def test_justification_not_customer_model(self):
        result = determine_model_search_mode({"name": "Кондиционер", "model_from_justification": "Royal Clima RC-TWN28HN"})
        self.assertEqual(result["model_search_mode"], MODEL_DISCOVERY_REQUIRED)

    def test_direct_discovery_blocked_before_cache_and_provider(self):
        for text in ("Royal Clima RC-TWN28HN", "Возможно Royal Clima RC-TWN28HN"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                provider = Mock()
                with patch("model_search.live_discovery._cache_key", side_effect=AssertionError("cache reached")):
                    result = discover_models({"description": text}, provider=provider, cache_dir=Path(directory))
                provider.search.assert_not_called()
                provider.fetch.assert_not_called()
                self.assertEqual(result["live_queries_count"], 0)
                self.assertIsNone(result["selected_model"])
                self.assertEqual(result["candidates"][0]["exact_model"], "Royal Clima RC-TWN28HN")

    @patch("pipeline.batch_orchestrator.discover_models")
    def test_per_item_pipeline_gate(self, discovery):
        discovery.return_value = {"selected_model": None}
        modes = []
        for number, text in enumerate(("Royal Clima RC-TWN28HN", "Royal Clima RC-TWN28HN или эквивалент", "Мощность 2.8 кВт", "Возможно Royal Clima RC-TWN28HN"), 1):
            result = process_item({"name": "Кондиционер", "description": text}, number, "test", {}, model_live=True)
            modes.append(result["model_search"]["model_search_mode"])
        self.assertEqual(modes, [EXACT_MODEL_ONLY, EXACT_MODEL_OR_EQUIVALENT, MODEL_DISCOVERY_REQUIRED, MODEL_MODE_REVIEW_REQUIRED])
        # Только позиция без модели идёт в discovery. Точная модель сразу идёт
        # в price search, неоднозначный identifier остаётся на review.
        self.assertEqual(discovery.call_count, 1)

    def test_equivalent_preserves_original_and_searches(self):
        provider = Mock()
        provider.search.return_value = []
        with tempfile.TemporaryDirectory() as directory, patch("model_search.live_discovery.time.sleep"):
            result = discover_models({"name": "Royal Clima RC-TWN28HN или аналог"}, provider=provider, cache_dir=Path(directory))
        provider.search.assert_called()
        self.assertEqual(result["original_model"], "Royal Clima RC-TWN28HN")
        self.assertEqual(result["candidates"][0]["candidate_source"], "customer_specification")

    def test_exact_customer_model_skips_characteristic_compliance_after_exact_match(self):
        decision = determine_model_search_mode({"description": "CyberPower PR1500ELCD"})
        result = customer_exact_model_policy(decision, exact_model_match=True)
        self.assertFalse(result["compliance_required"])
        self.assertEqual(result["compliance_status"], CUSTOMER_EXACT_MODEL_RU)

    def test_exact_customer_model_without_seller_match_still_requires_compliance(self):
        decision = determine_model_search_mode({"description": "CyberPower PR1500ELCD"})
        self.assertTrue(customer_exact_model_policy(decision, exact_model_match=False)["compliance_required"])

    def test_equivalent_always_requires_compliance(self):
        decision = determine_model_search_mode({"description": "CyberPower PR1500ELCD или эквивалент"})
        self.assertTrue(customer_exact_model_policy(decision, exact_model_match=True)["compliance_required"])

    def test_ambiguous_identity_always_requires_compliance(self):
        decision = determine_model_search_mode({"description": "Возможно CyberPower PR1500ELCD"})
        self.assertTrue(customer_exact_model_policy(decision, exact_model_match=True)["compliance_required"])

    def test_resolved_exact_model_is_not_blocked_by_numbers_in_specification(self):
        decision = {
            "source_resolution_version": 2,
            "model_search_mode": MODEL_MODE_REVIEW_REQUIRED,
            "customer_required_model": "CyberPower PR1500ELCD",
            "model_evidence": [{"evidence": "ИБП CyberPower PR1500ELCD"}],
            "source_warnings": [], "source_conflicts": [],
            "customer_specification_text": "Мощность: 1350 Вт\nИБП CyberPower PR1500ELCD",
        }
        self.assertFalse(customer_exact_model_policy(decision, exact_model_match=True)["compliance_required"])

    def test_resolved_but_uncertain_identity_does_not_use_fast_path(self):
        decision = {
            "source_resolution_version": 2,
            "model_search_mode": MODEL_MODE_REVIEW_REQUIRED,
            "customer_required_model": "CyberPower PR1500ELCD",
            "model_evidence": [{"evidence": "Возможно CyberPower PR1500ELCD"}],
            "source_warnings": [], "source_conflicts": [],
            "customer_specification_text": "Возможно CyberPower PR1500ELCD",
        }
        self.assertTrue(customer_exact_model_policy(decision, exact_model_match=True)["compliance_required"])
