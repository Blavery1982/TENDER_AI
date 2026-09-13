"""Безопасная очистка просроченных закупок в рабочих вкладках."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from google_sheets.workbook import ACTIVE, LOCKED, MANUAL

MOSCOW = ZoneInfo("Europe/Moscow")

PROTECTED_STATUSES = frozenset({
    "КП готово — ждём подачи",
    "Подача сделана — ждём результаты",
    "Победили — ждём подписание",
    "Подписали — ждём подписание заказчиком",
    "Контракт подписан обеими сторонами — отправляем товар",
    "Товар отправлен — ждём приёмку",
    "УПД отправлен — ждём подписание заказчиком",
    "УПД подписан — счёт отправлен — ждём оплату",
})


@dataclass(frozen=True)
class CleanupPlan:
    rows_by_sheet: dict[str, tuple[int, ...]]
    expired_purchase_ids: frozenset[str]
    protected_purchase_ids: frozenset[str]
    unrecognized_purchase_ids: frozenset[str]

    @property
    def deleted_purchases(self) -> int:
        return len(self.expired_purchase_ids)

    @property
    def deleted_rows(self) -> int:
        return sum(len(rows) for rows in self.rows_by_sheet.values())


def parse_moscow_deadline(value: Any) -> datetime | None:
    """Даты без часового пояса из пользовательской таблицы считаются московскими."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {"нет данных", "none", "null", "nan"}:
        return None
    parsed = None
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
            break
        except ValueError:
            pass
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(MOSCOW)


def _group_rows(rows: list[list[Any]], id_column: int) -> dict[str, list[tuple[int, list[Any]]]]:
    groups: dict[str, list[tuple[int, list[Any]]]] = {}
    for sheet_row, row in enumerate(rows[1:], 2):
        purchase_id = str(row[id_column]).strip() if len(row) > id_column else ""
        if purchase_id:
            groups.setdefault(purchase_id, []).append((sheet_row, row))
    return groups


def plan_expired_cleanup(
    rows_by_sheet: dict[str, list[list[Any]]],
    now: datetime | None = None,
) -> CleanupPlan:
    """Строит план удаления без изменения Google Sheets."""
    current = now or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    else:
        current = current.astimezone(MOSCOW)

    rows_to_delete = {ACTIVE: [], MANUAL: [], LOCKED: []}
    expired: set[str] = set()
    protected: set[str] = set()
    unrecognized: set[str] = set()

    for title in (ACTIVE, MANUAL, LOCKED):
        sheet_rows = rows_by_sheet.get(title, [])
        headers = sheet_rows[0] if sheet_rows else []
        if "ID закупки" not in headers or "Крайний срок подачи заявки" not in headers:
            continue
        id_column = headers.index("ID закупки")
        deadline_column = headers.index("Крайний срок подачи заявки")
        status_column = headers.index("Статус закупки") if "Статус закупки" in headers else None
        groups = _group_rows(sheet_rows, id_column=id_column)
        for purchase_id, entries in groups.items():
            # Защищённый статус в «Актуальных» сохраняет этот ID во всех
            # рабочих вкладках, если он по ошибке оказался более чем в одной.
            if purchase_id in protected:
                continue
            if title == ACTIVE and status_column is not None and any(
                len(row) > status_column and str(row[status_column]).strip() in PROTECTED_STATUSES
                for _, row in entries
            ):
                protected.add(purchase_id)
                continue
            deadlines = [parse_moscow_deadline(row[deadline_column] if len(row) > deadline_column else None)
                         for _, row in entries]
            # Любая отсутствующая/битая дата защищает всю закупку от удаления.
            if not deadlines or any(deadline is None for deadline in deadlines):
                unrecognized.add(purchase_id)
                continue
            if all(deadline <= current for deadline in deadlines if deadline is not None):
                expired.add(purchase_id)
                rows_to_delete[title].extend(sheet_row for sheet_row, _ in entries)

    return CleanupPlan(
        rows_by_sheet={title: tuple(sorted(rows, reverse=True)) for title, rows in rows_to_delete.items()},
        expired_purchase_ids=frozenset(expired),
        protected_purchase_ids=frozenset(protected),
        unrecognized_purchase_ids=frozenset(unrecognized),
    )


def cleanup_expired_purchases(book, now: datetime | None = None, dry_run: bool = False) -> CleanupPlan:
    """Удаляет строки одним пакетом; вкладку «📊 Отчёт» даже не читает."""
    worksheets = {title: book.worksheet(title) for title in (ACTIVE, MANUAL, LOCKED)}
    values = {title: worksheet.get_all_values() for title, worksheet in worksheets.items()}
    plan = plan_expired_cleanup(values, now=now)
    if dry_run or not plan.deleted_rows:
        return plan
    requests = []
    for title, rows in plan.rows_by_sheet.items():
        sheet_id = worksheets[title].id
        for row in rows:
            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row - 1,
                        "endIndex": row,
                    }
                }
            })
    book.batch_update({"requests": requests})
    return plan
