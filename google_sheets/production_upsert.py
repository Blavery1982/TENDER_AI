"""Upsert одного production payload без перезаписи ручных колонок."""
from __future__ import annotations

from typing import Any

from google_sheets.client import (PRESERVED_ACTIVE_HEADERS, _env, _key_part,
                                  authorize_service_account)
from google_sheets.workbook import (ACTIVE, ACTIVE_HEADERS, CALCULATION_HEADERS,
                                    NO, clear_data_validation_requests, date,
                                    formulas)

EXTRA_HEADERS = [
    "Текущий итог просчета и анализа",
    "КРАТКИЙ АНАЛИЗ ЗАКУПКИ", "РИСКИ", "РЕЗУЛЬТАТ АНАЛИЗА КОНТРАКТА",
    "ВЫБРАННАЯ МОДЕЛЬ", "СТАТУС СООТВЕТСТВИЯ ТЗ", "КОММЕНТАРИЙ ПО СООТВЕТСТВИЮ",
    "ПОСТАВЩИК №1", "ПРОВЕРКА ПОСТАВЩИКА №1", "РИСКИ ПОСТАВЩИКА №1",
    "ПОСТАВЩИК №2", "ПРОВЕРКА ПОСТАВЩИКА №2", "РИСКИ ПОСТАВЩИКА №2",
    "ПОСТАВЩИК №3", "ПРОВЕРКА ПОСТАВЩИКА №3", "РИСКИ ПОСТАВЩИКА №3",
    "МИНИМАЛЬНАЯ ПОДТВЕРЖДЁННАЯ ЦЕНА, ₽", "ПОЗВОНИТЬ / ЗАПРОСИТЬ ЦЕНУ",
    "ПРЕДУПРЕЖДЕНИЯ", "ЛОГИСТИКА (БУДУЩИЙ РАСЧЁТ)",
    "РЕЖИМ ПОИСКА МОДЕЛИ", "МОДЕЛЬ ЗАКАЗЧИКА", "ЭКВИВАЛЕНТ РАЗРЕШЁН",
    "ПОЛНЫЕ АНАЛОГИ ПО ТЗ", "НМЦК МИНУС КОМИССИЯ ЕАТ, ₽",
    "ПРЕДВАРИТЕЛЬНЫЙ ЗАПАС МАРЖИ ДО ЛОГИСТИКИ, ₽",
]


def _text(value: Any) -> str:
    return "" if value in (None, "") else str(value)


def _date(value: Any) -> str:
    return "" if value in (None, "") else date(value)


def _enum_text(value: Any) -> str:
    """В пользовательскую колонку enum никогда не выводится внутренним кодом."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if text and not text.isdigit() else ""


def _has_manual_value(value: Any) -> bool:
    """«Нет данных» — старый placeholder, а не ручное пользовательское значение."""
    return value not in (None, "", NO)


def row_values(payload: dict[str, Any], headers: list[str]) -> list[Any]:
    p, item = payload["procurement"], payload["item"]
    audit, model = payload["audit"], payload["model"]
    trace, search = payload["traceability"], payload["supplier_search"]
    economics = payload.get("economics") or {}
    contacts = p.get("contact") or {}
    offers = search.get("confirmed_offers") or []
    compliance_comment = (
        _text(model.get("compliance_reason"))
        if model.get("compliance_required") is False
        else f"Подтверждено: {model.get('requirements_confirmed')}; не подтверждено: {model.get('requirements_unconfirmed')}; несоответствий: {model.get('requirements_mismatched')}"
    )
    values = {header: "" for header in headers}
    for header in CALCULATION_HEADERS:
        if header in values:
            values[header] = ""
    values.update({
        "Крайний срок подачи заявки": _date(p.get("deadline")),
        "Номер закупки": _text(p.get("trade_number")), "ID закупки": _text(p.get("id")),
        "Ссылка на закупку": _text(p.get("url")), "Источник закупки": "ЕАТ «Берёзка»",
        "Наименование закупки в извещении": _text(p.get("subject")),
        "Место поставки": _text(p.get("delivery_address")),
        "Срок поставки": (f"{p['delivery_period']} рабочих дней" if p.get("delivery_period") and p.get("delivery_working_days")
                          else f"{p['delivery_period']} календарных дней" if p.get("delivery_period") else ""),
        "Вид оплаты": _enum_text(p.get("payment_type")), "НМЦК, ₽": p.get("nmck"),
        "Заказчик": _text((audit.get("customer_check") or {}).get("customer_name")),
        "ИНН заказчика": _text((audit.get("customer_check") or {}).get("customer_inn")),
        "Контакты заказчика": "; ".join(filter(None, (contacts.get("phone"), contacts.get("email")))),
        "Комиссия площадки, ₽": p.get("commission_fee"), "№ позиции": item.get("position_number"),
        "Название позиции (ТЗ)": _text(item.get("display_name")), "Код ОКПД2": _text(item.get("okpd2_code")),
        "Код ЕАТ": _text(item.get("eat_code")), "Количество": item.get("quantity"),
        "Единица измерения": _text(item.get("unit")), "Цена заказчика за единицу, ₽": item.get("customer_unit_price"),
        "Сумма позиции, ₽": item.get("sum"), "ОСОБЫЕ УСЛОВИЯ": _text(audit.get("special_conditions")),
        "ПРОСЛЕЖИВАЕМОСТЬ": _text(trace.get("traceability_status")),
        "КРАТКИЙ АНАЛИЗ ЗАКУПКИ": f"Товарная закупка; документов обработано: {payload['documents']['processed']}; требований ТЗ: {audit.get('requirements_count')}",
        "Текущий итог просчета и анализа": _text(payload.get("current_analysis_result")),
        "РИСКИ": "; ".join(payload.get("warnings") or []) or "Явные риски не выявлены",
        "РЕЗУЛЬТАТ АНАЛИЗА КОНТРАКТА": _text(audit.get("contract_analysis")),
        "ВЫБРАННАЯ МОДЕЛЬ": _text(model.get("selected_model")),
        "СТАТУС СООТВЕТСТВИЯ ТЗ": _text(model.get("compliance_status")),
        "КОММЕНТАРИЙ ПО СООТВЕТСТВИЮ": compliance_comment,
        "МИНИМАЛЬНАЯ ПОДТВЕРЖДЁННАЯ ЦЕНА, ₽": search.get("minimum_confirmed_price"),
        "ПОЗВОНИТЬ / ЗАПРОСИТЬ ЦЕНУ": "; ".join(f"{x.get('supplier_name')}: {x.get('product_url')}" for x in search.get("suppliers_for_call") or []),
        "ПРЕДУПРЕЖДЕНИЯ": "; ".join(payload.get("warnings") or []) or "Нет предупреждений",
        "ЛОГИСТИКА (БУДУЩИЙ РАСЧЁТ)": "Не рассчитана",
        "РЕЖИМ ПОИСКА МОДЕЛИ": "EXACT MODEL" if model.get("search_mode") == "EXACT_MODEL_ONLY" else "MODEL DISCOVERY",
        "МОДЕЛЬ ЗАКАЗЧИКА": _text(model.get("customer_model") or model.get("selected_model")),
        "ЭКВИВАЛЕНТ РАЗРЕШЁН": "Да" if model.get("equivalent_allowed") else "Нет",
        "ПОЛНЫЕ АНАЛОГИ ПО ТЗ": _text(model.get("full_analogs_note")),
        "НМЦК МИНУС КОМИССИЯ ЕАТ, ₽": economics.get("nmck_after_eat_commission"),
        "ПРЕДВАРИТЕЛЬНЫЙ ЗАПАС МАРЖИ ДО ЛОГИСТИКИ, ₽": economics.get("preliminary_margin_before_logistics"),
    })
    for number in range(1, 4):
        offer = offers[number - 1] if len(offers) >= number else {}
        values[f"ПОСТАВЩИК №{number}"] = _text(offer.get("supplier_name"))
        values[f"ПРОВЕРКА ПОСТАВЩИКА №{number}"] = _text(offer.get("verification_status"))
        values[f"РИСКИ ПОСТАВЩИКА №{number}"] = "; ".join(offer.get("risk_flags") or []) or ("Нет явных рисков" if offer else "")
    # Существующие КП-поля используются по назначению; ручные значения при повторном upsert сохраняются.
    for number, (price_header, link_header) in enumerate((("Цена КП 1", "Ссылка на товар КП 1"),
                                                          ("Цена КП 2", "Ссылка на товар КП 2"),
                                                          ("Цена КП 3", "Ссылка на товар КП 3")), 1):
        if len(offers) >= number:
            values[price_header] = offers[number - 1].get("public_price")
            values[link_header] = offers[number - 1].get("product_url")
    return ["" if values.get(header) is None else values.get(header, "") for header in headers]


def upsert_live_payload(payload: dict[str, Any]) -> dict[str, Any]:
    client, email = authorize_service_account()
    book = client.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
    ws = book.worksheet(ACTIVE)
    current = ws.get_all_values(value_render_option="FORMULA")
    existing_headers = current[0] if current else []
    headers = list(existing_headers or ACTIVE_HEADERS)
    if len(headers) != len(set(headers)):
        raise ValueError("В строке 1 есть повторяющиеся названия колонок")
    for header in EXTRA_HEADERS:
        if header not in headers:
            headers.append(header)
    if ws.col_count < len(headers):
        ws.add_cols(len(headers) - ws.col_count)
    if headers != existing_headers:
        ws.update([headers], "A1", value_input_option="RAW")
    # «ВЫБРАННАЯ МОДЕЛЬ» всегда принимает произвольный текст. Снимаем любое
    # унаследованное правило по заголовку при каждом production-upsert.
    validation_requests = clear_data_validation_requests(ws.id, headers)
    if validation_requests:
        book.batch_update({"requests": validation_requests})
    source = row_values(payload, headers)
    key = (_key_part(payload["procurement"]["id"]), _key_part(payload["item"]["position_number"]))
    id_index = headers.index("ID закупки")
    item_index = headers.index("№ позиции")
    row_number = None
    for number, row in enumerate(current[1:], 2):
        if (len(row) > max(id_index, item_index)
                and (_key_part(row[id_index]), _key_part(row[item_index])) == key):
            row_number = number; break
    if row_number is None:
        row_number = max(2, len(current) + 1)
        if "Статус закупки" in headers:
            source[headers.index("Статус закупки")] = "Новая — нужен просчёт"
    else:
        old = current[row_number - 1] if row_number - 1 < len(current) else []
        for header in PRESERVED_ACTIVE_HEADERS:
            if header not in headers:
                continue
            index = headers.index(header)
            if index < len(old) and _has_manual_value(old[index]):
                source[index] = old[index]
    for index, formula in formulas(row_number, headers).items():
        if index < len(source):
            source[index] = formula
    ws.update([source], f"A{row_number}", value_input_option="USER_ENTERED")
    # Телефон, ID, коды и URL повторно записываем RAW, чтобы +7 не стал формулой.
    raw_updates = []
    for header in ("Номер закупки", "ID закупки", "Ссылка на закупку", "ИНН заказчика",
                   "Контакты заказчика", "Код ОКПД2", "Код ЕАТ", "ВЫБРАННАЯ МОДЕЛЬ",
                   "Ссылка на товар КП 1",
                   "Ссылка на товар КП 2", "Ссылка на товар КП 3", "ПОЗВОНИТЬ / ЗАПРОСИТЬ ЦЕНУ"):
        if header in headers:
            column = headers.index(header) + 1
            from gspread.utils import rowcol_to_a1
            raw_updates.append({"range": rowcol_to_a1(row_number, column),
                                "values": [[str(source[column - 1])]]})
    if raw_updates:
        ws.batch_update(raw_updates, value_input_option="RAW")
    return {"spreadsheet_url": book.url, "worksheet": ACTIVE, "row": row_number,
            "service_account_email": email, "key": list(key)}
