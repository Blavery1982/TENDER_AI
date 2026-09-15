import unittest

from suppliers.ranking import price_request_candidates, select_quotes
from suppliers.verification import (HIGH_RISK, INSUFFICIENT, MANUAL, PASSED,
                                    normalize_domain, verify_invoice_recipient,
                                    verify_supplier)
from suppliers.site_requisites import extract_requisites
from suppliers.arbitration import defendant_kad_result


def offer(name="A", price=100, category="federal_or_specialist", status=PASSED):
    return {"supplier_name":name,"product_url":f"https://{name.lower()}.ru/item",
            "source_category":category,"public_price":price,"purchase_price":None,
            "exact_model":True,"product_page_available":True,"verification_status":status,
            "availability":"in_stock"}


class SupplierSearchTest(unittest.TestCase):
    def test_01_above_threshold_not_quote(self):
        self.assertIsNone(select_quotes([offer(price=101)],100)["kp2"])

    def test_02_high_risk_not_quote(self):
        self.assertIsNone(select_quotes([offer(price=90,status=HIGH_RISK)],100)["kp2"])

    def test_03_manual_not_quote(self):
        self.assertIsNone(select_quotes([offer(price=90,status=MANUAL)],100)["kp2"])

    def test_04_passed_below_threshold_is_quote(self):
        self.assertEqual(select_quotes([offer(price=90)],100)["kp2"]["public_price"],90)

    def test_05_marketplace_only_kp1(self):
        q=select_quotes([offer(price=90,category="marketplace")],100)
        self.assertIsNotNone(q["kp1"]); self.assertIsNone(q["kp2"])

    def test_06_federal_only_kp2(self):
        q=select_quotes([offer(price=90)],100)
        self.assertIsNotNone(q["kp2"]); self.assertIsNone(q["kp1"])

    def test_07_local_only_kp3(self):
        self.assertIsNotNone(select_quotes([offer(price=90,category="local")],100)["kp3"])

    def test_08_lowest_eligible_price_wins(self):
        q=select_quotes([offer("A",90),offer("B",80)],100)
        self.assertEqual(q["kp2"]["supplier_name"],"B")

    def test_09_no_wayback_is_not_fraud(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":5,"wayback_available":False,"company":{"inn":"1","active":True}}})
        self.assertNotEqual(x["verification_status"],HIGH_RISK)

    def test_10_young_domain_alone_not_fraud(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":1,"wayback_available":True,"company":{"inn":"1","active":True}}})
        self.assertNotEqual(x["verification_status"],HIGH_RISK)

    def test_11_recent_director_is_red_flag(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":5,"wayback_available":True,"company":{"inn":"1","active":True,
            "director_changed_within_6_months":True}}})
        self.assertIn("Руководитель сменился менее 6 месяцев назад",x["risk_flags"])

    def test_12_company_mismatch_is_high_risk(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":5,"wayback_available":True,"company":{"inn":"1","active":True,
            "site_company_mismatch":True}}})
        self.assertEqual(x["verification_status"],HIGH_RISK)

    def test_13_unavailable_services_are_not_invented(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "whois_status":"unavailable","wayback_status":"unavailable","company":{}}})
        self.assertIn(x["verification_status"],{MANUAL,INSUFFICIENT})
        self.assertTrue(x["unavailable_checks"])

    def test_14_public_price_never_becomes_purchase_price(self):
        self.assertIsNone(select_quotes([offer(price=90)],100)["kp2"]["purchase_price"])

    def test_15_above_threshold_can_request_price(self):
        self.assertEqual(len(price_request_candidates([offer(price=105)],100,3)),1)

    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("https://www.Shop.RU/item"),"shop.ru")

    def test_future_invoice_mismatch(self):
        supplier={"verification_checks":{"company":{"inn":"123"}}}
        self.assertIn("НЕ ОПЛАЧИВАТЬ",verify_invoice_recipient(supplier,"456","ООО")["status"])

    def test_requisites_are_extracted_with_spaces_and_hyphens(self):
        x=extract_requisites("ООО «Тест» ИНН 77 01-097787 ОГРН 115-774-6298104 mail@test.ru")
        self.assertEqual(x["inn"],["7701097787"])
        self.assertEqual(x["ogrn"],["1157746298104"])

    def test_consistent_legal_entity_and_old_domain_can_pass_without_cms_ip(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "kad_check":defendant_kad_result("7701097787",0),
            "domain_age_years":8,"requisites_consistent":True,"cms_status":"unavailable",
            "ip_status":"unavailable","wayback_status":"unavailable",
            "company":{"inn":"7701097787","active":True,"director_changed_within_6_months":False}}})
        self.assertEqual(x["verification_status"],PASSED)

    def test_multiple_inns_without_current_seller_are_manual_not_fraud(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":5,"requisites_consistent":False,"current_seller_determined":False,
            "company":{"inn":None,"active":None},
            "requisites_conflict_evidence":{"inn":["7701097787","5904993922"]}}})
        self.assertEqual(x["verification_status"],MANUAL)

    def test_old_inn_and_new_active_inn_are_history_not_red_flag(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":8,"requisites_consistent":False,"current_seller_determined":True,
            "current_company":{"inn":"7701097787","active":True,"registered_at":"2018-01-01",
                               "registered_recently":False},
            "historical_companies":[{"inn":"5904993922","active":False}]}})
        self.assertNotEqual(x["verification_status"],HIGH_RISK)
        self.assertIn("Текущий продавец определён; другие ИНН сохранены как исторические",x["positive_signals"])

    def test_new_current_inn_registered_long_ago_can_pass(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "kad_check":defendant_kad_result("7701097787",0),
            "domain_age_years":8,"requisites_consistent":False,"current_seller_determined":True,
            "current_company":{"inn":"7701097787","active":True,"registered_at":"2014-01-01",
                               "registered_recently":False},"historical_companies":[{"inn":"1"}]}})
        self.assertEqual(x["verification_status"],PASSED)

    def test_recent_current_legal_entity_requires_manual_review(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":8,"requisites_consistent":False,"current_seller_determined":True,
            "current_company":{"inn":"7701097787","active":True,"registered_at":"2026-08-01",
                               "registered_recently":True},"historical_companies":[{"inn":"1"}]}})
        self.assertEqual(x["verification_status"],MANUAL)
        self.assertTrue(any("зарегистрировано недавно" in r for r in x["risk_flags"]))

    def test_liquidated_old_company_and_active_new_company_not_fraud(self):
        x=verify_supplier({"product_url":"https://shop.ru/x","verification_checks":{
            "domain_age_years":8,"requisites_consistent":False,"current_seller_determined":True,
            "current_company":{"inn":"7701097787","active":True,"registered_at":"2018-01-01",
                               "registered_recently":False},
            "historical_companies":[{"inn":"5904993922","active":False}]}})
        self.assertNotEqual(x["verification_status"],HIGH_RISK)

    def test_invoice_from_unconfirmed_third_party_is_blocked(self):
        supplier={"verification_checks":{"current_company":{"inn":"7701097787","active":True}}}
        result=verify_invoice_recipient(supplier,"5904993922","Другое ООО")
        self.assertFalse(result["matches"])
        self.assertIn("НЕ ОПЛАЧИВАТЬ",result["status"])


if __name__ == "__main__": unittest.main()
