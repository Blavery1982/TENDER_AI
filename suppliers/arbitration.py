"""Безопасная оценка сведений КАД без обхода CAPTCHA и антибот-защиты."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

KAD_URL = "https://kad.arbitr.ru"


def unavailable_kad_result(inn: str | None, warning: str) -> dict:
    return {
        "checked_in_kad": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "searched_inn": inn,
        "cases_found": None,
        "cases_count": None,
        "plaintiff_cases_count": None,
        "defendant_cases_count": None,
        "bankruptcy_cases_count": None,
        "other_cases_count": None,
        "recent_cases_count": None,
        "case_numbers": [], "case_roles": [], "case_dates": [],
        "case_categories": [], "kad_url": KAD_URL,
        "evidence": None, "warnings": [warning],
        "user_summary": "Арбитражные дела: требуется ручная проверка",
        "risk_level": "unknown",
    }


def assess_kad_cases(inn: str, cases: list[dict], *, checked_at: str | None = None) -> dict:
    """Нормализует уже достоверно полученный ответ поиска КАД."""
    now = datetime.fromisoformat(checked_at) if checked_at else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    plaintiff = defendant = bankruptcy = other = recent = 0
    numbers, roles, dates, categories = [], [], [], []
    for case in cases:
        role = str(case.get("role") or "иная роль").casefold()
        category = str(case.get("category") or "не определена")
        if "истец" in role:
            plaintiff += 1
        elif "ответчик" in role:
            defendant += 1
        else:
            other += 1
        if "банкрот" in category.casefold() or case.get("is_bankruptcy") is True:
            bankruptcy += 1
        raw_date = case.get("date")
        if raw_date:
            try:
                date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                if date >= now - timedelta(days=365 * 2):
                    recent += 1
            except ValueError:
                pass
        numbers.append(case.get("number"))
        roles.append(case.get("role"))
        dates.append(raw_date)
        categories.append(case.get("category"))
    count = len(cases)
    if bankruptcy:
        risk = "bankruptcy"
    elif defendant >= 3 and recent >= 2:
        risk = "elevated"
    elif defendant:
        risk = "attention"
    else:
        risk = "informational"
    if count:
        summary = (f"Арбитражные дела: ⚠️ ЕСТЬ СУДЫ! Найдено {count}: "
                   f"истец — {plaintiff}, ответчик — {defendant}, иная роль — {other}.")
        if bankruptcy:
            summary += f" 🔴 ОБНАРУЖЕНО ДЕЛО О БАНКРОТСТВЕ: {bankruptcy}."
    else:
        summary = "Арбитражные дела: не обнаружены по результатам доступной проверки"
    return {
        "checked_in_kad": True, "checked_at": now.isoformat(), "searched_inn": inn,
        "cases_found": bool(count), "cases_count": count,
        "plaintiff_cases_count": plaintiff, "defendant_cases_count": defendant,
        "bankruptcy_cases_count": bankruptcy, "other_cases_count": other,
        "recent_cases_count": recent, "case_numbers": numbers, "case_roles": roles,
        "case_dates": dates, "case_categories": categories, "kad_url": KAD_URL,
        "evidence": cases, "warnings": [], "user_summary": summary, "risk_level": risk,
    }


def check_kad(inn: str | None, fetcher: Callable[[str], list[dict]] | None = None) -> dict:
    """Точка интеграции.

    КАД не предоставляет проекту документированный стабильный API. Без переданного
    законного адаптера результат остаётся ручным; CAPTCHA никогда не обходится.
    """
    if not inn:
        return unavailable_kad_result(None, "Текущий ИНН поставщика не подтверждён")
    if fetcher is None:
        return unavailable_kad_result(
            inn, "Проверка арбитражных дел автоматически не выполнена: "
                 "документированный публичный API КАД не подтверждён")
    try:
        response = fetcher(inn)
        if response is None:
            return unavailable_kad_result(inn, "КАД вернул пустой или неоднозначный ответ")
        return assess_kad_cases(inn, response)
    except PermissionError:
        return unavailable_kad_result(inn, "КАД требует CAPTCHA/ручного доступа; защита не обходилась")
    except (ConnectionError, TimeoutError, OSError) as exc:
        return unavailable_kad_result(inn, f"КАД недоступен: {type(exc).__name__}")
