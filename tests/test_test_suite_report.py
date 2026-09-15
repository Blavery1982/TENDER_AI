"""Отображение успеха, падения и пропуска в журнале unittest."""
import io
import unittest
import contextlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.test_suite_report import ReportResult, run


class SuiteReportTests(unittest.TestCase):
    def test_selected_modules_do_not_discover_full_regression_suite(self):
        class Case(unittest.TestCase):
            def runTest(self):
                self.assertTrue(True)
        journal = MagicMock()
        journal.verify.side_effect = lambda data: {"url": "https://example.test", "rows_verified": len(data["events"])}
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
             patch("pipeline.test_suite_report.ROOT", Path(directory)), \
             patch("pipeline.test_suite_report.unittest.defaultTestLoader.loadTestsFromNames", return_value=unittest.TestSuite([Case()])) as selected, \
             patch("pipeline.test_suite_report.unittest.defaultTestLoader.discover", side_effect=AssertionError("Полный suite запрещён")):
            result = run(journal=journal, modules=["tests.test_exact_model_branch"])
        selected.assert_called_once_with(["tests.test_exact_model_branch"])
        self.assertEqual(result["tests_run"], 1)
        self.assertTrue(result["successful"])
        self.assertEqual(result["selected_modules"], ["tests.test_exact_model_branch"])
        journal.write_many.assert_not_called()
        journal.verify.assert_not_called()
        self.assertNotIn("google_sheets", result)

    def execute(self, action):
        class Case(unittest.TestCase):
            def runTest(self):
                action(self)
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=ReportResult).run(Case())

    def test_failure_is_not_promoted_to_success_and_secret_is_not_exported(self):
        result = self.execute(lambda test: test.fail("private-secret"))
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(next(iter(result.events.values()))["status"], "НЕ ПРОЙДЕН")
        self.assertNotIn("private-secret", str(result.events))

    def test_skip_is_distinct_from_success(self):
        result = self.execute(lambda test: test.skipTest("Причина"))
        self.assertEqual(next(iter(result.events.values()))["status"], "ПРОПУЩЕН")

    def test_success_has_own_result_and_duration(self):
        result = self.execute(lambda test: test.assertEqual(1, 1))
        event = next(iter(result.events.values()))
        self.assertEqual(event["status"], "ПРОЙДЕН")
        self.assertGreaterEqual(event["seconds"], 0)
