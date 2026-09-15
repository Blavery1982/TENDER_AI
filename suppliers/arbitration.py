"""КАД: только количество дел ответчика по актуальному ИНН, без судебного анализа."""
from datetime import datetime, timezone

RESULT_FIELDS = ("searched_inn", "checked_at", "technical_status", "defendant_cases_count", "kad_status", "reason")


def defendant_kad_result(inn, count, *, checked_at=None):
    if type(count) is not int or count < 0:
        return unavailable_kad_result(inn, "Количество дел ответчика не подтверждено")
    reason = (f"Есть судебные дела в качестве ответчика: {count}. Требуется вручную проверить "
              "судебные иски и добросовестность поставщика." if count else
              "КАД проверен: дел в качестве ответчика не найдено.")
    return {"searched_inn": inn, "checked_at": checked_at or datetime.now(timezone.utc).isoformat(),
            "technical_status": "KAD_CHECKED", "defendant_cases_count": count,
            "kad_status": "YELLOW" if count else "GREEN", "reason": reason}


def unavailable_kad_result(inn, warning, *, status="KAD_REQUIRES_MANUAL_CHECK"):
    return {"searched_inn": inn, "checked_at": datetime.now(timezone.utc).isoformat(),
            "technical_status": status, "defendant_cases_count": None,
            "kad_status": "YELLOW", "reason": str(warning)[:1000]}


def minimal_kad_result(value, inn=None):
    """Старые подробные результаты не подтверждают новую проверку и не переносятся дальше."""
    if not isinstance(value, dict):
        return unavailable_kad_result(inn, "КАД не проверен")
    searched = value.get("searched_inn", inn)
    if value.get("technical_status") == "KAD_CHECKED":
        result = defendant_kad_result(searched, value.get("defendant_cases_count"),
                                      checked_at=value.get("checked_at"))
    else:
        status = value.get("technical_status")
        if status not in {"KAD_REQUIRES_MANUAL_CHECK", "KAD_CURRENT_SELLER_UNDETERMINED"}:
            status = "KAD_REQUIRES_MANUAL_CHECK"
        result = unavailable_kad_result(searched, value.get("reason") or "КАД не проверен по новой схеме", status=status)
        if value.get("checked_at"):
            result["checked_at"] = value["checked_at"]
    return result


def check_kad(inn, fetcher=None):
    if not inn:
        return unavailable_kad_result(None, "Текущий ИНН поставщика не подтверждён",
                                      status="KAD_CURRENT_SELLER_UNDETERMINED")
    if fetcher is None:
        from suppliers.kad_client import KADClient
        return KADClient().check(inn)
    try:
        result = minimal_kad_result(fetcher(inn), inn)
        if result["searched_inn"] != inn:
            return unavailable_kad_result(inn, "КАД вернул результат по другому ИНН")
        return result
    except Exception as exc:
        # Техническая непроверенность никогда не превращается в нулевой счётчик.
        return unavailable_kad_result(inn, f"КАД не проверен: {type(exc).__name__}: {str(exc)[:800]}")
