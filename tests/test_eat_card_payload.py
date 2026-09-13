import unittest

from eat.card_payload import card_purchase_payloads, fullest_card_purchase


class EatCardPayloadTests(unittest.TestCase):
    def test_customer_sibling_is_preserved_with_trade(self):
        purchase_id = "fd71642c-1c03-4739-a487-f1e8fa2722a6"
        response = {
            "trade": {"id": purchase_id, "tradeNumber": "100316493126100091", "lot": {}},
            "customer": {
                "name": 'ФКУ "ДИРЕКЦИЯ"',
                "inn": "7703255580",
                "kpp": "770301001",
            },
            "changes": [],
        }
        result = fullest_card_purchase(card_purchase_payloads(response, purchase_id))
        self.assertEqual(result["tradeNumber"], "100316493126100091")
        self.assertEqual(result["customer"]["inn"], "7703255580")
        self.assertEqual(result["customer"]["kpp"], "770301001")

    def test_unrelated_customer_is_not_attached(self):
        response = {
            "trade": {"id": "another"},
            "customer": {"inn": "7703255580"},
        }
        self.assertEqual(card_purchase_payloads(response, "wanted"), [])


if __name__ == "__main__":
    unittest.main()
