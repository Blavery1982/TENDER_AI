"""Regression-проверки честного журнала этапов, без внешних сервисов."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
from google_sheets.test_journal import HEADERS, GoogleTestJournal, row_values
from pipeline.single_purchase_test import STAGES, run_check

PID = "93ffd454-9d9e-49f5-a4d7-acbcf8cdbe8b"


class MemoryJournal:
    def __init__(self):
        self.events = []

    def write(self, run, event):
        self.events.append(dict(event))

    def verify(self, run):
        assert self.events == run["events"]
        return {"url": "https://example.test/sheet", "rows_verified": len(self.events)}


class SinglePurchaseTestTests(unittest.TestCase):
    def test_later_stage_failure_is_recorded_and_following_stages_are_not_successful(self):
        card = {"raw": {"tradeNumber": "123", "lotItems": [{"name": "МФУ"}]}}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test._run_loaded", side_effect=RuntimeError("private-secret")):
            result = run_check(PID, journal=MemoryJournal(), card_loader=lambda _: card, output_dir=Path(directory))
        self.assertEqual(result["status"], "RUN_ERROR")
        self.assertNotIn("private-secret", str(result))
        self.assertFalse(any(e["status"] == "ВЫПОЛНЕНО" for e in result["events"]))
    def test_nested_card_preserves_law_and_known_model_defers_documents(self):
        card = {"raw": {"id": PID, "tradeNumber": "200909083126100184",
                "purchaseTypeTitle": "Закупка до 600 000 руб. (п. 5 ч.1 ст. 93 Закона №44-ФЗ)",
                "purchaseTypeId": "2", "lot": {"price": 66900, "subject": "МФУ",
                    "lotItems": [{"name": "МФУ", "model": "MA3500X", "quantity": 2, "unitPrice": 33450}],
                    "deliveryInfos": [{"deliveryAddress": {"regionName": "Самарская область"}}]}},
                "documents": [{"download_status": "deferred", "document_type": 15, "file_name": "Проект контракта.pdf"}]}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.single_purchase_test.process_procurement_documents") as extract:
            result = run_check(PID, journal=MemoryJournal(), card_loader=lambda _: card, output_dir=Path(directory))
        self.assertNotIn("law_not_determined", result["filter"]["rejection_reasons"])
        extract.assert_not_called()
        self.assertEqual(result["status"], "NO_ECONOMIC_SIGNAL")
        self.assertNotEqual(result["final_decision"]["status"], "ГОТОВО К УЧАСТИЮ")

    def test_card_failure_records_all_stages_and_never_searches(self):
        search = MagicMock(side_effect=AssertionError("Не должен запускаться"))
        journal = MemoryJournal()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = run_check(PID, journal=journal, card_loader=lambda _: {},
                               price_search=search, output_dir=Path(directory))
        self.assertEqual(result["status"], "CARD_NOT_RECEIVED")
        self.assertEqual([e["stage"] for e in result["events"][:-1]], STAGES)
        self.assertEqual(result["events"][0]["status"], "ОШИБКА")
        self.assertTrue(all(e["status"] == "НЕ ЗАПУЩЕН" for e in result["events"][1:-1]))
        search.assert_not_called()

    def test_external_error_does_not_leak_credentials_into_journal(self):
        def loader(_):
            raise RuntimeError("Authorization=private-secret")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = run_check(PID, journal=MemoryJournal(), card_loader=loader, output_dir=Path(directory))
        self.assertNotIn("private-secret", str(result))

    def test_cli_uses_requested_uuid_and_failure_exit_code(self):
        with patch("sys.argv", ["main.py", "--single-purchase-test", PID]), \
             patch("pipeline.single_purchase_test.run", return_value={"status": "CARD_NOT_RECEIVED",
                   "google_sheets": {"url": "https://example.test"}}) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 1)
        run.assert_called_once_with(PID)


class TestJournalTests(unittest.TestCase):
    def event(self):
        return {"checked_at": "2026-09-15T10:00:00+00:00", "stage": "Карточка",
                "status": "НЕПОЛНО", "result": "=опасная формула", "seconds": 1.25}

    def test_raw_write_preserves_text_and_uuid(self):
        book = MagicMock()
        ws = MagicMock(title="Тесты программы")
        book.worksheets.return_value = [ws]
        ws.row_values.return_value = HEADERS
        journal = GoogleTestJournal(book)
        run = {"run_id": "run-1", "purchase_id": PID}
        journal.write(run, self.event())
        ws.append_rows.assert_called_once_with([row_values(run, self.event())], value_input_option="RAW")

    def test_existing_different_headers_are_not_overwritten(self):
        book = MagicMock()
        ws = MagicMock(title="Тесты программы")
        book.worksheets.return_value = [ws]
        ws.row_values.return_value = ["Ручные данные"]
        with self.assertRaises(ValueError):
            GoogleTestJournal(book)
        ws.update.assert_not_called()

    def test_clear_live_rows_keeps_headers_and_only_clears_journal_body(self):
        book = MagicMock()
        ws = MagicMock(title="Тесты программы")
        book.worksheets.return_value = [ws]
        ws.row_values.return_value = HEADERS
        ws.get_all_values.return_value = [HEADERS, ["time", "run", "", "", "", "stage", "", "", "", "", "", "LIVE — одна закупка"]]
        journal = GoogleTestJournal(book)
        result = journal.clear_live_rows()
        ws.batch_clear.assert_called_once_with(["A2:L2"])
        self.assertEqual(result["rows_cleared"], 1)

    def test_clear_live_rows_refuses_other_mode(self):
        book = MagicMock()
        ws = MagicMock(title="Тесты программы")
        book.worksheets.return_value = [ws]
        ws.row_values.return_value = HEADERS
        ws.get_all_values.return_value = [HEADERS, ["time", "run", "", "", "", "stage", "", "", "", "", "", "production"]]
        journal = GoogleTestJournal(book)
        with self.assertRaises(RuntimeError):
            journal.clear_live_rows()
        ws.batch_clear.assert_not_called()

    def test_readback_rejects_missing_result(self):
        book = MagicMock()
        ws = MagicMock(title="Тесты программы")
        book.worksheets.return_value = [ws]
        ws.row_values.return_value = HEADERS
        ws.get_all_values.return_value = [HEADERS]
        journal = GoogleTestJournal(book)
        with self.assertRaises(RuntimeError):
            journal.verify({"run_id": "run-1", "events": [self.event()]})
