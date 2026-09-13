import unittest

from documents.procurement_audit import classify_supplier_search_readiness, customer_document_issue
from model_search.ranking import procurement_business_status, rank_models
from model_search.technical_discovery import add_justification_candidate


def model(name, price, technical="fully_compliant"):
    return {"brand":"Тест","model":name,"technical_status":technical,
            "production_status":"in_production","russia_availability":"available",
            "market_price":price,"market_price_source":"цена","parameter_check":[]}


class ModelSearchBusinessTest(unittest.TestCase):
    def test_1_missing_justification_continues(self):
        result=classify_supplier_search_readiness([],[])
        self.assertTrue(result["automatic_supplier_search_allowed"])

    def test_2_justification_without_model_continues_external_search(self):
        self.assertEqual(add_justification_candidate([model("A",10)],{} )[0]["model"],"A")

    def test_3_compliant_justification_model_joins_candidates(self):
        result=add_justification_candidate([],model("КП-1",10))
        self.assertEqual(result[0]["candidate_source"],"price_justification")

    def test_4_bad_justification_model_does_not_reject_purchase(self):
        issue=customer_document_issue("price_justification_model_mismatch",1,"не подходит",{},"high")
        self.assertTrue(classify_supplier_search_readiness([issue],[])["automatic_supplier_search_allowed"])

    def test_5_quantity_mismatch_is_informational(self):
        issue=customer_document_issue("quantity_mismatch",1,"1 вместо 10",{},"high")
        self.assertTrue(classify_supplier_search_readiness([issue],[])["automatic_supplier_search_allowed"])

    def test_6_nmck_mismatch_is_informational(self):
        issue=customer_document_issue("nmck_not_supported",None,"суммы разные",{},"high")
        self.assertTrue(classify_supplier_search_readiness([issue],[])["automatic_supplier_search_allowed"])

    def test_7_cheapest_model_wins_even_if_found_later(self):
        ranked=rank_models([model("A",79),model("B",65)],100)
        self.assertEqual(ranked[0]["model"],"B")
        self.assertEqual(ranked[0]["reserve_percent"],35.0)

    def test_8_public_price_above_threshold_still_goes_to_supplier_search(self):
        result=procurement_business_status([model("дорогая",110)],100,{"sufficiently_broad":False})
        self.assertTrue(result["supplier_search_ready"])
        self.assertEqual(result["models_for_supplier_search"][0]["model"],"дорогая")

    def test_9_three_models_are_sorted_by_price(self):
        ranked=rank_models([model("C",90),model("A",60),model("B",75)],100)
        self.assertEqual([x["model"] for x in ranked],["A","B","C"])

    def test_10_incomplete_search_cannot_be_final_rejection(self):
        result=procurement_business_status([],100,{"sufficiently_broad":False})
        self.assertNotIn("НЕ ТРАТИТЬ",result["business_status"])

    def test_11_public_price_is_not_purchase_price(self):
        ranked=rank_models([model("A",90)],100)
        self.assertEqual(ranked[0]["public_price_reference"],90)
        self.assertIsNone(ranked[0]["purchase_price"])

    def test_12_budget_models_receive_higher_supplier_priority(self):
        ranked=rank_models([model("дорогая",90),model("бюджетная",60),model("средняя",75)],100)
        self.assertEqual([x["model"] for x in ranked],["бюджетная","средняя","дорогая"])
        self.assertEqual([x["supplier_search_priority"] for x in ranked],[1,2,3])

    def test_13_compliant_model_never_says_cheap_model_missing(self):
        result=procurement_business_status([model("A",110)],100,{"sufficiently_broad":False})
        self.assertEqual(result["business_status"],
                         "ГОТОВО К ПОИСКУ ПОСТАВЩИКОВ — найдены полностью соответствующие модели")

    def test_14_no_confirmed_model_continues_model_search(self):
        result=procurement_business_status([model("A",50,"not_confirmed")],100,{"sufficiently_broad":True})
        self.assertTrue(result["business_status"].startswith("ПРОДОЛЖИТЬ ПОИСК МОДЕЛЕЙ"))

    def test_15_nmck_issue_does_not_block_ready_model(self):
        issue=customer_document_issue("nmck_not_supported",None,"суммы разные",{},"high")
        self.assertTrue(classify_supplier_search_readiness([issue],[{"candidate_models":[model("A",90)]}])["automatic_supplier_search_allowed"])


if __name__=="__main__": unittest.main()
