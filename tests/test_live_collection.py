from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from eat.live_collection import DEFAULT_PAGE_SIZE, audit_unique_records, collect_all_pages


def item(number: int) -> dict:
    return {"id": f"id-{number}", "tradeNumber": str(number),
            "subject": f"Поставка товара {number}", "lotItems": [{"name": "Товар"}]}


class LivePaginationTests(unittest.TestCase):
    def test_default_page_size_reads_full_result_in_larger_api_pages(self):
        requested_sizes = []

        def fetch(page, size):
            requested_sizes.append(size)
            start = (page - 1) * size
            return {
                "items": [item(n) for n in range(start, min(start + size, 250))],
                "totalCount": 250,
            }

        result = collect_all_pages(fetch, endpoint="https://eat.test/list", pause_seconds=0)
        self.assertEqual(DEFAULT_PAGE_SIZE, 100)
        self.assertEqual(result["unique_count"], 250)
        self.assertEqual(result["requests_made"], 3)
        self.assertEqual(requested_sizes, [100, 100, 100])

    def test_confidential_page_without_subject_is_not_mistaken_for_empty(self):
        locked = {
            "id": "locked-1", "publishDate": "2026-09-11T10:00:00",
            "applicationFillingEndDate": "2026-09-12T10:00:00", "lotState": 1,
            "isTradeInfoHiddenByPrivacyAgreement": True,
            "hideDetailsForUnauthorized": True,
        }
        result = collect_all_pages(
            lambda page, size: {"items": [locked], "totalCount": 1},
            endpoint="https://eat.test/list", page_size=10, pause_seconds=0)
        self.assertTrue(result["end_proven"])
        self.assertEqual(result["unique_count"], 1)

    def test_reads_more_than_old_production_caps_until_total_count(self):
        def fetch(page, size):
            start = (page - 1) * size
            return {"items": [item(n) for n in range(start, min(start + size, 1205))],
                    "totalCount": 1205}

        result = collect_all_pages(fetch, endpoint="https://eat.test/list", page_size=10,
                                   pause_seconds=0)
        self.assertTrue(result["end_proven"])
        self.assertEqual(result["stop_reason"], "total_count_reached")
        self.assertEqual((result["pages_read"], result["raw_count"], result["unique_count"]),
                         (121, 1205, 1205))

    def test_empty_page_before_total_is_not_a_proven_end(self):
        def fetch(page, size):
            return {"items": [item(1)] if page == 1 else [], "totalCount": 2}

        result = collect_all_pages(fetch, endpoint="https://eat.test/list", page_size=10,
                                   pause_seconds=0)
        self.assertFalse(result["end_proven"])
        self.assertEqual(result["stop_reason"], "empty_page_before_total_count")

    def test_explicit_api_last_page_without_total_is_accepted(self):
        result = collect_all_pages(
            lambda page, size: {"items": [item(page)], "isLastPage": page == 3},
            endpoint="https://eat.test/list", page_size=1, pause_seconds=0)
        self.assertTrue(result["end_proven"])
        self.assertEqual(result["pages_read"], 3)

    def test_repeated_page_stops_with_diagnostic_error(self):
        result = collect_all_pages(
            lambda page, size: {"items": [item(1)], "totalCount": 10},
            endpoint="https://eat.test/list", page_size=1, pause_seconds=0)
        self.assertFalse(result["end_proven"])
        self.assertEqual(result["stop_reason"], "pagination_not_advancing_repeated_page")

    def test_every_request_has_required_diagnostics(self):
        result = collect_all_pages(
            lambda page, size: {"items": [item(page)], "totalCount": 2},
            endpoint="https://eat.test/list", page_size=1, pause_seconds=0)
        required = {"endpoint", "page", "size", "returned_count", "totalCount",
                    "cumulative_raw_count", "cumulative_unique_count", "timestamp",
                    "stop_reason"}
        self.assertTrue(all(required <= set(row) for row in result["page_diagnostics"]))


class LiveFilterAuditTests(unittest.TestCase):
    @patch("eat.live_collection.procurement_queue")
    @patch("eat.live_collection.filter_purchase_v2", return_value={"filter_result": "manual_check"})
    def test_api_purchase_type_title_is_used_when_catalog_map_is_absent(self, filtering, _routing):
        row = item(1)
        row["purchaseTypeTitle"] = "Закупка по Закону №44-ФЗ"
        audit_unique_records([row], {})
        self.assertEqual(filtering.call_args.args[1], "Закупка по Закону №44-ФЗ")

    @patch("eat.live_collection.procurement_queue")
    @patch("eat.live_collection.filter_purchase_v2")
    def test_filter_is_applied_before_deadline_and_processed_routing(self, filtering, routing):
        filtering.side_effect = [
            {"filter_result": "passed"}, {"filter_result": "rejected"},
            {"filter_result": "confidential_locked"}, {"filter_result": "passed"},
        ]
        routing.return_value = {"priority": 2, "queue": "PRIORITY_2_MODEL_NOT_SPECIFIED"}
        rows = [item(n) for n in range(1, 5)]
        rows[0]["applicationFillingEndDate"] = "2026-01-01T00:00:00Z"
        rows[1]["applicationFillingEndDate"] = "2027-01-01T00:00:00Z"
        rows[2]["applicationFillingEndDate"] = "2027-01-01T00:00:00Z"
        rows[3]["applicationFillingEndDate"] = "2027-01-01T00:00:00Z"
        result = audit_unique_records(
            rows, {}, already_processed_ids={"id-4"},
            now=datetime(2026, 9, 11, tzinfo=timezone.utc))
        counts = result["counts"]
        self.assertEqual(filtering.call_count, 4)
        self.assertEqual(counts["filter_applied"], 4)
        self.assertEqual(counts["expired"], 1)
        self.assertEqual(counts["already_processed"], 1)
        self.assertEqual(counts["priority_1"] + counts["priority_2"], 0)

    @patch("eat.live_collection.procurement_queue")
    @patch("eat.live_collection.filter_purchase_v2", return_value={"filter_result": "passed"})
    def test_active_unprocessed_passed_purchase_is_routed(self, filtering, routing):
        routing.return_value = {"priority": 1, "queue": "PRIORITY_1_EXACT_MODEL"}
        row = item(1)
        row["applicationFillingEndDate"] = "2027-01-01T00:00:00Z"
        result = audit_unique_records([row], {}, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
        self.assertEqual(result["counts"]["priority_1"], 1)


if __name__ == "__main__":
    unittest.main()
