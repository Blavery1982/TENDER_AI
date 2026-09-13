"""Консервативная проверка поставщика по доступным открытым данным."""
from __future__ import annotations

from urllib.parse import urlparse
from datetime import datetime, timezone

PASSED = "✅ ПОСТАВЩИК ПРОШЁЛ ПРОВЕРКУ"
MANUAL = "🟡 ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА ПЕРЕД ОПЛАТОЙ"
HIGH_RISK = "🔴 ПРИЗНАКИ ВЫСОКОГО РИСКА — НЕ ОПЛАЧИВАТЬ"
INSUFFICIENT = "⚪ НЕДОСТАТОЧНО ДАННЫХ"

TRUSTED_DOMAINS = {
    "ozon.ru", "wildberries.ru", "market.yandex.ru", "vseinstrumenti.ru",
    "citilink.ru", "xcom-shop.ru", "komus.ru",
}


def normalize_domain(url: str) -> str:
    value = url if "://" in url else f"https://{url}"
    host = (urlparse(value).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def verify_supplier(supplier: dict) -> dict:
    result = dict(supplier)
    domain = normalize_domain(result.get("product_url") or result.get("domain") or "")
    result["domain"] = domain
    checks = result.get("verification_checks") or {}
    risks, positives, unavailable, evidence_checks = [], [], [], []
    checked_at = datetime.now(timezone.utc).isoformat()
    kad = checks.get("kad_check") or {
        "checked_in_kad": False, "cases_found": None, "bankruptcy_cases_count": None,
        "defendant_cases_count": None, "recent_cases_count": None,
        "user_summary": "Арбитражные дела: требуется ручная проверка",
        "warnings": ["Проверка связанных физических лиц по ИНН не выполнена"],
        "risk_level": "unknown",
    }
    def evidence(name, status, value, source=None):
        evidence_checks.append({"check_name":name,"status":status,"evidence":value,
                                "source":source,"checked_at":checked_at})

    if domain in TRUSTED_DOMAINS:
        positives.append("Доверенный крупный источник")
        evidence("trusted_source","confirmed_positive","Домен входит в доверенный список",domain)
        status = PASSED
    else:
        if checks.get("commercial_site_confirmed") is True:
            positives.append("Подтверждён действующий профильный сайт с карточкой товара и контактами")
            evidence("commercial_site","confirmed_positive",checks.get("commercial_site_evidence"),domain)
        age = checks.get("domain_age_years")
        if age is None:
            unavailable.append("WHOIS/RDAP")
            evidence("domain_age","unavailable","Возраст домена не подтверждён",checks.get("domain_source"))
        elif age >= 3:
            positives.append("Домен старше 3 лет")
            evidence("domain_age","confirmed_positive",f"Возраст домена: {age} лет",checks.get("domain_source"))
        elif age >= 1:
            risks.append("Домен существует от 1 до 3 лет")
            evidence("domain_age","warning",f"Возраст домена: {age} лет",checks.get("domain_source"))
        else:
            risks.append("Домен моложе 1 года")
            evidence("domain_age","warning",f"Возраст домена: {age} лет",checks.get("domain_source"))

        if checks.get("wayback_available") is False:
            unavailable.append("История Wayback отсутствует")
            evidence("wayback_history","unavailable","История сайта в Web Archive не подтверждена","Wayback CDX")
        elif checks.get("same_business_history") is True:
            positives.append("История сайта соответствует текущей деятельности")
            evidence("wayback_history","confirmed_positive",checks.get("history_evidence") or "Тематика стабильна","Wayback CDX")
        elif checks.get("abrupt_profile_change") is True:
            risks.append("Резкая смена тематики сайта")
            evidence("wayback_history","red_flag",checks.get("history_evidence") or "Доказана смена тематики","Wayback CDX")
        elif checks.get("wayback_available") is True:
            unavailable.append("Требуется ручная проверка истории сайта")
            evidence("wayback_history","inconclusive","Снимки найдены, тематика автоматически не доказана","Wayback CDX")

        company = checks.get("current_company") or checks.get("company") or {}
        historical_companies = checks.get("historical_companies") or []
        current_seller_determined = bool(company.get("inn") and checks.get("current_seller_determined", True))
        if not company.get("inn"):
            risks.append("На сайте не найден ИНН")
            evidence("legal_entity","warning","ИНН на исследованных страницах не найден",checks.get("legal_source"))
        elif company.get("active") is True:
            positives.append("Компания действует")
            evidence("legal_entity","confirmed_positive",f"{company.get('name')}; ИНН {company.get('inn')}; действует",company.get("source"))
        elif company.get("active") is False:
            risks.append("Компания ликвидирована или не действует")
            evidence("legal_entity","red_flag",f"ИНН {company.get('inn')}; компания не действует",company.get("source"))
        if checks.get("requisites_consistent") is True and company.get("inn"):
            positives.append("Реквизиты сайта согласованы")
            evidence("requisites_consistency","confirmed_positive",f"На страницах сайта используется ИНН {company.get('inn')}",checks.get("legal_source"))
        elif checks.get("requisites_consistent") is False:
            if current_seller_determined:
                positives.append("Текущий продавец определён; другие ИНН сохранены как исторические")
                evidence("legal_entity_history","confirmed_positive",
                         {"current_inn":company.get("inn"),"historical_companies":historical_companies,
                          "found_requisites":checks.get("requisites_conflict_evidence")},checks.get("legal_source"))
            else:
                unavailable.append("Не удалось определить актуального продавца среди нескольких ИНН")
                evidence("current_legal_entity","inconclusive",checks.get("requisites_conflict_evidence"),checks.get("legal_source"))
        if checks.get("simultaneous_payment_entities") is True:
            risks.append("Сайт одновременно предлагает оплату разным юрлицам без объяснения")
            evidence("payment_requisites","red_flag",checks.get("simultaneous_payment_evidence"),checks.get("payment_source"))
        if company.get("registered_recently") is True:
            risks.append("Текущее юридическое лицо/ИП зарегистрировано недавно — требуется дополнительная проверка перед оплатой")
            evidence("current_legal_entity_age","warning",
                     {"registered_at":company.get("registered_at"),"inn":company.get("inn")},company.get("source"))
        elif company.get("registered_at"):
            evidence("current_legal_entity_age","confirmed_positive",
                     {"registered_at":company.get("registered_at"),"inn":company.get("inn")},company.get("source"))
        if company.get("government_procurement") is True:
            positives.append("Есть участие в государственных закупках")
            evidence("government_procurement","confirmed_positive","Подтверждено участие в государственных закупках",company.get("source"))
        if company.get("director_changed_within_6_months") is True:
            risks.append("Руководитель сменился менее 6 месяцев назад")
            evidence("director_changes","red_flag",company.get("director_change_evidence"),company.get("source"))
        elif company.get("director_changed_within_6_months") is None:
            unavailable.append("Не удалось автоматически проверить изменения руководства")
            evidence("director_changes","unavailable","Дата последних изменений руководства неизвестна",company.get("source"))
        if company.get("site_company_mismatch") is True:
            risks.append("Реквизиты сайта не соответствуют проверенной компании")

        if kad.get("checked_in_kad") is False:
            unavailable.append("Картотека арбитражных дел")
            evidence("arbitration_cases", "unavailable", kad.get("warnings"), kad.get("kad_url"))
        elif kad.get("bankruptcy_cases_count", 0):
            risks.append("Обнаружено дело о банкротстве")
            evidence("arbitration_cases", "red_flag", kad.get("user_summary"), kad.get("kad_url"))
        elif kad.get("risk_level") == "elevated":
            risks.append("Несколько свежих арбитражных дел, где поставщик является ответчиком")
            evidence("arbitration_cases", "warning", kad.get("user_summary"), kad.get("kad_url"))
        elif kad.get("defendant_cases_count", 0):
            risks.append("Есть арбитражные дела, где поставщик является ответчиком")
            evidence("arbitration_cases", "warning", kad.get("user_summary"), kad.get("kad_url"))
        elif kad.get("cases_found"):
            positives.append("Арбитражные дела найдены только без доказанного негативного контекста")
            evidence("arbitration_cases", "informational", kad.get("user_summary"), kad.get("kad_url"))
        else:
            evidence("arbitration_cases", "confirmed_positive", kad.get("user_summary"), kad.get("kad_url"))

        for service in ("whois", "wayback", "company_registry", "cms", "ip"):
            if checks.get(f"{service}_status") == "unavailable":
                unavailable.append(service)
        cms = checks.get("cms")
        evidence("cms","confirmed_positive" if cms and cms != "unknown" else "inconclusive",
                 f"CMS: {cms}" if cms else "CMS не определена",checks.get("cms_source") or domain)
        addresses = checks.get("addresses")
        evidence("ip","confirmed_positive" if addresses else "unavailable",
                 {"addresses":addresses,"reverse_dns":checks.get("reverse_dns"),
                  "sites_on_same_ip":checks.get("sites_on_same_ip")},"DNS")

        strong = {"Реквизиты сайта не соответствуют проверенной компании", "Резкая смена тематики сайта",
                  "Сайт одновременно предлагает оплату разным юрлицам без объяснения",
                  "Компания ликвидирована или не действует", "Обнаружено дело о банкротстве"}
        if "Обнаружено дело о банкротстве" in risks:
            status = HIGH_RISK
        elif strong.intersection(risks) and (len(risks) >= 2 or "Реквизиты сайта не соответствуют проверенной компании" in risks):
            status = HIGH_RISK
        elif not positives and unavailable:
            status = INSUFFICIENT
        elif risks:
            status = MANUAL
        elif company.get("inn") and company.get("active") is True \
                and (checks.get("requisites_consistent") is True or current_seller_determined) \
                and (age is not None and age >= 3 or checks.get("same_business_history") is True):
            status = PASSED
        elif unavailable:
            status = MANUAL if company.get("inn") and company.get("active") is True else INSUFFICIENT
        else:
            status = INSUFFICIENT

    result["risk_flags"] = risks
    result["positive_signals"] = positives
    result["unavailable_checks"] = sorted(set(unavailable))
    result["verification_status"] = status
    result["verification_comment"] = "; ".join(risks + positives + unavailable) or "Нет данных"
    result["arbitration_cases"] = kad
    result["arbitration_cases_display"] = kad.get("user_summary", "Арбитражные дела: требуется ручная проверка")
    result["purchase_price"] = None
    result["verification_evidence"] = evidence_checks
    return result


def verify_invoice_recipient(verified_supplier: dict, invoice_inn: str | None,
                             invoice_name: str | None) -> dict:
    """Интерфейс будущей проверки счёта; ничего не оплачивает и не отправляет."""
    checks = verified_supplier.get("verification_checks") or {}
    company = checks.get("current_company") or checks.get("company") or {}
    matches = bool(invoice_inn and company.get("inn") == invoice_inn)
    return {"matches": matches, "invoice_name": invoice_name, "invoice_inn": invoice_inn,
            "status": "Реквизиты совпадают" if matches else
            "🔴 НЕ ОПЛАЧИВАТЬ — реквизиты счёта не совпадают с проверенным поставщиком"}
