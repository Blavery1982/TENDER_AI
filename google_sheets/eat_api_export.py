"""Выгрузка списка закупок из API сайта ЕАТ в текущую Google-таблицу."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from eat.live_collection import COLLECTION_PATH
from eat.public_api_collection import run_public_api_collection_audit
from google_sheets.client import (
    PRESERVED_ACTIVE_HEADERS,
    _env,
    _upsert,
    authorize_service_account,
    setup_structure,
)
from google_sheets.workbook import (
    ACTIVE,
    ACTIVE_HEADERS,
    MANUAL,
    MANUAL_HEADERS,
    REPORT,
    REPORT_HEADERS,
    NO,
    date,
    link,
    val,
)


ROOT = Path(__file__).resolve().parent.parent
EXPORT_REPORT_PATH = ROOT / "data" / "eat_api_google_sheets_export.json"


def _address(raw: dict[str, Any]) -> tuple[str, str]:
    addresses = raw.get("deliveryInfos") or []
    full: list[str] = []
    regions: list[str] = []
    for info in addresses:
        address = info.get("deliveryAddress") if isinstance(info, dict) else None
        if not isinstance(address, dict):
            continue
        if address.get("formattedFullInfo"):
            full.append(str(address["formattedFullInfo"]))
        if address.get("regionName"):
            regions.append(str(address["regionName"]))
    return "; ".join(dict.fromkeys(full)) or NO, "; ".join(dict.fromkeys(regions)) or NO


def _customer(raw: dict[str, Any]) -> tuple[str, str, str]:
    customer = raw.get("organizerInfo") or {}
    if not isinstance(customer, dict):
        return NO, NO, NO
    contacts = "; ".join(
        str(value) for value in (customer.get("phoneNumber"), customer.get("email"))
        if value
    )
    return (
        str(customer.get("name") or customer.get("fullName") or NO),
        str(customer.get("inn") or customer.get("INN") or NO),
        contacts or NO,
    )


def _active_row(audited: dict[str, Any]) -> list[Any]:
    raw = audited["raw"]
    items = raw.get("lotItems") or [{}]
    headers = {header: index for index, header in enumerate(ACTIVE_HEADERS)}
    rows: list[list[Any]] = []
    address, _region = _address(raw)
    customer_name, customer_inn, contacts = _customer(raw)
    for position, item in enumerate(items, 1):
        item = item if isinstance(item, dict) else {}
        quantity = item.get("quantity")
        unit_price = None
        if isinstance(quantity, (int, float)) and quantity and isinstance(raw.get("price"), (int, float)):
            unit_price = raw["price"] / quantity
        row = [""] * len(ACTIVE_HEADERS)

        def put(name: str, value: Any) -> None:
            row[headers[name]] = value if value not in (None, "") else NO

        put("Крайний срок подачи заявки", date(raw.get("applicationFillingEndDate")))
        put("Номер закупки", val(raw.get("tradeNumber")))
        put("ID закупки", val(raw.get("id")))
        purchase_url = raw.get("url")
        if not purchase_url and raw.get("id"):
            purchase_url = (
                "https://agregatoreat.ru/purchases/announcement/"
                f"{raw['id']}/info"
            )
        put("Ссылка на закупку", link(purchase_url))
        put("Источник закупки", "ЕАТ «Берёзка» (официальный API)")
        put("Наименование закупки в извещении", val(raw.get("subject")))
        put("Место поставки", address)
        put("Срок поставки", val(raw.get("deliveryDate")))
        put("Вид оплаты", NO)
        put("НМЦК, ₽", raw.get("price"))
        put("Заказчик", customer_name)
        put("ИНН заказчика", customer_inn)
        put("Контакты заказчика", contacts)
        put("Комиссия площадки, ₽", round(raw["price"] * 0.03, 2) if isinstance(raw.get("price"), (int, float)) else None)
        put("№ позиции", position)
        put("Название позиции (ТЗ)", item.get("description") or item.get("name"))
        put("Код ОКПД2", item.get("okpd2"))
        put("Код ЕАТ", item.get("eatCode"))
        put("Количество", quantity)
        put("Единица измерения", item.get("unit"))
        put("Цена заказчика за единицу, ₽", unit_price)
        put("Сумма позиции, ₽", raw.get("price") if len(items) == 1 else None)
        put("ОСОБЫЕ УСЛОВИЯ", "Нет")
        put("ПРОСЛЕЖИВАЕМОСТЬ", "Не подлежит по имеющимся данным")
        put("Статус закупки", "Новая — нужен просчёт")
        rows.append(row)
    return rows


def _manual_row(audited: dict[str, Any]) -> list[Any]:
    raw = audited["raw"]
    _address_text, region = _address(raw)
    reason = "; ".join(audited.get("filter", {}).get("rejection_reasons") or []) or "Требуется ручная проверка"
    values = {
        "Крайний срок подачи заявки": date(raw.get("applicationFillingEndDate")),
        "Номер закупки": val(raw.get("tradeNumber")),
        "ID закупки": val(raw.get("id")),
        "Ссылка на закупку": link(raw.get("url")),
        "Наименование закупки": val(raw.get("subject")),
        "НМЦК, ₽": raw.get("price") if raw.get("price") is not None else NO,
        "Регион": region,
        "Причина ручной проверки": reason,
    }
    return [values.get(header, "") for header in MANUAL_HEADERS]


def run_api_google_sheets_export(*, detail_limit: int | None = None, refresh: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    if refresh:
        collection_report = run_public_api_collection_audit()
    else:
        existing = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))
        collection_report = existing["metadata"]
    collection = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))
    audited_purchases = collection.get("purchases") or []
    selected = audited_purchases[:detail_limit] if detail_limit is not None else audited_purchases
    audit = {"purchases": selected, "counts": collection_report["filter_audit"]}
    active_rows = [ACTIVE_HEADERS]
    manual_rows = [MANUAL_HEADERS]
    for audited in audit["purchases"]:
        if audited["filter_result"] == "passed":
            active_rows.extend(_active_row(audited))
        elif audited["filter_result"] == "manual_check":
            manual_rows.append(_manual_row(audited))

    spreadsheet_url, _ = setup_structure()
    gclient, service_account_email = authorize_service_account()
    book = gclient.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
    _upsert(book.worksheet(ACTIVE), active_rows, ("ID закупки", "№ позиции"), PRESERVED_ACTIVE_HEADERS)
    _upsert(book.worksheet(MANUAL), manual_rows, ("ID закупки",))

    now = datetime.now(ZoneInfo("Europe/Moscow"))
    counts = audit["counts"]
    references_count = collection_report["pagination"]["unique_count"]
    report_row = [
        now.strftime("%d.%m.%Y"), now.strftime("%H:%M:%S"), references_count,
        references_count, 0, NO, NO, NO, NO, NO, NO,
        counts["deadline_unknown"], counts["passed_filter_v2"],
        counts["rejected_filter_v2"], counts["priority_1"] + counts["priority_2"],
        0, 0, counts["passed_filter_v2"], 0, 0, 0, 0, 0, 0, 0, 0,
    ]
    report = book.worksheet(REPORT)
    if report.col_count < len(REPORT_HEADERS):
        report.add_cols(len(REPORT_HEADERS) - report.col_count)
    if not report.row_values(1):
        report.update([REPORT_HEADERS], "A1", value_input_option="RAW")
    report.append_row(report_row, value_input_option="USER_ENTERED")

    result = {
        "spreadsheet_url": spreadsheet_url,
        "service_account_email": service_account_email,
        "references_count": references_count,
        "details_requested": len(selected),
        "active_rows_written": len(active_rows) - 1,
        "manual_rows_written": len(manual_rows) - 1,
        "filter_counts": counts,
        "endpoint_mode": "playwright_api_request_context",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    EXPORT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
