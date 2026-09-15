"""Явный запуск unittest с локальным отчётом, без выгрузки UNIT в Sheets."""
from __future__ import annotations

import io
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from documents.tender_archive import write_json

ROOT = Path(__file__).resolve().parent.parent


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = {}

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()

    def record(self, test, status, limitation=""):
        self.events[test.id()] = {"checked_at": datetime.now(timezone.utc).isoformat(),
            "stage": test.id(), "status": status, "result": "Автоматический regression-тест",
            "limitation": limitation, "seconds": round(time.monotonic() - self.started, 3),
            "mode": "UNIT / локально и mocks"}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "ПРОЙДЕН")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "НЕ ПРОЙДЕН", err[0].__name__)

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "ОШИБКА", err[0].__name__)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "ПРОПУЩЕН", "Тест пропущен; успех не подтверждён")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.record(test, "НЕ ПРОЙДЕН", err[0].__name__)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, "ОЖИДАЕМОЕ ПАДЕНИЕ", err[0].__name__)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, "НЕОЖИДАННЫЙ УСПЕХ", "Ожидалось падение теста")


def run(*, journal=None, pattern="test_*.py", modules=None):
    # Совместимый аргумент больше не используется для выгрузки UNIT.
    suite = (unittest.defaultTestLoader.loadTestsFromNames(modules) if modules is not None
             else unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern))
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0, resultclass=ReportResult).run(suite)
    data = {"run_id": datetime.now(timezone.utc).strftime("UNIT_%Y%m%dT%H%M%S%fZ"),
            "mode": "UNIT / локально и mocks", "events": list(result.events.values()),
            "tests_run": result.testsRun, "successful": result.wasSuccessful(),
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
    if modules is not None:
        data["selected_modules"] = list(modules)
    path = ROOT / "data/test_runs" / (data["run_id"] + ".json")
    write_json(path, data)
    print(f"Тестов: {result.testsRun}; ошибок: {len(result.errors)}; падений: {len(result.failures)}")
    print(f"Локальный отчёт: {path}")
    return data
