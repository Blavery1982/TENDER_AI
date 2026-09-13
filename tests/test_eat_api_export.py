from __future__ import annotations

import unittest

from google_sheets.eat_api_export import _active_row, _export_rows
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

    def test_export_rows_skips_expired_and_routes_current_groups(self):
        def purchase(identifier, result, deadline):
            return {
                "id": identifier,
                "filter_result": result,
                "deadline_status": deadline,
                "filter": {},
                "raw": {
                    "id": identifier,
                    "subject": identifier,
                    "price": 200000,
                    "lotItems": [{}],
                },
            }

        active, manual, locked = _export_rows([
            purchase("active", "passed", "active"),
            purchase("manual", "manual_check", "active"),
            purchase("locked", "confidential_locked", "active"),
            purchase("expired", "passed", "expired"),
        ])
        self.assertEqual(len(active), 2)
        self.assertEqual(len(manual), 2)
        self.assertEqual(len(locked), 2)


if __name__ == "__main__":
    unittest.main()
