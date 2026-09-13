"""Воспроизводимый supplier_search для одной модели без запросов поставщикам."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from suppliers.ranking import price_request_candidates, select_quotes
from suppliers.verification import verify_supplier

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/supplier_search_third_test.json"
MODEL = "Royal Clima RC-TWN28HN"
CUSTOMER_PRICE = 25400.0
THRESHOLD = 20320.0
QUANTITY = 3


def source(name, url, category, price, availability, checks, **extra):
    return {"supplier_name": name, "product_url": url, "source_category": category,
            "model": MODEL, "exact_model": True, "product_page_available": True,
            "public_price": price, "purchase_price": None, "availability": availability,
            "city": extra.pop("city", None), "contacts": extra.pop("contacts", {}),
            "verification_checks": checks, **extra}


def run_test():
    found = [
        source("Royal-Clima.com.ru", "https://royal-clima.com.ru/royal-clima-rc-twn28hn-triumph",
               "local", 19900, "card_available",
               {"domain_age_years": None, "wayback_status": "unavailable",
                "company": {"inn": None}, "cms": "OpenCart", "ip_status": "unavailable"},
               city="Москва", contacts={"phone": "+7 (800) 100-46-08", "email": "info@royal-clima.com.ru"},
               price_request_reason="Проходная публичная цена, но до оплаты нужно установить и проверить юридическое лицо"),
        source("ООО Стокинг", "https://stoking.ru/products/klassicheskie-split-sistemy-royal-clima-serii-triumph-upgrade-rc-twn28hn",
               "federal_or_specialist", None, "price_on_request",
               {"domain_age_years": 3, "wayback_status": "unavailable",
                "company": {"inn": "5904993922", "ogrn": "1145958010010", "active": True,
                            "registered_at": "2014-03-14", "director_changed_within_6_months": False,
                            "site_company_mismatch": False, "government_procurement": True},
                "cms_status": "unavailable", "ip_status": "unavailable"},
               city="Пермь", contacts={"phone": "8 (800) 600-90-16", "email": "sale@stoking.ru"}),
        source("СДМ Климат", "https://sdmclimate.ru/kondicionery/nastennye-kondicionery/nastennaya-split-sistema-royal-clima-rc-twn28hn",
               "local", None, "price_on_request",
               {"domain_age_years": None, "wayback_status": "unavailable", "company": {"inn": None},
                "cms_status": "unavailable", "ip_status": "unavailable"}, city="Москва"),
        source("Точка Холода", "https://tochkaholoda.ru/catalog/kondicioner-royal-clima-rc-twn28hn",
               "local", 26490, "in_stock",
               {"domain_age_years": None, "wayback_status": "unavailable", "company": {"inn": None},
                "cms_status": "unavailable", "ip_status": "unavailable"}, city="Москва",
               price_request_reason="Цена зависит от объёма; запросить скидку на 3 шт."),
        source("Royal-Rus", "https://royal-rus.ru/bytovye-konditsionery/seriya-triumph/nastennyy-konditsioner-royal-clima-rc-twn28hn/",
               "federal_or_specialist", 26490, "card_available",
               {"domain_age_years": None, "wayback_status": "unavailable", "company": {"inn": None},
                "cms_status": "unavailable", "ip_status": "unavailable"}),
    ]
    checked = [verify_supplier(x) for x in found]
    quotes = select_quotes(checked, THRESHOLD)
    requests = price_request_candidates(checked, THRESHOLD, QUANTITY)
    report = {
        "procurement_number": "100205573126100053", "model": MODEL,
        "delivery_city": "Москва", "quantity": QUANTITY,
        "customer_unit_price": CUSTOMER_PRICE, "price_threshold_80_percent": THRESHOLD,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "checked_named_sources": {"marketplaces": ["Ozon", "Wildberries", "Яндекс Маркет"],
                                  "federal": ["ВсеИнструменты", "Citilink", "Xcom", "Komus"]},
        "supplier_candidates": checked, "quotes": quotes,
        "price_request_candidates": requests,
        "search_report": {
            "marketplaces_checked": 3, "federal_named_sources_checked": 4,
            "specialist_sites_found": 2, "local_stores_found": 3,
            "prices_passing_threshold": sum(x.get("public_price") is not None and x["public_price"] <= THRESHOLD for x in checked),
            "suppliers_passed": sum(x["verification_status"].startswith("✅") for x in checked),
            "manual_review": sum(x["verification_status"].startswith("🟡") for x in checked),
            "high_risk": sum(x["verification_status"].startswith("🔴") for x in checked),
            "insufficient_data": sum(x["verification_status"].startswith("⚪") for x in checked),
            "price_request_candidates": len(requests),
        },
        "limitations": [
            "Маркетплейсы не дали подтверждаемой публичной карточки точной модели в доступной выдаче.",
            "Rusprofile, WHOIS/RDAP, Wayback, CMS и reverse-IP доступны не для всех сайтов; отсутствующие данные не выдумывались.",
            "Публичные цены не подтверждены счётом и не являются purchase_price.",
        ],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))

