import unittest
from pathlib import Path

from documents.procurement_audit import (
    classify_supplier_search_readiness,
    customer_document_issue,
    classify_document,
    evaluate_candidate_model,
    extract_document,
    run_test,
)
from documents.tv_procurement_audit import run_test as run_tv_audit
from documents.text_extraction import ambiguity_variants, identify_model_from_catalog, text_layer_quality


ROOT = Path(__file__).resolve().parent.parent


class ProcurementAuditTest(unittest.TestCase):
    def test_saved_xlsx_is_price_justification_only(self):
        path = ROOT / "data/contracts/100309200126100271/Приложение _2 обоснование НМЦК.xlsx"
        text, status, rows = extract_document(path)
        self.assertEqual(status, "analyzed")
        self.assertTrue(rows)
        self.assertEqual(classify_document(path.name, text), ["price_justification"])

    def test_single_saved_procurement_audit_is_conservative(self):
        result = run_test()
        self.assertEqual(result["tradeNumber"], "100309200126100271")
        self.assertEqual(result["tz_audit_status"], "manual_check")
        self.assertEqual(result["price_justification_status"], "model_not_identified")
        self.assertEqual(result["detected_mismatches"], [])
        self.assertEqual(result["pre_supplier_check_status"], "ready_with_attention")
        self.assertEqual(result["model_search_status"], "external_model_search_required")
        self.assertEqual([x["target_price_80_percent"] for x in result["position_assessments"]], [8960.0, 9280.0])
        self.assertTrue(all(not x["candidate_models"] for x in result["position_assessments"]))

    def test_24_of_25_confirmed_is_not_fully_compliant(self):
        requirements = [{"parameter": f"p{i}", "required_value": str(i)} for i in range(25)]
        checks = [{"parameter": f"p{i}", "model_value": str(i), "result": "complies", "source": "источник"} for i in range(24)]
        result = evaluate_candidate_model(
            requirements,
            {"brand": "A", "model": "B", "market_price": 10, "market_price_source": "источник", "parameter_check": checks},
            20,
        )
        self.assertEqual(result["technical_status"], "not_confirmed")
        self.assertEqual(result["unconfirmed_parameters"], ["p24"])

    def test_one_proven_mismatch_means_non_compliant(self):
        requirements = [{"parameter": "размер", "required_value": "43"}]
        candidate = {"parameter_check": [{"parameter": "размер", "model_value": "32", "result": "does_not_comply", "source": "паспорт"}]}
        self.assertEqual(evaluate_candidate_model(requirements, candidate, 100)["technical_status"], "non_compliant")

    def test_price_needs_source_and_threshold(self):
        requirements = [{"parameter": "материал", "required_value": "PTFE"}]
        checks = [{"parameter": "материал", "model_value": "PTFE", "result": "complies", "source": "паспорт"}]
        result = evaluate_candidate_model(
            requirements,
            {"market_price": 80, "market_price_source": "прайс", "parameter_check": checks},
            80,
        )
        self.assertEqual(result["technical_status"], "fully_compliant")
        self.assertEqual(result["price_check_status"], "passes_20_percent_threshold")

    def test_quote_model_mismatch_is_informational(self):
        issue = customer_document_issue(
            "price_justification_model_mismatch", 1, "Модель не соответствует", "ТЗ и КП", "high"
        )
        decision = classify_supplier_search_readiness([issue], [{"candidate_models": []}])
        self.assertTrue(decision["automatic_supplier_search_allowed"])
        self.assertEqual(
            decision["supplier_search_status_ru"],
            "ПРОДОЛЖИТЬ ПОИСК МОДЕЛЕЙ",
        )

    def test_tv_documents_audit_detects_expected_problems(self):
        result = run_tv_audit()
        types = [x["issue_type"] for x in result["customer_document_issues"]]
        self.assertEqual(types.count("price_justification_model_mismatch"), 2)
        self.assertIn("quantity_mismatch", types)
        self.assertIn("nmck_not_supported", types)
        self.assertNotIn("arithmetic_mismatch", types)
        self.assertEqual(result["supplier_search_status"], "continue_model_search")

    def test_dangerous_ocr_substitutions_are_only_suggestions(self):
        self.assertIn("GE32LFN0", ambiguity_variants("GE32LFNO"))
        self.assertIn("43UCY3", ambiguity_variants("A3UCY3"))
        self.assertIn("≥", ambiguity_variants(">"))
        self.assertIn("≤", ambiguity_variants("<"))
        self.assertNotEqual("GE32LFNO", "GE32LFN0")

    def test_empty_scan_layer_requires_ocr(self):
        usable, _ = text_layer_quality("")
        self.assertFalse(usable)

    def test_model_identification_preserves_original_ocr(self):
        extraction = {"critical_values": [{"value_type": "model_or_article", "recognized_value": "GE32LFNO", "confidence": 90.49}]}
        result = identify_model_from_catalog(extraction, "GE32LFN0", "официальный каталог")
        self.assertEqual(result["source_ocr_value"], "GE32LFNO")
        self.assertEqual(result["identified_model"], "GE32LFN0")


if __name__ == "__main__":
    unittest.main()
