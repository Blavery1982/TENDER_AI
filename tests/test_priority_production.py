import unittest
from pipeline.priority_production import _economics, _exact_item


class PriorityProductionTests(unittest.TestCase):
    def test_economics_uses_configured_commission(self):
        payload={"procurement":{"nmck":200000},"item":{"quantity":2},
                 "supplier_search":{"minimum_confirmed_price":70000}}
        result=_economics(payload,{"calculator":{"eat_commission_rate":.03}})
        self.assertEqual(result["eat_commission"],6000)
        self.assertEqual(result["nmck_after_eat_commission"],194000)
        self.assertEqual(result["preliminary_margin_before_logistics"],54000)

    def test_unknown_supplier_price_keeps_margin_unknown(self):
        payload={"procurement":{"nmck":200000},"item":{"quantity":2},
                 "supplier_search":{"minimum_confirmed_price":None}}
        self.assertIsNone(_economics(payload,{"calculator":{"eat_commission_rate":.03}})
                          ["preliminary_margin_before_logistics"])

    def test_single_production_entry_uses_global_price_readiness(self):
        fixture={"raw":{"lotItems":[{"model":"4303dw"}]}}
        position,item,readiness=_exact_item(fixture,{"items":[{"requirements":[]}]})
        self.assertEqual((position,readiness["classification"]),(1,"PRICE_SEARCH_READY"))
        self.assertEqual(readiness["identifier"],"4303dw")
        self.assertFalse(readiness["compliance_before_price_search"])


if __name__ == "__main__": unittest.main()
