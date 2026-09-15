"""Переиспользование колонок КП без потери данных и ссылок формул."""
from __future__ import annotations


def kp_headers(number: int) -> list[str]:
    return [f"Поставщик КП {number}", f"Цена КП {number}",
            f"Рентабельность КП {number}, %", f"Ссылка на товар КП {number}",
            f"Ссылка на счёт КП {number}", f"Проверка поставщика КП {number}"]


KP_HEADERS = {header for number in range(1, 4) for header in kp_headers(number)}


def plan_kp_schema(headers: list[str], sheet_id: int) -> tuple[list[str], list[dict]]:
    """Перемещение целых колонок заставляет Sheets обновить ссылки формул."""
    if len(headers) != len(set(headers)):
        raise ValueError("В строке 1 есть повторяющиеся названия колонок")
    result, requests = list(headers), []
    # Основные блоки обязаны существовать: не создавать вторую схему КП.
    for number in range(1, 4):
        for header in kp_headers(number)[1:5]:
            if header not in headers:
                raise ValueError(f"Не найдена существующая колонка: {header}")
    for number in range(1, 4):
        aliases = {f"Поставщик КП {number}": f"ПОСТАВЩИК №{number}",
                   f"Проверка поставщика КП {number}": f"ПРОВЕРКА ПОСТАВЩИКА №{number}"}
        for name, legacy in aliases.items():
            if name in result:
                continue
            target = (result.index(f"Цена КП {number}") if name.startswith("Поставщик")
                      else result.index(f"Ссылка на счёт КП {number}") + 1)
            if legacy in result:
                source = result.index(legacy)
                if source < target:
                    target -= 1
                if source != target:
                    requests.append({"moveDimension": {
                        "source": {"sheetId": sheet_id, "dimension": "COLUMNS",
                                   "startIndex": source, "endIndex": source + 1},
                        "destinationIndex": target if source > target else target + 1}})
                    result.insert(target, result.pop(source))
                result[target] = name
            else:
                requests.append({"insertDimension": {"range": {
                    "sheetId": sheet_id, "dimension": "COLUMNS",
                    "startIndex": target, "endIndex": target + 1},
                    "inheritFromBefore": True}})
                result.insert(target, name)
            requests.append({"updateCells": {
                "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": target},
                "rows": [{"values": [{"userEnteredValue": {"stringValue": name}}]}],
                "fields": "userEnteredValue"}})
    return result, requests


def ensure_kp_schema(book, ws) -> list[str]:
    headers, requests = plan_kp_schema(ws.row_values(1), ws.id)
    if requests:
        book.batch_update({"requests": requests})
        if ws.row_values(1) != headers:
            raise RuntimeError("Заголовки КП после изменения не совпали с ожидаемыми")
    return headers
