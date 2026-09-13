import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from google_sheets.retention import PROTECTED_STATUSES, parse_moscow_deadline, plan_expired_cleanup
from google_sheets.workbook import (ACTIVE, ACTIVE_HEADERS, LOCKED, LOCKED_HEADERS,
                                    MANUAL, MANUAL_HEADERS)


MOSCOW = ZoneInfo("Europe/Moscow")


def active_row(deadline, purchase_id, status="Новая — нужен просчёт"):
    row = [""] * len(ACTIVE_HEADERS)
    row[ACTIVE_HEADERS.index("Крайний срок подачи заявки")] = deadline
    row[ACTIVE_HEADERS.index("ID закупки")] = purchase_id
    row[ACTIVE_HEADERS.index("Статус закупки")] = status
    return row


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 4, 12, 0, tzinfo=MOSCOW)

    def test_moscow_date_parsing(self):
        parsed = parse_moscow_deadline("04.09.2026 11:59")
        self.assertEqual(parsed.tzinfo, MOSCOW)
        self.assertLess(parsed, self.now)

    def test_all_active_item_rows_removed_but_counted_once(self):
        rows = {
            ACTIVE: [ACTIVE_HEADERS, active_row("04.09.2026 10:00", "id-1"), active_row("04.09.2026 10:00", "id-1")],
            MANUAL: [MANUAL_HEADERS], LOCKED: [LOCKED_HEADERS],
        }
        plan = plan_expired_cleanup(rows, self.now)
        self.assertEqual(plan.rows_by_sheet[ACTIVE], (3, 2))
        self.assertEqual(plan.deleted_purchases, 1)
        self.assertEqual(plan.deleted_rows, 2)

    def test_every_protected_status_preserves_whole_purchase(self):
        for status in PROTECTED_STATUSES:
            rows = {ACTIVE: [ACTIVE_HEADERS, active_row("01.09.2026 10:00", "id", status)], MANUAL: [], LOCKED: []}
            plan = plan_expired_cleanup(rows, self.now)
            self.assertEqual(plan.deleted_purchases, 0, status)
            self.assertIn("id", plan.protected_purchase_ids)

    def test_protected_active_id_is_preserved_in_all_working_tabs(self):
        protected = next(iter(PROTECTED_STATUSES))
        rows = {
            ACTIVE: [ACTIVE_HEADERS, active_row("01.09.2026 10:00", "same-id", protected)],
            MANUAL: [MANUAL_HEADERS, ["01.09.2026 10:00", "number", "same-id"]],
            LOCKED: [LOCKED_HEADERS, ["01.09.2026 10:00", "time", "same-id"]],
        }
        plan = plan_expired_cleanup(rows, self.now)
        self.assertEqual(plan.deleted_purchases, 0)
        self.assertEqual(plan.deleted_rows, 0)

    def test_bad_or_missing_date_is_not_deleted(self):
        rows = {ACTIVE: [ACTIVE_HEADERS, active_row("Нет данных", "id-1"), active_row("ошибка", "id-2")], MANUAL: [], LOCKED: []}
        plan = plan_expired_cleanup(rows, self.now)
        self.assertEqual(plan.deleted_purchases, 0)
        self.assertEqual(plan.unrecognized_purchase_ids, frozenset({"id-1", "id-2"}))

    def test_manual_and_locked_expired_rows_are_removed(self):
        manual = ["04.09.2026 09:00", "number", "manual-id"]
        locked = ["03.09.2026 09:00", "time", "locked-id"]
        rows = {ACTIVE: [ACTIVE_HEADERS], MANUAL: [MANUAL_HEADERS, manual], LOCKED: [LOCKED_HEADERS, locked]}
        plan = plan_expired_cleanup(rows, self.now)
        self.assertEqual(plan.rows_by_sheet[MANUAL], (2,))
        self.assertEqual(plan.rows_by_sheet[LOCKED], (2,))
        self.assertEqual(plan.deleted_purchases, 2)

    def test_future_deadline_is_preserved(self):
        rows = {ACTIVE: [ACTIVE_HEADERS, active_row("05.09.2026 10:00", "id")], MANUAL: [], LOCKED: []}
        self.assertEqual(plan_expired_cleanup(rows, self.now).deleted_purchases, 0)

    def test_reordered_headers_keep_cleanup_working(self):
        headers = list(ACTIVE_HEADERS)
        headers[0], headers[10] = headers[10], headers[0]
        canonical = active_row("04.09.2026 10:00", "id")
        by_name = dict(zip(ACTIVE_HEADERS, canonical))
        reordered = [by_name[name] for name in headers]
        rows = {ACTIVE: [headers, reordered], MANUAL: [], LOCKED: []}
        self.assertEqual(plan_expired_cleanup(rows, self.now).deleted_purchases, 1)


if __name__ == "__main__":
    unittest.main()
