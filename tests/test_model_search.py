import unittest
from model_search.price_discovery import discover_prices
from model_search.search import run_test
from model_search.market_eligibility import market_eligible

class ModelSearchTest(unittest.TestCase):
    def test_price_stage_does_not_run_for_technical_failure(self):
        candidate={"technical_status":"not_confirmed","price_check_status":"price_not_confirmed","rejection_reasons":[],"market_price":10,"market_price_source":"url"}
        result=discover_prices([candidate],20)[0]
        self.assertEqual(result["price_discovery_status"],"not_started_technical_check_failed")

    def test_control_purchase_has_no_unproven_admission(self):
        result=run_test()
        candidates=[c for p in result["positions"] for c in p["candidate_models"]]
        self.assertEqual(len(candidates),3)
        self.assertTrue(all(not p["admitted_models"] for p in result["positions"]))
        self.assertTrue(all(c["price_discovery_status"]=="not_started_technical_check_failed" for c in candidates))

    def test_model_needs_production_and_russian_availability(self):
        base={"technical_status":"fully_compliant","price_check_status":"passes_20_percent_threshold"}
        self.assertFalse(market_eligible({**base,"production_status":"production_not_confirmed","russia_availability":"available"}))
        self.assertFalse(market_eligible({**base,"production_status":"in_production","russia_availability":"not_confirmed"}))
        self.assertTrue(market_eligible({**base,"production_status":"in_production","russia_availability":"available_to_order"}))

if __name__=="__main__": unittest.main()
