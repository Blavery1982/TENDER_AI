"""Назначение ремонтного комплекта не превращает товар в закупку работ."""
import unittest

from filters.position_kind import classify_position
from filters.semantic_bad_words import filter_purchase_v2
from documents.item_sources import resolve_item_sources
from model_search.exact_model import resolve_exact_model
from model_search.price_readiness import extract_direct_identifier, classify_price_search_readiness

LAW = "Закупка по Закону №44-ФЗ"


def purchase(item):
    return {"subject": item["name"], "price": 270000, "lotItems": [item],
            "deliveryInfos": [{"deliveryAddress": {"regionName": "Московская область"}}]}


class RepairGoodsContextTests(unittest.TestCase):
    def assert_kit_is_goods(self, name):
        for category in (None, "ТОВАРЫ"):
            with self.subTest(name=name, category=category):
                item = {"name": name}
                if category:
                    item["eat"] = {"title": category}
                decision = filter_purchase_v2(purchase(item), LAW)
                self.assertEqual(classify_position(item)["position_kind"], "goods")
                self.assertEqual(decision["filter_result"], "passed")
                matches = [m for m in decision["contextual_exclusion_matches"] if m["matched_word"] == "ремонт"]
                self.assertTrue(matches)
                self.assertTrue(all(m["semantic_role"] == "product_property" and not m["affects_decision"] for m in matches))
                resolved = resolve_exact_model(item, [])
                self.assertEqual(resolved["original_model"], "Acme RK-100")
                self.assertTrue(classify_price_search_readiness(item, resolved)["price_search_ready"])

    def test_repair_adjective_kit_is_goods(self):
        self.assert_kit_is_goods("Ремонтный комплект Acme RK-100 для экскаватора")

    def test_kit_for_repair_is_goods(self):
        self.assert_kit_is_goods("Комплект для ремонта экскаватора Acme RK-100")

    def assert_work_is_blocked(self, item):
        decision = filter_purchase_v2(purchase(item), LAW)
        self.assertEqual(decision["procurement_kind"], "works")
        self.assertEqual(decision["filter_result"], "rejected")
        self.assertIsNone(resolve_exact_model(item, []))
        self.assertIsNone(extract_direct_identifier(item))
        readiness = classify_price_search_readiness(item, resolve_item_sources(item, []))
        self.assertFalse(readiness["price_search_ready"])
        self.assertIsNone(readiness["identifier"])

    def test_standalone_repair_remains_work(self):
        self.assert_work_is_blocked({"name": "Ремонт экскаватора ЭО-2621Е"})

    def test_official_works_still_has_priority_over_kit_wording(self):
        self.assert_work_is_blocked({"name": "Ремонтный комплект Acme RK-100", "eat": {"title": "РАБОТЫ"}})

    def test_original_repair_position_remains_blocked(self):
        self.assert_work_is_blocked({"name": "Ремонт", "description": "Экскаватора-бульдозера марки ЭО-2621Е ОР 6508",
                                     "eat": {"title": "РАБОТЫ"}})
