"""Человекочитаемые значения структурированных enum карточки ЕАТ."""
from __future__ import annotations

from typing import Any

# Подтверждено 11.09.2026 на одном и том же официальном ответе карточки
# tender-cache-api (числовой код) и странице agregatoreat.ru (русская подпись).
PAYMENT_TYPE_TITLES = {2: "По счету"}
PAYMENT_CONDITION_TITLES = {3: "В установленный срок"}
PURCHASE_METHOD_TITLES = {1: "Закупочная сессия"}

ENUM_EVIDENCE = {
    "source": "Официальные API и интерфейс карточки ЕАТ",
    "verified_at": "2026-09-11",
    "control_purchase": "100316493126100091",
}


def _explicit_text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip() and not value.strip().isdigit():
            return value.strip()
    return None


def _mapped(
    source: dict[str, Any],
    *,
    code_key: str,
    title_keys: tuple[str, ...],
    mapping: dict[int, str],
    diagnostics: list[dict[str, Any]],
) -> str | None:
    explicit = _explicit_text(source, *title_keys)
    if explicit:
        return explicit
    code = source.get(code_key)
    if code in (None, ""):
        return None
    try:
        normalized_code = int(code)
    except (TypeError, ValueError):
        normalized_code = code
    title = mapping.get(normalized_code) if isinstance(normalized_code, int) else None
    if title is None:
        diagnostics.append({
            "field": code_key,
            "raw_value": code,
            "warning": "Enum ЕАТ не расшифрован; пользовательское значение оставлено пустым",
        })
    return title


def _payment_deadline(lot: dict[str, Any]) -> str | None:
    days = lot.get("paymentDateInDays")
    if days in (None, ""):
        return None
    day_kind = "рабочих" if lot.get("isPaymentPeriodWorkDays") is True else "календарных"
    if lot.get("isPaymentInDaysSinceSigninAcceptanceDocument") is True:
        return f"{days} {day_kind} дней с даты подписания документа о приемке"
    if lot.get("isPaymentInDaysSinceDealExecuting") is True:
        return f"{days} {day_kind} дней с даты исполнения сделки"
    return None


def normalize_card_enums(raw: dict[str, Any], lot: dict[str, Any]) -> dict[str, Any]:
    """Преобразовать только доказанные enum; неизвестные коды не показывать."""
    diagnostics: list[dict[str, Any]] = []
    payment_type = _mapped(
        lot,
        code_key="paymentType",
        title_keys=("paymentTypeTitle", "paymentTypeName"),
        mapping=PAYMENT_TYPE_TITLES,
        diagnostics=diagnostics,
    )
    payment_condition = _mapped(
        lot,
        code_key="paymentCondition",
        title_keys=("paymentConditionTitle", "paymentConditionName"),
        mapping=PAYMENT_CONDITION_TITLES,
        diagnostics=diagnostics,
    )
    purchase_method = _mapped(
        raw,
        code_key="purchaseMethod",
        title_keys=("purchaseMethodTitle", "purchaseMethodName"),
        mapping=PURCHASE_METHOD_TITLES,
        diagnostics=diagnostics,
    )
    russian = lot.get("isRussianItemsPurchase")
    russian_title = "Да" if russian is True else "Нет" if russian is False else None
    purchase_type = _explicit_text(raw, "purchaseTypeTitle", "purchaseTypeName")
    if raw.get("purchaseTypeId") not in (None, "") and not purchase_type:
        diagnostics.append({
            "field": "purchaseTypeId",
            "raw_value": raw.get("purchaseTypeId"),
            "warning": "Тип закупки не расшифрован; пользовательское значение оставлено пустым",
        })
    return {
        "payment_type": payment_type,
        "payment_condition": payment_condition,
        "payment_deadline": _payment_deadline(lot),
        "purchase_type": purchase_type,
        "purchase_method": purchase_method,
        "russian_items_purchase": russian_title,
        "diagnostics": diagnostics,
        "evidence": ENUM_EVIDENCE,
    }
