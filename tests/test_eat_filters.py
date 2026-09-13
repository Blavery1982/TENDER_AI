import unittest

from filters.eat_filters import filter_purchase, load_config, normalize_region
from filters.semantic_bad_words import filter_purchase_v2


class EatFiltersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()

    def purchase(self, **changes):
        raw = {
            "id": "1",
            "subject": "Поставка офисной бумаги",
            "price": 200000,
            "lotItems": [{"name": "Бумага", "description": "Белая бумага"}],
            "deliveryInfos": [{"deliveryAddress": {"regionName": "г Москва"}}],
        }
        raw.update(changes)
        return raw

    def test_passed(self):
        result = filter_purchase(self.purchase(), "Закупка по Закону №44-ФЗ", self.config)
        self.assertEqual(result["filter_result"], "passed")

    def test_all_reasons_are_collected(self):
        raw = self.purchase(price=100, subject="Ремонт за счет субсидии", lotItems=[{}] * 16)
        result = filter_purchase(raw, "Закупка по 223-ФЗ", self.config)
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("price_below_min", result["rejection_reasons"])
        self.assertIn("not_44fz", result["rejection_reasons"])
        self.assertNotIn("contains_subsidy", result["rejection_reasons"])
        self.assertEqual(result["special_conditions"], "СУБСИДИИ")
        self.assertIn("too_many_items", result["rejection_reasons"])
        self.assertIn("bad_word: ремонт", result["rejection_reasons"])

    def test_unknown_law_and_region_are_manual(self):
        raw = self.purchase(deliveryInfos=[{"deliveryAddress": {"regionName": "неясный формат"}}])
        result = filter_purchase(raw, None, self.config)
        self.assertEqual(result["filter_result"], "manual_check")
        self.assertIn("law_not_determined", result["rejection_reasons"])
        self.assertIn("region_not_determined", result["rejection_reasons"])

    def test_explicit_forbidden_region(self):
        raw = self.purchase(deliveryInfos=[{"deliveryAddress": {"regionName": "Красноярский край"}}])
        result = filter_purchase(raw, "44-ФЗ", self.config)
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_region_aliases_are_exact(self):
        self.assertEqual(normalize_region("город Москва", self.config), "г. Москва")
        self.assertIsNone(normalize_region("Московский район", self.config))

    def test_bad_words_do_not_search_customer(self):
        raw = self.purchase(organizerInfo={"name": "Организация ремонта"})
        result = filter_purchase(raw, "44-ФЗ", self.config)
        self.assertEqual(result["matched_bad_words"], [])

    def test_confidential_purchase_gets_separate_status(self):
        raw = self.purchase(
            tradeNumber=None,
            subject=None,
            price=None,
            lotItems=[],
            deliveryInfos=[],
            isTradeInfoHiddenByPrivacyAgreement=True,
            hideDetailsForUnauthorized=True,
        )
        result = filter_purchase(raw, "44-ФЗ", self.config)
        self.assertEqual(result["filter_result"], "confidential_locked")
        self.assertEqual(
            result["rejection_reasons"],
            ["confidentiality_agreement_required"],
        )

    def delivery(self, region, city=None, full=None):
        address = {"regionName": region}
        if city is not None:
            address["city"] = city
        if full is not None:
            address["formattedFullInfo"] = full
        return [{"deliveryAddress": address}]

    def production_result(self, region, city=None, full=None):
        raw = self.purchase(deliveryInfos=self.delivery(region, city, full))
        return filter_purchase_v2(raw, "Закупка по Закону №44-ФЗ", self.config)

    def test_stavropol_is_allowed(self):
        result = self.production_result(
            "Ставропольский край", "г Пятигорск",
            "Ставропольский край, г Пятигорск, ул Кузнечная, д 10",
        )
        self.assertEqual(result["filter_result"], "passed")
        self.assertEqual(result["normalized_regions"], ["Ставропольский край"])

    def test_saint_petersburg_is_forbidden(self):
        result = self.production_result(
            "г Санкт-Петербург", "г Санкт-Петербург",
            "г Санкт-Петербург, Невский проспект, д 1",
        )
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_leningrad_region_is_forbidden(self):
        result = self.production_result(
            "Ленинградская обл", "г Выборг",
            "Ленинградская область, г Выборг, ул Ленина, д 1",
        )
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_vladikavkaz_is_conditionally_allowed(self):
        result = self.production_result(
            "Респ Северная Осетия - Алания", "г Владикавказ",
            "г Владикавказ, ул Титова, д 11",
        )
        self.assertEqual(result["filter_result"], "passed")
        self.assertEqual(result["normalized_regions"], ["г. Владикавказ"])

    def test_vladikavkaz_in_full_delivery_address_is_conditionally_allowed(self):
        result = self.production_result(
            "Республика Северная Осетия-Алания", None,
            "362019, Республика Северная Осетия-Алания, г. Владикавказ, ул Пушкинская, д 40",
        )
        self.assertEqual(result["filter_result"], "passed")
        self.assertEqual(result["normalized_regions"], ["г. Владикавказ"])

    def test_other_north_ossetia_city_is_forbidden(self):
        result = self.production_result(
            "Респ Северная Осетия - Алания", "г Беслан",
            "Респ Северная Осетия - Алания, г Беслан, ул Фриева, д 139а",
        )
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_north_ossetia_without_city_is_forbidden(self):
        result = self.production_result(
            "Республика Северная Осетия - Алания", None,
            "Республика Северная Осетия - Алания",
        )
        self.assertEqual(result["filter_result"], "rejected")
        self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_other_north_caucasus_regions_are_forbidden(self):
        regions = {
            "Респ Дагестан": "г Махачкала",
            "РЕСПУБЛИКА ИНГУШЕТИЯ": "г Назрань",
            "Кабардино-Балкарская Респ": "г Нальчик",
            "Карачаево-Черкесская Респ": "г Черкесск",
            "Чеченская респ": "г Грозный",
        }
        for region, city in regions.items():
            with self.subTest(region=region):
                result = self.production_result(region, city, f"{region}, {city}")
                self.assertEqual(result["filter_result"], "rejected")
                self.assertIn("region_not_allowed", result["rejection_reasons"])

    def test_subsidy_remains_special_condition_not_rejection(self):
        raw = self.purchase(subject="Поставка бумаги за счёт субсидии")
        result = filter_purchase_v2(raw, "Закупка по Закону №44-ФЗ", self.config)
        self.assertEqual(result["filter_result"], "passed")
        self.assertEqual(result["special_conditions"], "СУБСИДИИ")


if __name__ == "__main__":
    unittest.main()
