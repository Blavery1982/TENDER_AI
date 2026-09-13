"""Русские строки для Google Sheets. Один товар — одна строка."""
from __future__ import annotations
import json, math
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parent.parent; NO="Нет данных"
ACTIVE="🟢 Актуальные закупки"; LOCKED="🔒 Закрытые закупки"; MANUAL="🟡 Закупки для ручной проверки"; REPORT="📊 Отчёт"
ACTIVE_HEADERS="""Крайний срок подачи заявки|Номер закупки|ID закупки|Ссылка на закупку|Источник закупки|Наименование закупки в извещении|Место поставки|Срок поставки|Вид оплаты|НМЦК, ₽|Заказчик|ИНН заказчика|Контакты заказчика|Комиссия площадки, ₽|№ позиции|Название позиции (ТЗ)|Код ОКПД2|Код ЕАТ|Количество|Единица измерения|Цена заказчика за единицу, ₽|Сумма позиции, ₽|ОСОБЫЕ УСЛОВИЯ|ПРОСЛЕЖИВАЕМОСТЬ|Дополнительные расходы, ₽|Средняя цена закупа, ₽|Налоги, ₽|Все итоговые затраты, ₽|Расчётная цена подачи, ₽|Цена подачи, ₽|Чистая прибыль, ₽|Запас на снижение, ₽|Запас на снижение, %|Рентабельность, %|Маржа с закупки, ₽|Максимальная цена закупа, ₽|Цена КП 1|Рентабельность КП 1, %|Ссылка на товар КП 1|Ссылка на счёт КП 1|Цена КП 2|Рентабельность КП 2, %|Ссылка на товар КП 2|Ссылка на счёт КП 2|Цена КП 3|Рентабельность КП 3, %|Ссылка на товар КП 3|Ссылка на счёт КП 3|Цена КП 4|Рентабельность КП 4, %|Ссылка на товар КП 4|Ссылка на счёт КП 4|Качество просчёта""".split("|")
ACTIVE_HEADERS.append("Статус закупки")
LOCKED_HEADERS="Крайний срок подачи заявки|Осталось времени|ID закупки|Ссылка на закупку|Источник закупки|Статус".split("|")
MANUAL_HEADERS="Крайний срок подачи заявки|Номер закупки|ID закупки|Ссылка на закупку|Наименование закупки|НМЦК, ₽|Регион|Причина ручной проверки".split("|")
REPORT_HEADERS="Дата запуска|Время запуска|Всего закупок на ЕАТ на момент запуска|Открытых закупок|Закрытых закупок|Прошли фильтр по цене|Прошли фильтр по регионам|Товары|Услуги|Работы|Смешанные|Нужна ручная проверка|Актуальные закупки|Отклонено|Передано в просчёт|Просчёт в работе|Просчёт принят|Продолжить поиск поставщиков|Подано заявок|Побед|Не победили|Контрактов подписано|Товаров отправлено|УПД подписано|Оплачено|Удалено просроченных закупок".split("|")
PROCUREMENT_AUDIT_HEADERS = [
    "ПРОВЕРКА ТЗ",
    "ОБОСНОВАНИЕ ЦЕНЫ",
    "МОДЕЛЬ ИЗ ОБОСНОВАНИЯ",
    "ЦЕНОВОЙ ПОРОГ ДЛЯ ПОИСКА, ₽",
    "ПОДХОДЯЩИЕ МОДЕЛИ, ОПРЕДЕЛЕННЫЕ ПО ТЗ",
    "ПРОВЕРКА СООТВЕТСТВИЯ МОДЕЛИ ТЗ",
    "ЦЕНОВАЯ ПРОВЕРКА МОДЕЛИ",
    "СООТВЕТСТВИЕ ОБОСНОВАНИЯ ТЗ",
    "НЕСООТВЕТСТВИЯ",
    "ОШИБКИ В ДОКУМЕНТАХ ЗАКАЗЧИКА",
    "ГОТОВНОСТЬ К ПОИСКУ ПОСТАВЩИКОВ",
]

# В этих колонках TENDER_AI записывает произвольный текст. Проверка данных и
# выпадающий список для них запрещены.
FREE_TEXT_HEADERS = {"ВЫБРАННАЯ МОДЕЛЬ"}


def clear_data_validation_requests(sheet_id: int, headers: list[str]) -> list[dict[str, Any]]:
    """Снять dropdown с текстовых колонок, находя их по точному заголовку."""
    requests: list[dict[str, Any]] = []
    for header in FREE_TEXT_HEADERS:
        if header not in headers:
            continue
        column = headers.index(header)
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": column,
                    "endColumnIndex": column + 1,
                },
                "rule": None,
            }
        })
    return requests

def active_headers_with_procurement_audit() -> list[str]:
    """Будущая схема: новые колонки сразу после «ПРОСЛЕЖИВАЕМОСТЬ»."""
    position = ACTIVE_HEADERS.index("ПРОСЛЕЖИВАЕМОСТЬ") + 1
    return ACTIVE_HEADERS[:position] + PROCUREMENT_AUDIT_HEADERS + ACTIVE_HEADERS[position:]
REASONS={"law_not_determined":"Не удалось подтвердить 44-ФЗ","region_not_determined":"Не удалось определить регион","price_not_determined":"Не удалось определить цену","items_count_not_determined":"Не удалось определить количество позиций","procurement_kind_mixed":"Смешанная закупка","procurement_kind_uncertain":"Не удалось определить вид закупки"}

# Пустой источник расчёта отображается как пустая ячейка, а не как текст или 0.
# Входные цены КП и дополнительные расходы также входят сюда: числовой 0
# является введённым значением и распознаётся формулами через ISNUMBER.
CALCULATION_HEADERS = {
    "Дополнительные расходы, ₽", "Средняя цена закупа, ₽", "Налоги, ₽",
    "Все итоговые затраты, ₽", "Расчётная цена подачи, ₽", "Цена подачи, ₽",
    "Чистая прибыль, ₽", "Запас на снижение, ₽", "Запас на снижение, %",
    "Рентабельность, %", "Маржа с закупки, ₽", "Максимальная цена закупа, ₽",
    "Цена КП 1", "Рентабельность КП 1, %", "Цена КП 2",
    "Рентабельность КП 2, %", "Цена КП 3", "Рентабельность КП 3, %",
    "Цена КП 4", "Рентабельность КП 4, %", "Качество просчёта",
}
# Совместимость локальных fixtures с исходным порядком. Production использует
# только названия заголовков и не зависит от этих индексов.
CALCULATION_COLUMNS = {ACTIVE_HEADERS.index(name) for name in CALCULATION_HEADERS}

def val(x:Any)->Any:
    return NO if x is None or x=="" or isinstance(x,float) and math.isnan(x) else "Да" if x is True else "Нет" if x is False else x
def date(x:Any)->str:
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00")).strftime("%d.%m.%Y %H:%M") if x else NO
    except ValueError:return val(x)
def link(x:Any)->str:return str(x) if x else NO
def reason(x:str)->str:
    if x in REASONS:return REASONS[x]
    if ": " in x:return {"hard_exclusion":"Исключённая категория товара","contextual_exclusion":"Контекстное исключение"}.get(x.split(":",1)[0],"Причина")+": "+x.split(": ",1)[1]
    return "Требуется ручная проверка"

def _column_letter(index: int) -> str:
    result = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def formulas(r:int, headers:list[str] | None = None)->dict[int,str]:
    """Формулы по точным заголовкам — порядок колонок может быть любым."""
    headers = headers or ACTIVE_HEADERS
    if len(headers) != len(set(headers)):
        raise ValueError("В строке заголовков Google Sheets есть дубликаты")
    positions = {name: index for index, name in enumerate(headers)}

    def index(name: str) -> int:
        if name not in positions:
            raise KeyError(f"Не найдена обязательная колонка: {name}")
        return positions[name]
    def letter(name: str) -> str:
        return _column_letter(index(name))
    def cell(name: str, absolute_column: bool = False) -> str:
        return f'{"$" if absolute_column else ""}{letter(name)}{r}'
    def column_range(name: str) -> str:
        col = letter(name)
        return f'${col}$2:${col}$10000'
    def full_column(name: str) -> str:
        col = letter(name)
        return f'${col}:${col}'

    id_cell = cell("ID закупки", True)
    id_range = column_range("ID закупки")
    count=f'COUNTIF({id_range};{id_cell})'
    def complete(name:str)->str:
        return f'SUMPRODUCT(({id_range}={id_cell})*N(ISNUMBER({column_range(name)})))={count}'
    quotes_ready=(f'AND({id_cell}<>"";{complete("Количество")};{complete("Цена КП 1")};'
                  f'{complete("Цена КП 2")};{complete("Цена КП 3")})')
    extras_ready=f'AND({id_cell}<>"";{complete("Дополнительные расходы, ₽")})'
    avg=(f'IF(NOT({quotes_ready});"";'
         f'SUMPRODUCT(({id_range}={id_cell})*{column_range("Количество")}*'
         f'({column_range("Цена КП 1")}+{column_range("Цена КП 2")}+'
         f'{column_range("Цена КП 3")})/3))')
    average=cell("Средняя цена закупа, ₽")
    commission=cell("Комиссия площадки, ₽")
    nmck=cell("НМЦК, ₽")
    tax=cell("Налоги, ₽")
    total=cell("Все итоговые затраты, ₽")
    calculated=cell("Расчётная цена подачи, ₽")
    submission=cell("Цена подачи, ₽")
    profit=cell("Чистая прибыль, ₽")
    reserve=cell("Запас на снижение, ₽")
    maximum=cell("Максимальная цена закупа, ₽")
    base=(f'({average}+{commission}+SUMIF({full_column("ID закупки")};{id_cell};'
          f'{full_column("Дополнительные расходы, ₽")}))')
    initial_tax=f'({base}*0,015/0,835)'
    base_ready=f'AND(ISNUMBER({average});ISNUMBER({commission});ISNUMBER({nmck});{extras_ready})'
    return {
        index("Средняя цена закупа, ₽"):f'={avg}',
        index("Налоги, ₽"):(f'=IF(NOT({base_ready});"";'
            f'IF(({base}+{initial_tax})*1,1<={nmck};'
            f'{initial_tax};MAX(0;({nmck}-{base})*0,15)))'),
        index("Все итоговые затраты, ₽"):f'=IF(OR(NOT({base_ready});NOT(ISNUMBER({tax})));"";{base}+{tax})',
        index("Расчётная цена подачи, ₽"):f'=IF(NOT(ISNUMBER({total}));"";{total}*1,10)',
        index("Цена подачи, ₽"):f'=IF(OR(NOT(ISNUMBER({calculated}));NOT(ISNUMBER({nmck})));"";MIN({calculated};{nmck}))',
        index("Чистая прибыль, ₽"):f'=IF(OR(NOT(ISNUMBER({submission}));NOT(ISNUMBER({total})));"";{submission}-{total})',
        index("Запас на снижение, ₽"):f'=IF(OR(NOT(ISNUMBER({submission}));NOT({base_ready}));"";{submission}-{base})',
        index("Запас на снижение, %"):f'=IF(OR(NOT(ISNUMBER({submission}));NOT(ISNUMBER({reserve}));{submission}=0);"";{reserve}/{submission})',
        index("Рентабельность, %"):f'=IF(OR(NOT(ISNUMBER({total}));NOT(ISNUMBER({profit}));{total}=0);"";{profit}/{total})',
        index("Маржа с закупки, ₽"):f'=IF(NOT(ISNUMBER({profit}));"";{profit})',
        index("Максимальная цена закупа, ₽"):(f'=IF(OR(NOT(ISNUMBER({nmck}));NOT({extras_ready}));"";'
            f'{nmck}/1,18-SUMIF({full_column("ID закупки")};{id_cell};{full_column("Дополнительные расходы, ₽")}))'),
        index("Качество просчёта"):(f'=IF(OR(NOT({quotes_ready});NOT(ISNUMBER({average}));'
            f'NOT(ISNUMBER({maximum})));"";IF({average}>{maximum};'
            f'"Продолжить поиск — цена закупа слишком высокая";"Просчёт принят"))'),
    }

def build()->dict[str,list[list[Any]]]:
    allp=json.loads((ROOT/'data/eat_filtered_semantic_v2.json').read_text())['purchases']; enrich={x['tradeNumber']:x for x in json.loads((ROOT/'data/eat_enrichment_test.json').read_text())['purchases']}; trace={x['tradeNumber']:x for x in json.loads((ROOT/'data/eat_traceability_200_test.json').read_text())['purchases']}
    active=[ACTIVE_HEADERS]; manual=[MANUAL_HEADERS]
    for p in allp:
      n=p['normalized']; raw=p['raw']; num=n.get('number')
      if p['filter_result']=='passed':
       e=enrich.get(num,{}); t=trace.get(num,{}); items=raw.get('lotItems') or [{}]; trace_items={x.get('item_index'):x for x in t.get('traceability_items',[])}
       org=raw.get('organizerInfo') or {}; addresses='; '.join(p.get('delivery_addresses') or []) or NO
       for i,it in enumerate(items,1):
        title=it.get('description') or it.get('name') or it.get('eatTitle') or NO
        period=raw.get('deliveryPeriod'); delivery=(f"{period} рабочих дней" if period and raw.get('isDeliveryDaysWorking') else f"{period} календарных дней" if period else raw.get('deliveryDate'))
        item_trace=trace_items.get(i-1,{})
        row=[date(raw.get('applicationFillingEndDate')),val(num),val(raw.get('id')),link(n.get('url')),'ЕАТ «Берёзка»',val(raw.get('subject')),addresses,val(delivery),NO,val(raw.get('price')),val(org.get('name')),val(org.get('inn')),val('; '.join(filter(None,[org.get('phoneNumber'),org.get('email')]))),round((raw.get('price') or 0)*.03,2),i,title,val(it.get('okpd2Code')),val(it.get('eatCode')),val(it.get('quantity')),val(it.get('okeiTitle')),val(it.get('unitPrice')),val(it.get('sum')),e.get('special_conditions_short') or 'Нет',item_trace.get('traceability_status') or t.get('traceability_status') or NO,""]+[""]*29
        for col in CALCULATION_COLUMNS:
            row[col]=""
        for col,f in formulas(len(active)+1).items():row[col]=f
        active.append(row)
      elif p['filter_result']=='manual_check':manual.append([date(raw.get('applicationFillingEndDate')),val(num),val(raw.get('id')),link(n.get('url')),val(raw.get('subject')),val(raw.get('price')),val(', '.join(p.get('normalized_regions') or [])), '; '.join(reason(x) for x in p.get('rejection_reasons',[]))])
    locked=[LOCKED_HEADERS]+[[date(x.get('applicationFillingEndDate')),val(x.get('time_left')),val(x.get('id')),link(x.get('url')),'ЕАТ «Берёзка»','Закрытая закупка'] for x in json.loads((ROOT/'data/eat_confidential_links.json').read_text())['purchases']]
    return {ACTIVE:active,LOCKED:locked,MANUAL:manual}
