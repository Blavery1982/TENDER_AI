"""Подключение Google Sheets только через существующий Service Account."""
from __future__ import annotations
import os
from pathlib import Path

import sys
import types

# gspread импортирует OAuth-модуль даже для Service Account. OAuth-пакет намеренно
# не установлен; безопасная заглушка исключает случайный запуск OAuth-сценария.
try:
    import gspread
except ModuleNotFoundError as error:
    if error.name != "google_auth_oauthlib":
        raise
    oauth_package = types.ModuleType("google_auth_oauthlib")
    oauth_flow = types.ModuleType("google_auth_oauthlib.flow")
    class _OAuthDisabled:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OAuth отключён: используется только Service Account")
    oauth_flow.InstalledAppFlow = _OAuthDisabled
    oauth_package.flow = oauth_flow
    sys.modules["google_auth_oauthlib"] = oauth_package
    sys.modules["google_auth_oauthlib.flow"] = oauth_flow
    import gspread
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

from google_sheets.workbook import ACTIVE, ACTIVE_HEADERS, LOCKED, LOCKED_HEADERS, MANUAL, MANUAL_HEADERS, REPORT, REPORT_HEADERS, _column_letter, build as build_workbook, clear_data_validation_requests, formulas, NO
from gspread.utils import ValidationConditionType, rowcol_to_a1

ROOT = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _env(name: str, default: str | None = None) -> str | None:
    if name in os.environ:
        return os.environ[name]
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip("\"'")
    return default


def authorize_service_account() -> tuple[gspread.Client, str]:
    key_path = ROOT / str(_env("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/gcloud_key.json"))
    if not key_path.is_file():
        raise FileNotFoundError(f"Не найден ключ Service Account: {key_path}")
    credentials = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    credentials.refresh(Request())  # Проверяет Google-авторизацию, не открывая таблицы.
    return gspread.authorize(credentials), credentials.service_account_email


def sync() -> str:
    """Повторно выгрузить последнюю live-MVP обработку по стабильному ключу."""
    import json

    latest = ROOT / "data" / "mvp_exact_batch_latest.json"
    if not latest.is_file():
        raise FileNotFoundError(
            f"Нет последнего live-MVP результата для синхронизации: {latest}"
        )
    from google_sheets.production_upsert import upsert_live_payload
    from pipeline.mvp_exact_batch import _sheet_payload

    result = json.loads(latest.read_text(encoding="utf-8"))
    receipts = [upsert_live_payload(_sheet_payload(item))
                for item in result.get("items") or []]
    if not receipts:
        return "Последний запуск не содержит обработанных позиций"
    return f"{receipts[0]['spreadsheet_url']}; записано строк: {len(receipts)}"


STATUSES = ["Новая — нужен просчёт","Просчёт в работе","КП готово — ждём подачи","Подача сделана — ждём результаты","Не победили","Победили — ждём подписание","Подписали — ждём подписание заказчиком","Контракт подписан обеими сторонами — отправляем товар","Товар заказан","Товар отправлен — ждём приёмку","Товар принят заказчиком","УПД отправлен — ждём подписание заказчиком","УПД подписан — счёт отправлен — ждём оплату","Оплачено","Отменено"]


def setup_structure() -> tuple[str, dict[str, int]]:
    spreadsheet_id=_env("GOOGLE_SPREADSHEET_ID")
    if not spreadsheet_id: raise RuntimeError("В .env не указан GOOGLE_SPREADSHEET_ID")
    client,_=authorize_service_account(); book=client.open_by_key(spreadsheet_id)
    definitions={ACTIVE:ACTIVE_HEADERS,LOCKED:LOCKED_HEADERS,MANUAL:MANUAL_HEADERS,REPORT:REPORT_HEADERS}
    sheets={w.title:w for w in book.worksheets()}
    actual_headers={}
    for title,required_headers in definitions.items():
        ws=sheets.get(title) or book.add_worksheet(title=title,rows=10000,cols=len(required_headers))
        existing=ws.row_values(1)
        headers=list(existing) if existing else list(required_headers)
        headers.extend(name for name in required_headers if name not in headers)
        if len(headers)!=len(set(headers)):raise ValueError(f"На листе {title} есть повторяющиеся заголовки")
        if ws.col_count < len(headers): ws.add_cols(len(headers)-ws.col_count)
        if headers!=existing:ws.update([headers],"A1",value_input_option="RAW")
        last=_column_letter(len(headers)-1)
        ws.freeze(rows=1); ws.set_basic_filter(f"A1:{last}10000")
        ws.format("1:1",{"textFormat":{"bold":True},"backgroundColor":{"red":0.85,"green":0.92,"blue":1},"wrapStrategy":"WRAP","verticalAlignment":"MIDDLE"})
        ws.format(f"A2:{last}10000",{"wrapStrategy":"WRAP","verticalAlignment":"TOP"})
        sheets[title]=ws; actual_headers[title]=headers
    status_col=_column_letter(actual_headers[ACTIVE].index("Статус закупки"))
    sheets[ACTIVE].add_validation(f"{status_col}2:{status_col}10000",ValidationConditionType.one_of_list,STATUSES,strict=True,showCustomUi=True,inputMessage="Выберите статус закупки")
    requests=[]
    for title,ws in sheets.items():
        if title not in definitions: continue
        width=140 if title!=ACTIVE else 130
        requests.append({"updateDimensionProperties":{"range":{"sheetId":ws.id,"dimension":"COLUMNS","startIndex":0,"endIndex":len(actual_headers[title])},"properties":{"pixelSize":width},"fields":"pixelSize"}})
    sid=sheets[ACTIVE].id
    requests.extend(clear_data_validation_requests(sid, actual_headers[ACTIVE]))
    red={"textFormat":{"foregroundColor":{"red":0.8}},"backgroundColor":{"red":1,"green":0.85,"blue":0.85}}
    green={"textFormat":{"foregroundColor":{"green":0.45}},"backgroundColor":{"red":0.85,"green":1,"blue":0.85}}
    active_headers=actual_headers[ACTIVE]
    special_col=_column_letter(active_headers.index("ОСОБЫЕ УСЛОВИЯ")); trace_col=_column_letter(active_headers.index("ПРОСЛЕЖИВАЕМОСТЬ")); quality_col=_column_letter(active_headers.index("Качество просчёта"))
    for name,formula in [("ОСОБЫЕ УСЛОВИЯ",f'=AND(${special_col}2<>"Нет";${special_col}2<>"")'),("ПРОСЛЕЖИВАЕМОСТЬ",f'=AND(${trace_col}2<>"Не подлежит по имеющимся данным";${trace_col}2<>"")'),("Качество просчёта",f'=LEFT(${quality_col}2;17)="Продолжить поиск"')]:
        col=active_headers.index(name)
        requests.append({"addConditionalFormatRule":{"rule":{"ranges":[{"sheetId":sid,"startRowIndex":1,"endRowIndex":10000,"startColumnIndex":col,"endColumnIndex":col+1}],"booleanRule":{"condition":{"type":"CUSTOM_FORMULA","values":[{"userEnteredValue":formula}]},"format":red}},"index":0}})
    quality_index=active_headers.index("Качество просчёта")
    requests.append({"addConditionalFormatRule":{"rule":{"ranges":[{"sheetId":sid,"startRowIndex":1,"endRowIndex":10000,"startColumnIndex":quality_index,"endColumnIndex":quality_index+1}],"booleanRule":{"condition":{"type":"TEXT_EQ","values":[{"userEnteredValue":"Просчёт принят"}]},"format":green}},"index":0}})
    book.batch_update({"requests":requests})
    apply_text_formats(book)
    default=sheets.get("Лист1")
    if default and len(book.worksheets())>1 and not default.get_all_values(): book.del_worksheet(default)
    return book.url,{title:len(headers) for title,headers in actual_headers.items()}


PRESERVED_ACTIVE_HEADERS = {
    "Дополнительные расходы, ₽", "Цена КП 1", "Ссылка на товар КП 1",
    "Ссылка на счёт КП 1", "Цена КП 2", "Ссылка на товар КП 2",
    "Ссылка на счёт КП 2", "Цена КП 3", "Ссылка на товар КП 3",
    "Ссылка на счёт КП 3", "Цена КП 4", "Ссылка на товар КП 4",
    "Ссылка на счёт КП 4", "Статус закупки",
}
MANUAL_COLUMNS = {ACTIVE_HEADERS.index(name) for name in PRESERVED_ACTIVE_HEADERS}
TEXT_HEADERS = {
    ACTIVE: {
        "Номер закупки", "ID закупки", "Ссылка на закупку", "Источник закупки",
        "Наименование закупки в извещении", "Место поставки", "Срок поставки",
        "Вид оплаты", "Заказчик", "ИНН заказчика", "Контакты заказчика",
        "Название позиции (ТЗ)", "Код ОКПД2", "Код ЕАТ", "Единица измерения",
        "ОСОБЫЕ УСЛОВИЯ", "ПРОСЛЕЖИВАЕМОСТЬ", "Ссылка на товар КП 1",
        "Текущий итог просчета и анализа",
        "ВЫБРАННАЯ МОДЕЛЬ",
        "Ссылка на счёт КП 1", "Ссылка на товар КП 2", "Ссылка на счёт КП 2",
        "Ссылка на товар КП 3", "Ссылка на счёт КП 3", "Ссылка на товар КП 4",
        "Ссылка на счёт КП 4", "Качество просчёта", "Статус закупки",
    },
    LOCKED: {"Осталось времени", "ID закупки", "Ссылка на закупку", "Источник закупки", "Статус"},
    MANUAL: {"Номер закупки", "ID закупки", "Ссылка на закупку", "Наименование закупки", "Регион", "Причина ручной проверки"},
}

# Только автоматически формируемые текстовые поля. Пользовательские поля КП
# форматируются как текст, но их содержимое здесь никогда не перезаписывается.
RAW_TEXT_HEADERS = TEXT_HEADERS

def apply_text_formats(book) -> None:
    for title, text_headers in TEXT_HEADERS.items():
        worksheet = book.worksheet(title)
        rows = worksheet.get("1:1", value_render_option="FORMULA")
        headers = rows[0] if rows else []
        ranges = [f"{rowcol_to_a1(2, headers.index(name) + 1)}:{rowcol_to_a1(10000, headers.index(name) + 1)}"
                  for name in text_headers if name in headers]
        worksheet.batch_format([
            {"range": cell_range, "format": {"numberFormat": {"type": "TEXT"}}}
            for cell_range in ranges
        ])

def _key_part(value) -> str:
    text=str(value).strip()
    return text[:-2] if text.endswith('.0') and text[:-2].isdigit() else text

def _upsert(ws, incoming: list[list], key_headers: tuple[str, ...], preserve: set[str] | None = None) -> None:
    preserve = preserve or set()
    current = ws.get_all_values(value_render_option="FORMULA")
    sheet_headers = current[0] if current else list(incoming[0])
    incoming_headers = incoming[0]
    if len(sheet_headers) != len(set(sheet_headers)):
        raise ValueError(f"На листе {ws.title} есть повторяющиеся названия колонок")
    key_columns = tuple(sheet_headers.index(name) for name in key_headers)
    keys = {
        tuple(_key_part(row[i]) if i < len(row) else "" for i in key_columns): number
        for number, row in enumerate(current[1:], 2)
        if all(i < len(row) and row[i] != "" for i in key_columns)
    }
    updates = []
    next_row = max(2, len(current) + 1)
    for incoming_row in incoming[1:]:
        source_values = dict(zip(incoming_headers, incoming_row))
        source = [source_values.get(header, "") for header in sheet_headers]
        key = tuple(_key_part(source[sheet_headers.index(name)]) for name in key_headers)
        target = keys.get(key)
        if target is None:
            target = next_row; next_row += 1; keys[key] = target
            if ws.title == ACTIVE and "Статус закупки" in sheet_headers:
                source[sheet_headers.index("Статус закупки")] = "Новая — нужен просчёт"
        else:
            old = current[target - 1] if target - 1 < len(current) else []
            for header in preserve:
                if header not in sheet_headers:
                    continue
                index = sheet_headers.index(header)
                if index < len(old) and old[index] not in ("", NO): source[index] = old[index]
        if ws.title == ACTIVE:
            for index, formula in formulas(target, sheet_headers).items(): source[index] = formula
        updates.append({"range": f"A{target}", "values": [source]})
    if updates: ws.batch_update(updates, value_input_option="USER_ENTERED")


def _rewrite_text_as_raw(ws, incoming: list[list], key_headers: tuple[str, ...]) -> None:
    """Повторно записывает текст без интерпретации +7, кодов и URL как формул."""
    current = ws.get_all_values(value_render_option="FORMULA")
    sheet_headers = current[0] if current else []
    incoming_headers = incoming[0]
    key_columns = tuple(sheet_headers.index(name) for name in key_headers)
    rows = {
        tuple(_key_part(row[i]) if i < len(row) else "" for i in key_columns): number
        for number, row in enumerate(current[1:], 2)
        if all(i < len(row) and row[i] != "" for i in key_columns)
    }
    updates = []
    for incoming_row in incoming[1:]:
        source_values = dict(zip(incoming_headers, incoming_row))
        source = [source_values.get(header, "") for header in sheet_headers]
        target = rows.get(tuple(_key_part(source[sheet_headers.index(name)]) for name in key_headers))
        if target is None:
            continue
        for header in RAW_TEXT_HEADERS[ws.title]:
            if header not in sheet_headers:
                continue
            column = sheet_headers.index(header)
            value = source[column] if column < len(source) else ""
            updates.append({
                "range": rowcol_to_a1(target, column + 1),
                "values": [[str(value) if value is not None else ""]],
            })
    if updates:
        ws.batch_update(updates, value_input_option="RAW")


def fix_text_formatting() -> dict[str, object]:
    """Исправляет формат и текущие значения, не затрагивая расчётные колонки."""
    client, _ = authorize_service_account()
    book = client.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
    payload = build_workbook()
    apply_text_formats(book)

    definitions = (
        (ACTIVE, ("ID закупки", "№ позиции"), PRESERVED_ACTIVE_HEADERS),
        (LOCKED, ("ID закупки",), None),
        (MANUAL, ("ID закупки",), None),
    )
    for title, key_columns, preserve in definitions:
        worksheet = book.worksheet(title)
        _upsert(worksheet, payload[title], key_columns, preserve)
        _rewrite_text_as_raw(worksheet, payload[title], key_columns)

    active = book.worksheet(ACTIVE)
    active_headers=active.row_values(1)
    contact_col=_column_letter(active_headers.index("Контакты заказчика"))
    contacts = active.get(f"{contact_col}2:{contact_col}", value_render_option="FORMULA")
    contact_values = [row[0] for row in contacts if row]
    return {
        "text_columns": {title: sorted(headers) for title, headers in TEXT_HEADERS.items()},
        "contacts_checked": len(contact_values),
        "formula_contacts": sum(str(value).startswith("=") for value in contact_values),
        "error_contacts": sum("#ERROR!" in str(value) for value in contact_values),
    }


def deduplicate_active(ws) -> int:
    current=ws.get_all_values(value_render_option="FORMULA"); groups={}
    if not current:return 0
    headers=current[0]; id_col=headers.index("ID закупки"); item_col=headers.index("№ позиции")
    for number,row in enumerate(current[1:],2):
        if len(row)>max(id_col,item_col) and row[id_col] and row[item_col]: groups.setdefault((_key_part(row[id_col]),_key_part(row[item_col])),[]).append((number,row))
    updates=[]; delete=[]
    for entries in groups.values():
        if len(entries)<2: continue
        winner,winner_row=entries[0]; merged=list(winner_row)+['']*(len(headers)-len(winner_row))
        for _,row in entries:
            for header in PRESERVED_ACTIVE_HEADERS:
                if header not in headers:continue
                col=headers.index(header)
                value=row[col] if col<len(row) else ''
                if value not in ('',NO,'Новая — нужен просчёт') or not merged[col]: merged[col]=value
        updates.append({'range':f'A{winner}','values':[merged[:len(headers)]]}); delete.extend(number for number,_ in entries[1:])
    if updates: ws.batch_update(updates,value_input_option='USER_ENTERED')
    if delete:
        ws.client.batch_update(ws.spreadsheet_id,{'requests':[{'deleteDimension':{'range':{'sheetId':ws.id,'dimension':'ROWS','startIndex':row-1,'endIndex':row}}} for row in sorted(delete,reverse=True)]})
    return len(delete)


def _report_row(active_rows: list[list], deleted_expired: int = 0) -> list:
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    root=ROOT; source=json.loads((root/'data/eat_filtered_semantic_v2.json').read_text())
    purchases=source['purchases']; old=json.loads((root/'data/eat_filtered_test.json').read_text())['metadata']
    kinds={k:sum(p.get('procurement_kind')==k for p in purchases) for k in ('goods','services','works','mixed')}
    now=datetime.now(ZoneInfo('Europe/Moscow'))
    active_headers=active_rows[0]; id_col=active_headers.index("ID закупки")
    active_count=len({str(r[id_col]) for r in active_rows[1:] if len(r)>id_col and r[id_col]})
    return [now.strftime('%d.%m.%Y'),now.strftime('%H:%M:%S'),NO,sum(p['filter_result']!='confidential_locked' for p in purchases),sum(p['filter_result']=='confidential_locked' for p in purchases),old['independent_statistics']['price_150_400_thousand'],old['independent_statistics']['region_allowed'],kinds['goods'],kinds['services'],kinds['works'],kinds['mixed'],sum(p['filter_result']=='manual_check' for p in purchases),active_count,sum(p['filter_result']=='rejected' for p in purchases),active_count,1,0,active_count,0,0,0,0,0,0,0,deleted_expired]


def test_export() -> dict[str, object]:
    client,_=authorize_service_account(); book=client.open_by_key(_env('GOOGLE_SPREADSHEET_ID')); payload=build_workbook()
    active=book.worksheet(ACTIVE); locked=book.worksheet(LOCKED); manual=book.worksheet(MANUAL); report=book.worksheet(REPORT)
    from google_sheets.retention import cleanup_expired_purchases
    cleanup = cleanup_expired_purchases(book)
    _upsert(active,payload[ACTIVE],("ID закупки","№ позиции"),PRESERVED_ACTIVE_HEADERS)
    _upsert(locked,payload[LOCKED],("ID закупки",)); _upsert(manual,payload[MANUAL],("ID закупки",))
    test_row=2; sheet_headers=active.row_values(1)
    def address(header):return rowcol_to_a1(test_row,sheet_headers.index(header)+1)
    active.update([[1234.56]],address("Дополнительные расходы, ₽"),value_input_option="USER_ENTERED"); active.update([[111111.11]],address("Цена КП 1"),value_input_option="USER_ENTERED"); active.update([["Просчёт в работе"]],address("Статус закупки"),value_input_option="USER_ENTERED")
    _upsert(active,payload[ACTIVE],("ID закупки","№ позиции"),PRESERVED_ACTIVE_HEADERS)
    preserved=active.row_values(test_row,value_render_option="UNFORMATTED_VALUE")
    manual_ok=(preserved[sheet_headers.index("Дополнительные расходы, ₽")]==1234.56 and preserved[sheet_headers.index("Цена КП 1")]==111111.11 and preserved[sheet_headers.index("Статус закупки")]=="Просчёт в работе")
    _upsert(active,payload[ACTIVE],("ID закупки","№ позиции"),PRESERVED_ACTIVE_HEADERS)
    rows=active.get_all_values(); sheet_headers=rows[0]; id_col=sheet_headers.index("ID закупки"); item_col=sheet_headers.index("№ позиции"); keys=[(r[id_col],r[item_col]) for r in rows[1:] if len(r)>max(id_col,item_col) and r[id_col] and r[item_col]]
    no_duplicates=len(keys)==len(set(keys))==len(payload[ACTIVE])-1
    if report.col_count < len(REPORT_HEADERS): report.add_cols(len(REPORT_HEADERS)-report.col_count)
    report.update([REPORT_HEADERS],"A1",value_input_option="RAW")
    report.append_row(_report_row(payload[ACTIVE],cleanup.deleted_purchases),value_input_option="USER_ENTERED")
    payload_id_col=payload[ACTIVE][0].index("ID закупки")
    return {"active_purchases":len({r[payload_id_col] for r in payload[ACTIVE][1:]}),"active_items":len(payload[ACTIVE])-1,"locked_rows":len(payload[LOCKED])-1,"manual_rows":len(payload[MANUAL])-1,"deleted_expired_purchases":cleanup.deleted_purchases,"manual_fields_preserved":manual_ok,"duplicates_absent":no_duplicates,"report_rows_added":1}
