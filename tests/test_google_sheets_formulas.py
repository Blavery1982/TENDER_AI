import unittest

from google_sheets.production_upsert import EXTRA_HEADERS, row_values
from google_sheets.workbook import ACTIVE_HEADERS, CALCULATION_COLUMNS, formulas


def _payload() -> dict:
    return {
        "procurement": {
            "deadline": "2026-09-14T08:52:00",
            "trade_number": "1001",
            "id": "uuid",
            "url": "https://agregatoreat.ru/x",
            "subject": "Товар",
            "delivery_address": "Москва",
            "delivery_period": 10,
            "delivery_working_days": True,
            "payment_type": 2,
            "nmck": 200000,
            "commission_fee": 6000,
            "contact": {},
        },
        "item": {
            "position_number": 1,
            "display_name": "Товар",
            "quantity": 2,
            "unit": "шт",
            "customer_unit_price": 100000,
            "sum": 200000,
        },
        "documents": {"processed": 0},
        "audit": {"requirements_count": 0, "customer_check": {}},
        "model": {},
        "traceability": {},
        "supplier_search": {"confirmed_offers": [], "suppliers_for_call": []},
        "warnings": [],
    }


class GoogleSheetsCalculationFormulaTest(unittest.TestCase):
    def test_missing_calculation_values_are_empty_not_placeholder_text(self):
        headers = ACTIVE_HEADERS + EXTRA_HEADERS
        row = row_values(_payload(), headers)
        for index in CALCULATION_COLUMNS:
            if index < len(ACTIVE_HEADERS):
                self.assertEqual(row[index], "", ACTIVE_HEADERS[index])

    def test_formula_chain_checks_for_numeric_inputs(self):
        sheet_formulas = formulas(2)
        for index in (25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 52):
            self.assertIn('""', sheet_formulas[index])
        combined = "\n".join(sheet_formulas.values())
        self.assertIn("ISNUMBER($Y$2:$Y$10000)", combined)
        self.assertIn("ISNUMBER($AK$2:$AK$10000)", combined)
        self.assertIn("ISNUMBER($AO$2:$AO$10000)", combined)
        self.assertIn("ISNUMBER($AS$2:$AS$10000)", combined)
        self.assertNotIn('COUNTIFS($C$2:$C$10000;$C2;$AK$2:$AK$10000;">0")', combined)
        self.assertNotIn("LET(", sheet_formulas[26])

    def test_real_zero_is_not_treated_as_missing(self):
        combined = "\n".join(formulas(2).values())
        # ISNUMBER(0) is TRUE in Google Sheets. This preserves a real zero while
        # empty/text cells do not enter the calculation chain.
        self.assertIn("N(ISNUMBER($Y$2:$Y$10000))", combined)
        self.assertNotIn('$Y$2:$Y$10000;">0"', combined)

    def test_dependent_formulas_stop_when_previous_result_is_empty(self):
        sheet_formulas = formulas(2)
        self.assertIn("ISNUMBER(AB2)", sheet_formulas[28])
        self.assertIn("ISNUMBER(AC2)", sheet_formulas[29])
        self.assertIn("ISNUMBER(AD2)", sheet_formulas[30])
        self.assertIn("ISNUMBER(AE2)", sheet_formulas[34])

    def test_formulas_follow_reordered_headers(self):
        headers = list(ACTIVE_HEADERS)
        left, right = headers.index("ID закупки"), headers.index("Заказчик")
        headers[left], headers[right] = headers[right], headers[left]
        left, right = headers.index("Цена КП 1"), headers.index("Название позиции (ТЗ)")
        headers[left], headers[right] = headers[right], headers[left]
        reordered = formulas(2, headers)
        average_formula = reordered[headers.index("Средняя цена закупа, ₽")]
        self.assertIn("$K$2:$K$10000", average_formula)  # ID закупки теперь K
        self.assertIn("$P$2:$P$10000", average_formula)  # Цена КП 1 теперь P


if __name__ == "__main__":
    unittest.main()
