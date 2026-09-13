"""Извлечение и безопасная оценка юридических рисков заказчика."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from suppliers.arbitration import check_kad

STATUS_CLEAR = "✅ ЯВНЫХ СУДЕБНЫХ РИСКОВ НЕ ОБНАРУЖЕНО ПО ДОСТУПНЫМ ДАННЫМ"
STATUS_CASES = "⚠️ ЕСТЬ СУДЫ!"
STATUS_MANUAL = "🟡 ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА КАД"
STATUS_SERIOUS = "🔴 ОБНАРУЖЕН СЕРЬЁЗНЫЙ ЮРИДИЧЕСКИЙ РИСК"


def _first(data: Any, names: set[str]) -> Any:
    if isinstance(data, dict):
        for key, value in data.items():
            if key.casefold() in names and value not in (None, ""):
                return value
        for value in data.values():
            found = _first(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _first(value, names)
            if found not in (None, ""):
                return found
    return None


def extract_customer(card: dict, documents: list[dict] | None = None) -> dict:
    """Сначала структурированная карточка, затем точные реквизиты документа."""
    structured = card.get("raw") or card
    customer_block = _first(
        structured,
        {"customer", "customerorganization", "customerinfo", "organizerinfo"},
    )
    if not isinstance(customer_block, dict):
        customer_block = {}
    name = _first(customer_block, {"name", "fullname", "organizationname", "customername"})
    inn = _first(customer_block, {"inn", "customerinn"})
    kpp = _first(customer_block, {"kpp", "customerkpp"})
    ogrn = _first(customer_block, {"ogrn", "customerogrn"})
    address = _first(
        customer_block,
        {"address", "legaladdress", "customeraddress", "postaladdress", "factaddress"},
    )
    contact_fio = _first(customer_block, {"contactfio", "contactperson", "contactname"})
    email = _first(customer_block, {"contactemail", "email", "customeremail"})
    phone = _first(customer_block, {"contactphone", "phone", "phonenumber", "customerphone"})
    fax = _first(customer_block, {"contactfax", "fax", "customerfax"})
    if inn and re.fullmatch(r"\d{10}|\d{12}", str(inn)):
        return {"customer_name": name, "customer_inn": str(inn),
                "customer_kpp": str(kpp) if kpp else None,
                "customer_ogrn": str(ogrn) if ogrn else None,
                "customer_address": str(address) if address else None,
                "customer_contact_fio": str(contact_fio) if contact_fio else None,
                "customer_email": str(email) if email else None,
                "customer_phone": str(phone) if phone else None,
                "customer_fax": str(fax) if fax else None,
                "customer_data_source": "Структурированные данные карточки ЕАТ"}

    text = "\n".join(str(x.get("text") or "") for x in (documents or []))
    inn_match = re.search(r"\bИНН\s*[:№]?\s*(\d{10}|\d{12})\b", text, re.I)
    kpp_match = re.search(r"\bКПП\s*[:№]?\s*(\d{9})\b", text, re.I)
    ogrn_match = re.search(r"\bОГРН(?:ИП)?\s*[:№]?\s*(\d{13}|\d{15})\b", text, re.I)
    name_match = re.search(
        r"((?:Федеральное|Государственное|Муниципальное|Бюджетное|Автономное|Казенное|Казённое)[^\n]{0,40}"
        r"(?:учреждение|предприятие)\s+[«\"].{3,250}?[»\"])", text, re.I)
    if not name_match:
        name_match = re.search(r"(ФКУ\s+[А-ЯЁA-Z0-9№\- ]{3,100})", text)
    return {"customer_name": name_match.group(1).strip() if name_match else name,
            "customer_inn": inn_match.group(1) if inn_match else None,
            "customer_kpp": kpp_match.group(1) if kpp_match else None,
            "customer_ogrn": ogrn_match.group(1) if ogrn_match else None,
            "customer_address": str(address) if address else None,
            "customer_contact_fio": str(contact_fio) if contact_fio else None,
            "customer_email": str(email) if email else None,
            "customer_phone": str(phone) if phone else None,
            "customer_fax": str(fax) if fax else None,
            "customer_data_source": "Документы закупки" if inn_match else "ИНН заказчика не определён"}


def build_customer_check(card: dict, documents: list[dict] | None = None,
                         kad_fetcher: Callable[[str], list[dict]] | None = None) -> dict:
    identity = extract_customer(card, documents)
    inn = identity["customer_inn"]
    kad = check_kad(inn, kad_fetcher)
    warnings = list(kad.get("warnings") or [])
    manual_actions = []
    if not inn:
        arbitration_status = STATUS_MANUAL
        payment_risk = "Недостаточно данных для оценки риска оплаты"
        warnings.append("ИНН заказчика не определён")
        manual_actions.append({"action": "Уточнить ИНН заказчика", "customer_inn": "Нет данных"})
    elif not kad.get("checked_in_kad"):
        arbitration_status = STATUS_MANUAL
        payment_risk = "Риск оплаты не оценён автоматически — требуется ручная проверка КАД"
        manual_actions.append({"action": "Проверить КАД вручную", "customer_name": identity["customer_name"] or "Нет данных",
                               "customer_inn": inn, "url": "https://kad.arbitr.ru",
                               "reason": "Автоматический результат КАД недоступен или неоднозначен"})
    elif kad.get("bankruptcy_cases_count", 0):
        arbitration_status = STATUS_SERIOUS
        payment_risk = "Серьёзный юридический риск — требуется ручная проверка"
        warnings.append("Обнаружено дело о банкротстве / серьёзный юридический риск")
        manual_actions.append({"action": "Проверить банкротное дело и платёжный риск вручную",
                               "customer_inn": inn, "url": "https://kad.arbitr.ru"})
    elif kad.get("cases_found"):
        arbitration_status = STATUS_CASES
        if kad.get("defendant_cases_count", 0):
            payment_risk = "Есть судебные споры — требуется учитывать риск оплаты"
            warnings.append("Проверить характер свежих дел, где заказчик является ответчиком")
        else:
            payment_risk = "Обычные дела в роли истца сами по себе не указывают на риск оплаты"
    else:
        arbitration_status = STATUS_CLEAR
        payment_risk = "Явных судебных рисков по доступной проверке не обнаружено"
    summary = kad.get("user_summary", "Арбитражные дела заказчика: требуется ручная проверка")
    summary = summary.replace("Арбитражные дела:", "Арбитражные дела заказчика:")
    return {**identity, "arbitration_status": arbitration_status,
            "arbitration_summary": summary,
            "cases_count": kad.get("cases_count"),
            "plaintiff_cases_count": kad.get("plaintiff_cases_count"),
            "defendant_cases_count": kad.get("defendant_cases_count"),
            "bankruptcy_cases_count": kad.get("bankruptcy_cases_count"),
            "recent_cases_count": kad.get("recent_cases_count"),
            "case_numbers": kad.get("case_numbers") or [], "case_roles": kad.get("case_roles") or [],
            "case_dates": kad.get("case_dates") or [], "case_categories": kad.get("case_categories") or [],
            "payment_risk_status": payment_risk, "warnings": list(dict.fromkeys(warnings)),
            "manual_actions_required": manual_actions,
            "checked_at": kad.get("checked_at") or datetime.now(timezone.utc).isoformat(),
            "arbitration_evidence": kad}


def customer_check_for_google_sheets(result: dict) -> dict:
    """Русские поля будущего интерфейса; Google Sheets сейчас не вызывается."""
    display = lambda value: "Нет данных" if value is None else value
    return {"ПРОВЕРКА ЗАКАЗЧИКА": result["arbitration_status"],
            "АРБИТРАЖ ЗАКАЗЧИКА": result["arbitration_summary"],
            "СУДЫ — КОЛИЧЕСТВО": display(result["cases_count"]),
            "СУДЫ — ОТВЕТЧИК": display(result["defendant_cases_count"]),
            "СУДЫ — ИСТЕЦ": display(result["plaintiff_cases_count"]),
            "БАНКРОТНЫЕ ДЕЛА": display(result["bankruptcy_cases_count"]),
            "РИСК ОПЛАТЫ": result["payment_risk_status"],
            "КОММЕНТАРИЙ ПО ЗАКАЗЧИКУ": "; ".join(result["warnings"]) or "Нет замечаний"}
