import unittest

from eat.enum_mapping import normalize_card_enums


class EatEnumMappingTests(unittest.TestCase):
    def test_control_purchase_verified_values(self):
        result = normalize_card_enums(
            {"purchaseMethod": 1, "purchaseTypeId": "1",
             "purchaseTypeTitle": "Закупка до 600 000 руб. по 44-ФЗ"},
            {"paymentType": 2, "paymentCondition": 3,
             "isRussianItemsPurchase": False, "paymentDateInDays": 7,
             "isPaymentPeriodWorkDays": True,
             "isPaymentInDaysSinceSigninAcceptanceDocument": True},
        )
        self.assertEqual(result["payment_type"], "По счету")
        self.assertEqual(result["payment_condition"], "В установленный срок")
        self.assertEqual(result["purchase_method"], "Закупочная сессия")
        self.assertEqual(result["russian_items_purchase"], "Нет")
        self.assertEqual(result["payment_deadline"],
                         "7 рабочих дней с даты подписания документа о приемке")
        self.assertEqual(result["diagnostics"], [])

    def test_unknown_code_is_blank_and_diagnostic(self):
        result = normalize_card_enums({}, {"paymentType": 999})
        self.assertIsNone(result["payment_type"])
        self.assertEqual(result["diagnostics"][0]["field"], "paymentType")
        self.assertEqual(result["diagnostics"][0]["raw_value"], 999)

    def test_explicit_structured_title_has_priority(self):
        result = normalize_card_enums({}, {"paymentType": 999,
                                           "paymentTypeTitle": "По банковской карте"})
        self.assertEqual(result["payment_type"], "По банковской карте")
        self.assertEqual(result["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
