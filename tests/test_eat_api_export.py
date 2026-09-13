from __future__ import annotations

import unittest

from google_sheets.eat_api_export import _active_row
from google_sheets.workbook import ACTIVE_HEADERS


class EatApiExportTests(unittest.TestCase):
    def test_active_row_builds_public_purchase_link_from_id(self):
        purchase_id = "00000000-0000-0000-0000-000000000001"
        rows = _active_row({
            "raw": {
                "id": purchase_id,
                "subject": "Тестовая закупка",
                "price": 200000,
                "lotItems": [{}],
            }
        })
        link_column = ACTIVE_HEADERS.index("Ссылка на закупку")
        self.assertEqual(
            rows[0][link_column],
            f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info",
        )


if __name__ == "__main__":
    unittest.main()
