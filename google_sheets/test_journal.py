"""Журнал контрольных проверок в существующей рабочей Google-таблице."""
from __future__ import annotations

import re
from typing import Any

from google_sheets.client import _env, authorize_service_account

TITLE = "Тесты программы"
HEADERS = ["Время проверки", "Запуск", "Номер закупки", "ID закупки", "Позиция",
           "Этап / тест", "Статус", "Полученный результат", "Что осталось / причина",
           "Источник", "Время, сек.", "Режим проверки"]


def row_values(run: dict[str, Any], event: dict[str, Any]) -> list[Any]:
    """RAW сохраняет идентификаторы и текст, начинающийся с =, без формул."""
    return [event["checked_at"], run["run_id"], run.get("tender_number") or "",
            run.get("purchase_id") or "", event.get("position") or "",
            event["stage"], event["status"], event.get("result") or "",
            event.get("limitation") or "", event.get("source") or run.get("card_url") or "",
            event.get("seconds", 0), event.get("mode") or run.get("mode") or ""]


class GoogleTestJournal:
    def __init__(self, book=None):
        if book is None:
            client, _ = authorize_service_account()
            book = client.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
        self.book = book
        sheets = {ws.title: ws for ws in book.worksheets()}
        self.ws = sheets.get(TITLE)
        if self.ws is None:
            self.ws = book.add_worksheet(title=TITLE, rows=2000, cols=len(HEADERS))
            self.ws.update([HEADERS], "A1", value_input_option="RAW")
            self.ws.freeze(rows=1)
            self.ws.set_basic_filter(f"A1:L{self.ws.row_count}")
            self.ws.format("A1:L1", {"backgroundColor": {"red": .12, "green": .2, "blue": .32},
                                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                    "wrapStrategy": "WRAP"})
            self.ws.format("A2:L2000", {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"})
            widths = [155, 220, 160, 240, 75, 240, 170, 520, 480, 350, 100, 165]
            book.batch_update({"requests": [
                {"updateDimensionProperties": {"range": {"sheetId": self.ws.id,
                 "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                 "properties": {"pixelSize": width}, "fields": "pixelSize"}}
                for i, width in enumerate(widths)]})
        elif self.ws.row_values(1) != HEADERS:
            raise ValueError("Заголовки листа «Тесты программы» отличаются; существующие данные не изменены")

    @property
    def url(self):
        return f"{self.book.url}#gid={self.ws.id}"

    def clear_live_rows(self):
        """Очистить только строки рабочего LIVE-журнала, сохранив заголовок."""
        if self.ws.title != TITLE or self.ws.row_values(1) != HEADERS:
            raise RuntimeError("Очистка отменена: лист не является журналом контрольных тестов")
        rows = self.ws.get_all_values()
        for row in rows[1:]:
            values = list(row) + [""] * (len(HEADERS) - len(row))
            mode = str(values[11]).strip()
            if mode and "LIVE" not in mode and "UNIT" not in mode:
                raise RuntimeError("Очистка отменена: в журнале найдена строка другого режима")
        if len(rows) > 1:
            self.ws.batch_clear([f"A2:L{len(rows)}"])
        return {"rows_cleared": max(0, len(rows) - 1), "header_preserved": True,
                "url": self.url}

    def write(self, run, event):
        response = self.ws.append_rows([row_values(run, event)], value_input_option="RAW")
        self._fit_rows(response)

    def write_many(self, run):
        if run["events"]:
            response = self.ws.append_rows([row_values(run, event) for event in run["events"]], value_input_option="RAW")
            self._fit_rows(response)

    def _fit_rows(self, response):
        if not isinstance(response, dict):
            return
        updated_range = (response.get("updates") or {}).get("updatedRange", "")
        match = re.search(r"![A-Z]+(\d+):[A-Z]+(\d+)$", updated_range)
        if match:
            self.book.batch_update({"requests": [{"autoResizeDimensions": {"dimensions": {
                "sheetId": self.ws.id, "dimension": "ROWS",
                "startIndex": int(match[1]) - 1, "endIndex": int(match[2])}}}]})

    def update_run(self, run):
        """Исправить только строки данного запуска, сохранив остальные проверки."""
        matching = [(number, row) for number, row in enumerate(self.ws.get_all_values()[1:], 2)
                    if len(row) > 1 and row[1] == run["run_id"]]
        if len(matching) != len(run["events"]):
            raise RuntimeError("Набор строк запуска изменился; обновление отменено")
        updates = []
        for (number, old), event in zip(matching, run["events"]):
            if len(old) < 6 or old[5] != event["stage"]:
                raise RuntimeError("Этап строки изменился; обновление отменено")
            updates.append({"range": f"A{number}:L{number}", "values": [row_values(run, event)]})
        self.ws.batch_update(updates, value_input_option="RAW")
        for number, _ in matching:
            self._fit_rows({"updates": {"updatedRange": f"sheet!A{number}:L{number}"}})
        return self.verify(run)

    def verify(self, run):
        rows = self.ws.get_all_values()
        matching = [(number, row) for number, row in enumerate(rows[1:], 2)
                    if len(row) > 1 and row[1] == run["run_id"]]
        actual = [row for _, row in matching]
        expected = [row_values(run, event) for event in run["events"]]
        if not expected:
            raise RuntimeError("Нет результатов для подтверждения Google Sheets")
        if len(actual) != len(expected):
            raise RuntimeError("Число строк журнала не совпало с числом результатов проверки")
        # Метаданные ранних этапов могли быть записаны до получения номера ЕАТ.
        for source, saved in zip(expected, actual):
            for index in (0, 1, 3, 4, 5, 6, 7, 8, 9, 11):
                if str(source[index]) != str(saved[index]):
                    raise RuntimeError("Проверка прочитанного результата Google Sheets не пройдена")
        first_row, last_row = matching[0][0], matching[-1][0]
        return {"url": f"{self.url}&range=A{first_row}:L{last_row}", "rows_verified": len(actual),
                "first_row": first_row, "last_row": last_row}
