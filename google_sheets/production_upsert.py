"""Upsert одного production payload без перезаписи ручных колонок."""
from __future__ import annotations

from typing import Any
from calculator.business_decision import decision_text
from calculator.result_decision import attach_business_decision
from google_sheets.kp_schema import KP_HEADERS, ensure_kp_schema
from suppliers.price_search_flow import confirmed_price_ranking
from suppliers.verification import HIGH_RISK, PASSED
from model_search.purchase_category import validate_purchase_category

from google_sheets.client import (PRESERVED_ACTIVE_HEADERS, _env, _key_part,
                                  authorize_service_account, active_row_updates)
from gspread.utils import rowcol_to_a1
from google_sheets.workbook import (ACTIVE, ACTIVE_HEADERS, CALCULATION_HEADERS,
                                    NO, clear_data_validation_requests, date,
                                    formulas)
from google_sheets.workbook import quote_formulas

EXTRA_HEADERS = [
    "Текущий итог просчета и анализа",
    "КРАТКИЙ АНАЛИЗ ЗАКУПКИ", "РИСКИ", "РЕЗУЛЬТАТ АНАЛИЗА КОНТРАКТА",
    "ВЫБРАННАЯ МОДЕЛЬ", "Категория закупки", "СТАТУС СООТВЕТСТВИЯ ТЗ", "КОММЕНТАРИЙ ПО СООТВЕТСТВИЮ",
    "РИСКИ ПОСТАВЩИКА №1", "РИСКИ ПОСТАВЩИКА №2", "РИСКИ ПОСТАВЩИКА №3",
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


def supplier_check_summary(offer: dict[str, Any]) -> str:
    status = offer.get('verification_status')
    if status == HIGH_RISK or str(status or '').startswith('🔴'):
        return '🔴 НЕ ИСПОЛЬЗОВАТЬ'
    if status == PASSED or status == '🟢 ПРОШЁЛ':
        return '🟢 ПРОШЁЛ'
    if any('не найден инн' in str(flag).casefold() for flag in offer.get('risk_flags') or []):
        return '🟡 РУЧНАЯ ПРОВЕРКА — не найден ИНН на сайте'
    return '🟡 РУЧНАЯ ПРОВЕРКА ПЕРЕД ОПЛАТОЙ'


def final_kp_offers(search: dict[str, Any]) -> list[dict[str, Any]]:
    """Mapping принимает только финальные свидетельства текущего поиска."""
    run_id = search.get('price_run_id')
    sources = []
    for offer in search.get('confirmed_offers') or []:
        if (not run_id or offer.get('price_run_id') != run_id or not offer.get('checked_at')
                or offer.get('product_page_available') is not True
                or offer.get('price_confirmed_on_product_page') is not True
                or offer.get('verification_skipped') is True
                or not offer.get('verification_status')
                or supplier_check_summary(offer) == '🔴 НЕ ИСПОЛЬЗОВАТЬ'):
            continue
        sources.append({**offer, 'price': offer.get('public_price'),
                        'exact_model_match': offer.get('exact_model_match', offer.get('exact_model'))})
    # TOP уже прошёл antifraud. Здесь только проверка mapping, без повторного поиска.
    return confirmed_price_ranking(sources)[:3]


def row_values(payload: dict[str, Any], headers: list[str]) -> list[Any]:
    p, item = payload["procurement"], payload["item"]
    audit, model = payload["audit"], payload["model"]
    trace, search = payload["traceability"], payload["supplier_search"]
    economics = payload.get("economics") or {}
    contacts = p.get("contact") or {}
    offers = final_kp_offers(search)
    compliance_comment = (
        _text(model.get("compliance_reason"))
        if model.get("compliance_required") is False
        else f"Подтверждено: {model.get('requirements_confirmed')}; не подтверждено: {model.get('requirements_unconfirmed')}; несоответствий: {model.get('requirements_mismatched')}"
    )
    category = payload.get("purchase_category")
    if category is not None:
        validate_purchase_category(category)
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
        "Категория закупки": category or "",
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
    if len(offers) < 3:
        count = len(offers)
        description = {1: 'актуальное подтверждённое предложение',
                       2: 'актуальных подтверждённых предложения'}.get(count, 'актуальных подтверждённых предложений')
        note = f'Найдено только {count} {description}'
        values['Текущий итог просчета и анализа'] = '; '.join(filter(None, (values['Текущий итог просчета и анализа'], note)))
    values['МИНИМАЛЬНАЯ ПОДТВЕРЖДЁННАЯ ЦЕНА, ₽'] = offers[0]['public_price'] if offers else ''
    for number in range(1, 4):
        offer = offers[number - 1] if len(offers) >= number else {}
        values[f"Поставщик КП {number}"] = _text(offer.get("supplier_name"))
        values[f"Проверка поставщика КП {number}"] = supplier_check_summary(offer) if offer else ''
        values[f"РИСКИ ПОСТАВЩИКА №{number}"] = "; ".join(offer.get("risk_flags") or []) or ("Нет явных рисков" if offer else "")
        values[f'Цена КП {number}'] = offer.get('public_price', '')
        values[f'Ссылка на товар КП {number}'] = offer.get('product_url', '')
        values[f'Ссылка на счёт КП {number}'] = offer.get('invoice_url') or ''
    if payload.get("business_decision"):
        values["Текущий итог просчета и анализа"] = decision_text(payload["business_decision"])
    # Техническая остановка закупки важнее обычного бизнес-решения: строка
    # остаётся в рабочем листе, но команда сразу видит, что нужен ручной
    # просчёт. Схема таблицы при этом не меняется.
    if payload.get("manual_stop_comment"):
        values["Текущий итог просчета и анализа"] = _text(payload["manual_stop_comment"])
    return ["" if values.get(header) is None else values.get(header, "") for header in headers]


def upsert_live_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Перед каждой записью решение пересчитывается каноническим адаптером,
    # чтобы сохранённый final_economics/старый статус не обходил новые правила.
    if payload.get("procurement") and payload.get("item"):
        attach_business_decision(payload)
    client, email = authorize_service_account()
    book = client.open_by_key(_env("GOOGLE_SPREADSHEET_ID"))
    ws = book.worksheet(ACTIVE)
    ensure_kp_schema(book, ws)
    current = ws.get_all_values(value_render_option="FORMULA")
    existing_headers = current[0] if current else []
    headers = list(existing_headers or ACTIVE_HEADERS)
    if len(headers) != len(set(headers)):
        raise ValueError("В строке 1 есть повторяющиеся названия колонок")
    for header in EXTRA_HEADERS:
        if header not in headers:
            if header == "Категория закупки" and "ВЫБРАННАЯ МОДЕЛЬ" in headers:
                headers.insert(headers.index("ВЫБРАННАЯ МОДЕЛЬ"), header)
            else:
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
            if header == 'Текущий итог просчета и анализа' and payload.get('business_decision'):
                continue
            # КП обновляется целиком: старые цена, ссылка и счёт не могут
            # относиться к новому поставщику или оставаться в пустом КП3.
            if header in KP_HEADERS:
                continue
            if header == 'Дополнительные расходы, ₽' and header in headers:
                index = headers.index(header)
                source[index] = old[index] if index < len(old) else ''
                continue
            if header not in headers:
                continue
            index = headers.index(header)
            if index < len(old) and _has_manual_value(old[index]):
                source[index] = old[index]
    for index, formula in formulas(row_number, headers).items():
        if index < len(source):
            source[index] = formula
    for index, formula in quote_formulas(row_number, headers).items():
        source[index] = formula
    ws.batch_format([{'range': rowcol_to_a1(row_number, headers.index(f"Рентабельность КП {number}, %") + 1),
                      'format': {'numberFormat': {'type': 'PERCENT', 'pattern': '0.00%'}}}
                     for number in range(1, 4)])
    ws.batch_update(active_row_updates(row_number, headers, source), value_input_option="USER_ENTERED")
    # Телефон, ID, коды и URL повторно записываем RAW, чтобы +7 не стал формулой.
    raw_updates = []
    text_headers = ("Номер закупки", "ID закупки", "Ссылка на закупку", "ИНН заказчика",
                   "Контакты заказчика", "Код ОКПД2", "Код ЕАТ", "ВЫБРАННАЯ МОДЕЛЬ",
                   "Ссылка на товар КП 1",
                   "Ссылка на товар КП 2", "Ссылка на товар КП 3", "ПОЗВОНИТЬ / ЗАПРОСИТЬ ЦЕНУ")
    text_headers += tuple(header for header in KP_HEADERS if not header.startswith(('Цена', 'Рентабельность')))
    for header in text_headers:
        if header in headers:
            column = headers.index(header) + 1
            raw_updates.append({"range": rowcol_to_a1(row_number, column),
                                "values": [[str(source[column - 1])]]})
    if raw_updates:
        ws.batch_update(raw_updates, value_input_option="RAW")
    return {"spreadsheet_url": book.url, "worksheet": ACTIVE, "row": row_number,
            "service_account_email": email, "key": list(key)}


def publish_business_decision(payload: dict[str, Any], *, book=None) -> dict[str, Any]:
    """Записать только решение сохранённого просчёта в совпадающую строку КП."""
    if payload.get('procurement') and payload.get('item'):
        attach_business_decision(payload)
    if book is None:
        client, _ = authorize_service_account()
        book = client.open_by_key(_env('GOOGLE_SPREADSHEET_ID'))
    ws = book.worksheet(ACTIVE)
    current = ws.get_all_values(value_render_option='UNFORMATTED_VALUE')
    headers = current[0]
    if len(headers) != len(set(headers)):
        raise ValueError('Повторяющиеся заголовки; решение не записано')
    target = 'Текущий итог просчета и анализа'
    if target not in headers:
        raise ValueError('Нет существующей колонки итога; структура таблицы не изменена')
    key = (_key_part(payload['procurement']['id']), _key_part(payload['item']['position_number']))
    def cell(row, header):
        index = headers.index(header)
        return row[index] if index < len(row) else ''
    matches = [(n, row) for n, row in enumerate(current[1:], 2)
               if (_key_part(cell(row, 'ID закупки')), _key_part(cell(row, '№ позиции'))) == key]
    if len(matches) != 1:
        raise ValueError('Строка закупки не установлена однозначно; решение не записано')
    row_number, row = matches[0]
    offers = final_kp_offers(payload['supplier_search'])
    for number in range(1, 4):
        offer = offers[number - 1] if number <= len(offers) else {}
        expected = {f'Цена КП {number}': offer.get('public_price', ''),
                    f'Ссылка на товар КП {number}': offer.get('product_url', ''),
                    f'Поставщик КП {number}': offer.get('supplier_name', ''),
                    f'Проверка поставщика КП {number}': supplier_check_summary(offer) if offer else ''}
        for header, value in expected.items():
            if str(cell(row, header)) != str(value):
                # Google возвращает целые цены без .0.
                if header.startswith('Цена') and cell(row, header) == value:
                    continue
                raise ValueError('КП в таблице отличаются от сохранённого просчёта; устаревшее решение не записано')
    text = decision_text(payload['business_decision'])
    address = rowcol_to_a1(row_number, headers.index(target) + 1)
    ws.update([[text]], address, value_input_option='RAW')
    if ws.acell(address).value != text:
        raise RuntimeError('Google Sheets не подтвердил записанное решение')
    return {'url': f'{book.url}#gid={ws.id}&range={address}', 'worksheet': ACTIVE,
            'row': row_number, 'cell': address, 'readback_verified': True}
