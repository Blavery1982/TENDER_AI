import unittest

from calculator.result_decision import attach_business_decision
from google_sheets.production_upsert import EXTRA_HEADERS, row_values
from google_sheets.workbook import ACTIVE_HEADERS
from suppliers.economic_precheck import economic_precheck
from suppliers.exact_model_flow import exact_supplier_flow


def offer(name, price):
    return {"seller": name, "price": price,
            "url": f"https://{name}.ru/product/model",
            "exact_model_match": True, "availability": "В наличии"}


def business_payload(commission=2531.50):
    return {
        "procurement": {"nmck": 200, "commission_fee": commission},
        "item": {"position_number": 1, "quantity": 1},
        "audit": {"additional_expense_state": {"ready": True, "amount": 1}},
        "supplier_search": {"ranked_offers": [
            {"source_url": "https://a.ru/product/model", "confirmed_price": 100,
             "price_confirmed": True, "exact_model_confirmed": True,
             "availability_normalized": "in_stock", "antifraud_status": "green"},
            {"source_url": "https://b.ru/product/model", "confirmed_price": 110,
             "price_confirmed": True, "exact_model_confirmed": True,
             "availability_normalized": "in_stock", "antifraud_status": "green"},
            {"source_url": "https://c.ru/product/model", "confirmed_price": 120,
             "price_confirmed": True, "exact_model_confirmed": True,
             "availability_normalized": "in_stock", "antifraud_status": "green"},
        ]},
    }


class CommissionSourceTests(unittest.TestCase):
    def test_exact_flow_keeps_eat_fee_exactly(self):
        flow = exact_supplier_flow(
            {"price": 100_000, "commissionFee": 2531.50},
            [{"position_number": 1, "quantity": 1,
              "source_offers": [offer("a", 10), offer("b", 11), offer("c", 12)]}],
            verifier=lambda row: {**row, "verification_status": "✅ ПРОШЁЛ"},
        )
        self.assertEqual(flow["commission"], 2531.50)
        self.assertEqual(flow["economics"]["commission"], 2531.50)
        self.assertEqual(flow["commission_source"], "ЕАТ: lot.commissionFee")

    def test_precheck_does_not_replace_actual_fee_with_old_value(self):
        result = economic_precheck(100_000, 2531.50,
                                   [{"quantity": 1, "offers": [offer("a", 10)]}])
        self.assertEqual(result["commission"], 2531.50)
        self.assertEqual(result["commission_source"], "raw.lot.commissionFee")

    def test_missing_fee_makes_business_review(self):
        payload = business_payload()
        del payload["procurement"]["commission_fee"]
        decision = attach_business_decision(payload)
        self.assertEqual(decision["status"], "manual_review")
        self.assertIsNone(payload["final_economics"].get("eat_commission"))

    def test_explicit_zero_fee_is_real_zero(self):
        result = economic_precheck(100_000, 0,
                                   [{"quantity": 1, "offers": [{**offer("a", 10), "public_price": 10}]}])
        self.assertEqual(result["commission"], 0)
        self.assertTrue(result["passes"])

    def test_business_decision_uses_canonical_procurement_fee(self):
        payload = business_payload()
        payload["eat_commission"] = 999999
        payload["economic_precheck"] = {"commission": 999999}
        decision = attach_business_decision(payload)
        self.assertEqual(payload["final_economics"]["eat_commission"], 2531.50)
        self.assertEqual(payload["final_economics"]["commission_source"], "procurement.commission_fee")
        self.assertEqual(decision["status"], "do_not_bid")

    def test_old_unproven_final_economics_is_replaced(self):
        payload = business_payload()
        payload["final_economics"] = {
            "profitability_percent": 99, "commission": 999999,
            "complete": True, "mandatory_expenses_included": True,
        }
        decision = attach_business_decision(payload)
        self.assertEqual(payload["final_economics"]["eat_commission"], 2531.50)
        self.assertEqual(payload["final_economics"]["commission_source"], "procurement.commission_fee")
        self.assertEqual(decision["status"], "do_not_bid")

    def test_sheet_receives_procurement_fee_without_recalculation(self):
        payload = business_payload()
        payload.update({"model": {}, "documents": {"processed": 0}, "traceability": {}})
        payload["economics"] = {"nmck_after_eat_commission": 1}
        row = row_values(payload, ACTIVE_HEADERS + EXTRA_HEADERS)
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        self.assertEqual(row[headers.index("Комиссия площадки, ₽")], 2531.50)


if __name__ == "__main__":
    unittest.main()
