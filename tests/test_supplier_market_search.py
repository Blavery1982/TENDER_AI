import unittest

from suppliers.market_search import (PRICE_FIRE, PRICE_HIGH, PRICE_RESERVE,
                                     SUPPLIER_TARGET_DISCOUNT_PERCENT,
                                     build_call_lists, classify_source,
                                     deduplicate_offers, exact_model_match,
                                     normalize_product_url, prepare_offers,
                                     price_priority, supplier_target_price)
from suppliers.verification import HIGH_RISK, INSUFFICIENT, MANUAL, PASSED


def row(**kw):
    x={"model":"RC-TWN28HN","product_name":"Royal Clima RC-TWN28HN",
       "product_url":"https://shop.ru/item","domain":"shop.ru","public_price":25000,
       "purchase_price":None,"availability":"in_stock","verification_status":PASSED}
    x["exact_model"] = True
    x.update(kw); return x


class MarketSearchTests(unittest.TestCase):
    TARGET = 21_590
    def test_exact_model(self): self.assertTrue(exact_model_match("Royal Clima RC-TWN28HN", "RC-TWN28HN"))

    def test_manufacturer_model_allows_formatting_not_neighbor_model(self):
        self.assertTrue(exact_model_match('EACS-09HP/N3 Electrolux','Electrolux EACS-09HP/N3'))
        self.assertTrue(exact_model_match('Electrolux EACS 09HP/N3','EACS-09HP/N3'))
        self.assertFalse(exact_model_match('Electrolux EACS-12HP/N3','EACS-09HP/N3'))
        self.assertFalse(exact_model_match('HP M283fdw','HP M283fdn'))
    def test_similar_model_excluded(self):
        for model in ("RC-TWN22HN","RC-TWN35HN","RC-GL28HN","RCI-TWN28HN"):
            self.assertFalse(exact_model_match(model,"RC-TWN28HN"))
    def test_deduplication(self): self.assertEqual(len(deduplicate_offers([row(),row()])),1)
    def test_marketplace(self): self.assertEqual(classify_source("ozon.ru"),"marketplace")
    def test_federal(self): self.assertEqual(classify_source("citilink.ru"),"federal")
    def test_professional(self): self.assertEqual(classify_source("clima.ru"),"professional")
    def test_local(self): self.assertEqual(classify_source("clima.ru",local=True),"local")
    def test_public_price_preserved(self): self.assertEqual(prepare_offers([row()],"RC-TWN28HN",self.TARGET)[0]["public_price"],25000)
    def test_price_unavailable(self): self.assertIsNone(prepare_offers([row(public_price=None)],"RC-TWN28HN",self.TARGET)[0]["public_price"])
    def test_availability(self): self.assertEqual(prepare_offers([row()],"RC-TWN28HN",self.TARGET)[0]["availability"],"in_stock")
    def test_available_quantity(self): self.assertEqual(prepare_offers([row(available_quantity=3)],"RC-TWN28HN",self.TARGET)[0]["available_quantity"],3)
    def test_delivery_unknown(self): self.assertIsNone(prepare_offers([row(delivery_price=None)],"RC-TWN28HN",self.TARGET)[0]["delivery_price"])
    def test_checked_at(self): self.assertEqual(prepare_offers([row(checked_at="now")],"RC-TWN28HN",self.TARGET)[0]["checked_at"],"now")
    def test_target_threshold(self): self.assertEqual(price_priority(row(public_price=21590),self.TARGET),PRICE_FIRE)
    def test_one_ruble_above_target_does_not_pass(self): self.assertNotEqual(price_priority(row(public_price=21591),self.TARGET),PRICE_FIRE)
    def test_above_threshold_retained(self): self.assertEqual(len(prepare_offers([row(public_price=30000)],"RC-TWN28HN",self.TARGET)),1)
    def test_price_priority(self): self.assertEqual(price_priority(row(public_price=23850),self.TARGET),PRICE_HIGH)
    def test_target_calculation(self): self.assertEqual(supplier_target_price(25400),21590)
    def test_discount_percent_is_15(self): self.assertEqual(SUPPLIER_TARGET_DISCOUNT_PERCENT,15)
    def test_old_target_is_not_current(self): self.assertNotEqual(supplier_target_price(25400),20320)
    def test_verified_supplier(self): self.assertEqual(len(build_call_lists([row()])["suppliers_for_best_price_request"]),1)
    def test_yellow_supplier(self): self.assertEqual(len(build_call_lists([row(verification_status=MANUAL)])["requires_verification_before_work"]),1)
    def test_insufficient_supplier_separate(self): self.assertEqual(len(build_call_lists([row(verification_status=INSUFFICIENT)])["requires_verification_before_work"]),1)
    def test_red_excluded_from_call_list(self):
        lists=build_call_lists([row(verification_status=HIGH_RISK)])
        self.assertFalse(lists["suppliers_for_best_price_request"]); self.assertEqual(len(lists["excluded_high_risk"]),1)
    def test_public_not_purchase(self): self.assertIsNone(prepare_offers([row(public_price=1,purchase_price=1)],"RC-TWN28HN",self.TARGET)[0]["purchase_price"])
    def test_ranking(self):
        rows=prepare_offers([row(domain="a.ru",product_url="https://a.ru/x",public_price=30000),row(domain="b.ru",product_url="https://b.ru/x",public_price=20000)],"RC-TWN28HN",self.TARGET)
        self.assertEqual(build_call_lists(rows)["suppliers_for_best_price_request"][0]["domain"],"b.ru")
    def test_product_url_normalized(self): self.assertEqual(normalize_product_url("HTTPS://www.Shop.RU/a/?utm_source=x"),"https://shop.ru/a")
    def test_duplicate_url_tracking_removed(self):
        a=row(product_url="https://shop.ru/item?utm_source=a"); b=row(product_url="https://www.shop.ru/item/")
        self.assertEqual(len(deduplicate_offers([a,b])),1)
    def test_multiple_suppliers_retained(self):
        self.assertEqual(len(deduplicate_offers([row(),row(domain="two.ru",product_url="https://two.ru/item")])),2)


if __name__ == "__main__": unittest.main()
