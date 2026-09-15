import unittest

from model_search.price_readiness import (
    MODEL_DISCOVERY_REQUIRED,
    MODEL_IDENTIFIER_REVIEW_REQUIRED,
    PRICE_SEARCH_READY,
    classify_price_search_readiness,
    extract_direct_identifier,
)
from model_search.query_planning import generate_queries
from pipeline.mvp_exact_batch import _classify_model
from model_search.product_evidence import page_matches


class PriceReadinessTests(unittest.TestCase):
    def classify(self, item=None, resolved=None):
        return classify_price_search_readiness({"name": "Товарная позиция", **(item or {})}, resolved or {})

    def test_model_in_eat_specification_is_ready(self):
        result = self.classify({"model": "RC-TWN28HN"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["model_source"], "CUSTOMER_SPECIFICATION")

    def test_model_in_expanded_offer_description_is_ready(self):
        result = self.classify({"offer_description": "Кондиционер Royal Clima RC-TWN28HN"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["identifier"], "RC-TWN28HN")

    def test_model_in_contract_appendix_is_ready(self):
        result = self.classify({}, {"customer_required_model": "UE32T5300",
                                    "model_source": "CONTRACT_DOCUMENT"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["model_source"], "CONTRACT_DOCUMENT")

    def test_model_only_in_price_justification_is_ready(self):
        result = self.classify({}, {"price_justification_model": "M283fdn"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["model_source"], "PRICE_JUSTIFICATION")

    def test_price_model_does_not_become_customer_model(self):
        result = self.classify({}, {"price_justification_model": "M283fdn"})
        self.assertIsNone(result["customer_required_model"])
        self.assertEqual(result["price_justification_model"], "M283fdn")

    def test_procurement_model_never_requires_compliance_before_price(self):
        result = self.classify({"model": "4303dw"})
        self.assertFalse(result["compliance_before_price_search"])

    def test_exact_model_only_is_passed_directly_to_price_search(self):
        result = _classify_model({"customer_required_model": "RC-TWN28HN",
                                  "model_search_mode": "EXACT_MODEL_ONLY"}, {"name": "Товарная позиция"})
        self.assertEqual((result["route"], result["model"], result["mode"]),
                         ("exact", "RC-TWN28HN", "EXACT_MODEL_ONLY"))

    def test_exact_or_equivalent_searches_original_first(self):
        result = _classify_model({"customer_required_model": "RC-TWN28HN",
                                  "model_search_mode": "EXACT_MODEL_OR_EQUIVALENT"}, {"name": "Товарная позиция"})
        self.assertEqual((result["route"], result["model"], result["mode"]),
                         ("exact", "RC-TWN28HN", "EXACT_MODEL_OR_EQUIVALENT"))

    def test_characteristics_without_model_need_discovery(self):
        result = self.classify({"description": "Мощность 900 Вт; напряжение 220 В"})
        self.assertEqual(result["classification"], MODEL_DISCOVERY_REQUIRED)

    def test_faston_f2_property_is_not_ready(self):
        result = self.classify(
            {"description": "Тип клеммы: FASTON F2"},
            {"customer_required_model": "FASTON F2", "model_source": "CUSTOMER_SPECIFICATION"},
        )
        self.assertEqual(result["classification"], MODEL_IDENTIFIER_REVIEW_REQUIRED)

    def test_incomplete_abat_identifier_needs_review(self):
        result = self.classify({}, {"customer_required_model": "Abat ШХн-0",
                                    "model_source": "CONTRACT_DOCUMENT"})
        self.assertEqual(result["classification"], MODEL_IDENTIFIER_REVIEW_REQUIRED)

    def test_rc_twn28hn_is_ready(self):
        self.assertEqual(self.classify({"description": "RC-TWN28HN"})["classification"],
                         PRICE_SEARCH_READY)

    def test_ue32t5300_is_ready(self):
        self.assertEqual(self.classify({"description": "Телевизор Samsung UE32T5300"})["classification"],
                         PRICE_SEARCH_READY)

    def test_commercial_product_name_from_eat_description_is_ready(self):
        result = self.classify({"description": "Перфит / PERFIT Light Body Асиликон 2х50мл"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["identifier"], "PERFIT Light Body")

    def test_uppercase_series_without_article_needs_review(self):
        result = self.classify({"description": "Абатмент UCLA CCM под литье"})
        self.assertEqual(result["classification"], MODEL_IDENTIFIER_REVIEW_REQUIRED)

    def test_named_model_must_match_seller_heading_exactly(self):
        page = {"content_kind": "product_page", "heading": "Материал PERFIT Light Body 2x50 мл",
                "title": "", "url": "https://shop.ru/item", "text": ""}
        self.assertTrue(page_matches(page, "PERFIT Light Body"))
        self.assertFalse(page_matches(page, "PERFIT Putty"))

    def test_classic_sku_still_rejects_similar_seller_model(self):
        page = {"content_kind": "product_page", "heading": "Royal Clima RC-TWN28HN-A",
                "title": "", "url": "https://shop.ru/item", "text": ""}
        self.assertFalse(page_matches(page, "RC-TWN28HN"))

    def test_model_in_item_name_wins_over_connector_type_in_description(self):
        result = self.classify({"name": "Батарея BB Battery BP 5-12 (или аналог)",
                                "description": "Тип клеммы: FASTON F2"})
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["identifier"], "BB Battery BP 5-12")

    def test_decimal_model_from_document_is_not_silently_truncated(self):
        result = self.classify({}, {
            "customer_required_model": "Abat ШХн-0",
            "model_source": "CONTRACT_DOCUMENT",
            "customer_specification_text": "Шкаф Abat ШХн-0,5 либо эквивалент",
            "model_evidence": [{"source_document": "проект ГК.docx", "source_page": 1,
                                "evidence": "Abat ШХн-0,5"}],
        })
        self.assertEqual(result["classification"], PRICE_SEARCH_READY)
        self.assertEqual(result["identifier"], "Abat ШХн-0,5")
        self.assertEqual(result["evidence"][0]["source_page"], 1)

    def test_commercial_offer_column_numbers_are_not_models(self):
        result = self.classify({}, {"document_model_candidates": [
            {"identifier": "Предложение 1", "ambiguous": True,
             "reason": "Обозначение является технической характеристикой товара",
             "model_source": "PRICE_JUSTIFICATION"},
            {"identifier": "Предложение 2", "ambiguous": True,
             "reason": "Обозначение является технической характеристикой товара",
             "model_source": "PRICE_JUSTIFICATION"}],
            "requirements": [{"parameter": "Мощность", "value": "2.6 кВт"}]})
        self.assertEqual(result["classification"], MODEL_IDENTIFIER_REVIEW_REQUIRED)

    def test_offer_heading_identifier_is_ambiguous(self):
        from model_search.price_readiness import identifier_is_ambiguous
        self.assertTrue(identifier_is_ambiguous("Предложение 1")[0])

    def test_packaged_customer_designation_preserves_brand_and_series(self):
        item = {"name": "Клей плиточный 25 кг",
                "description": "Клей для керамической плитки Bergauf Keramik PLUS 25 кг"}
        result = extract_direct_identifier(item)
        self.assertEqual(result["identifier"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(result["customer_model_raw"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(result["customer_model_evidence"]["source_field"], "position_text")

    def test_short_packaged_candidate_does_not_win_over_full_customer_span(self):
        result = self.classify({"name": "Клей плиточный 25 кг",
                                "description": "Клей для керамической плитки Bergauf Keramik PLUS 25 кг"})
        self.assertEqual(result["customer_required_model"], "Bergauf Keramik PLUS 25 кг")
        self.assertNotEqual(result["customer_required_model"], "PLUS 25")

    def test_package_size_is_separate_from_procurement_quantity(self):
        item = {"name": "Клей плиточный 25 кг",
                "description": "Клей для керамической плитки Bergauf Keramik PLUS 25 кг",
                "quantity": 400}
        result = self.classify(item)
        self.assertEqual(result["identifier"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(item["quantity"], 400)
        self.assertNotEqual(result["identifier"], str(item["quantity"]))

    def test_exact_query_starts_with_full_customer_designation(self):
        item = {"name": "Клей плиточный 25 кг",
                "description": "Клей для керамической плитки Bergauf Keramik PLUS 25 кг",
                "quantity": 400}
        readiness = self.classify(item)
        queries = generate_queries({**item, **readiness, "requirements": []})
        self.assertEqual(queries[0], '"Bergauf Keramik PLUS 25 кг" характеристики')
        self.assertNotIn("PLUS 25", queries[0].replace("Bergauf Keramik PLUS 25 кг", ""))

    def test_existing_short_alphanumeric_model_remains_unchanged(self):
        result = self.classify({"description": "Телевизор Samsung UE32T5300"})
        self.assertEqual(result["identifier"], "UE32T5300")
        self.assertEqual(result["customer_model_raw"], "UE32T5300")

    def test_price_justification_model_is_not_customer_raw_model(self):
        result = self.classify({}, {"price_justification_model": "M283fdn"})
        self.assertIsNone(result.get("customer_model_raw"))
        self.assertIsNone(result["customer_required_model"])

    def test_customer_model_extraction_is_position_local(self):
        first = extract_direct_identifier({"name": "Клей Bergauf Keramik PLUS 25 кг"})
        second = extract_direct_identifier({"name": "Телевизор Samsung UE32T5300"})
        self.assertEqual(first["identifier"], "Bergauf Keramik PLUS 25 кг")
        self.assertEqual(second["identifier"], "UE32T5300")
        self.assertNotIn("Bergauf", second["identifier"])


if __name__ == "__main__":
    unittest.main()
