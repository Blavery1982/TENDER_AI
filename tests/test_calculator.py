import unittest

from calculator.calculator import scenario
from calculator.decision import calculator_decision
from calculator.formulas import (break_even_submission_price, calculate_economics,
                                 platform_commission, reserve_to_zero_percent,
                                 tax_amount)
from calculator.schemas import analyze_costs


ZERO_COSTS={"delivery_cost":0,"logistics_cost":0,"unloading_cost":0,"assembly_cost":0,
            "installation_cost":0,"packaging_removal_cost":0,"other_costs":0}


class CalculatorTest(unittest.TestCase):
    def test_01_commission_is_three_percent_nmck(self):
        self.assertEqual(platform_commission(76200),2286)

    def test_02_purchase_price_can_be_null(self):
        self.assertIsNone(calculator_decision(True,True,None,[]).get("purchase_price"))

    def test_03_public_price_does_not_become_purchase_price(self):
        data={"public_price":19900,"purchase_price":None}
        self.assertIsNone(data["purchase_price"])

    def test_04_tax_is_fifteen_percent_of_defined_base(self):
        self.assertEqual(tax_amount(100,60,10),(30,4.5))

    def test_05_negative_tax_base_means_zero_tax(self):
        self.assertEqual(tax_amount(100,120,10),(-30,0))

    def test_06_additional_costs_are_summed(self):
        costs=dict(ZERO_COSTS,delivery_cost=100,unloading_cost=50,other_costs=25)
        self.assertEqual(analyze_costs(costs)["additional_costs_total"],175)

    def test_07_unknown_delivery_is_not_silent_zero(self):
        self.assertIn("delivery_cost",analyze_costs(dict(ZERO_COSTS,delivery_cost=None))["unknown_costs"])

    def test_08_net_profit(self):
        # 100 - 60 - комиссия 3 - налог 6 = 31
        self.assertEqual(calculate_economics(100,100,60,0)["net_profit"],31)

    def test_09_break_even(self):
        self.assertAlmostEqual(break_even_submission_price(100,60,0),63.53,places=2)

    def test_10_reserve_to_zero(self):
        self.assertAlmostEqual(reserve_to_zero_percent(100,63.53),36.47,places=2)

    def test_11_minus_five_percent(self):
        x=scenario(19000,3,76200,ZERO_COSTS)
        self.assertEqual(x["calculations"][1]["submission_price"],72390)

    def test_12_minus_ten_percent(self):
        x=scenario(19000,3,76200,ZERO_COSTS)
        self.assertEqual(x["calculations"][2]["submission_price"],68580)

    def test_13_minus_fifteen_percent(self):
        x=scenario(19000,3,76200,ZERO_COSTS)
        self.assertEqual(x["calculations"][3]["submission_price"],64770)

    def test_14_status_without_purchase_price(self):
        x=calculator_decision(True,True,None,["delivery_cost"])
        self.assertEqual(x["status"],"ТРЕБУЕТСЯ ПОЛУЧИТЬ АКТУАЛЬНЫЕ ЦЕНЫ ПОСТАВЩИКОВ")

    def test_15_status_with_unknown_logistics(self):
        x=calculator_decision(True,True,100,["delivery_cost"])
        self.assertEqual(x["status"],"ТРЕБУЕТСЯ УТОЧНИТЬ РАСХОДЫ")

    def test_16_status_with_positive_economics(self):
        econ=calculate_economics(100,100,60,0)
        self.assertEqual(calculator_decision(True,True,60,[],econ)["status"],"ГОТОВО К УЧАСТИЮ")

    def test_17_status_with_negative_economics(self):
        econ=calculate_economics(100,100,110,0)
        self.assertEqual(calculator_decision(True,True,110,[],econ)["status"],"ЭКОНОМИКА НЕ ПРОХОДИТ")

    def test_18_hypothetical_price_not_saved_as_purchase_price(self):
        x=scenario(19000,3,76200,ZERO_COSTS)
        self.assertTrue(x["scenario_only"]); self.assertIsNone(x["purchase_price"])

    def test_unknown_cost_blocks_scenario_math(self):
        with self.assertRaises(ValueError):
            scenario(19000,3,76200,dict(ZERO_COSTS,delivery_cost=None))

    def test_margin_and_profitability_are_distinct(self):
        x=calculate_economics(100,100,60,0)
        self.assertNotEqual(x["margin_percent"],x["profitability_percent"])


if __name__=="__main__": unittest.main()
