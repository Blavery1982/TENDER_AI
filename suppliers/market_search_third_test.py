"""Зафиксированный результат ограниченного свежего web-поиска RC-TWN28HN."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from suppliers.market_search import (SUPPLIER_TARGET_DISCOUNT_PERCENT,
                                     build_call_lists, classify_source,
                                     prepare_offers, supplier_target_price)
from suppliers.arbitration import check_kad
from suppliers.verification import verify_supplier

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data/supplier_market_search_third_test.json"
MODEL = "RC-TWN28HN"
THRESHOLD = supplier_target_price(CUSTOMER_PRICE := 25_400.0)
CHECKED_AT = "2026-09-09T00:00:00+03:00"


def offer(name, url, price, availability, *, old_price=None, quantity=None,
          delivery_price=None, delivery_terms=None, city=None, pickup=None,
          local=False, card_status="Карточка подтверждена", stale=False):
    domain = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return {
        "exact_model": True, "model": MODEL,
        "product_name": f"Royal Clima {MODEL}", "product_url": url,
        "domain": domain, "supplier_name": name, "public_price": price,
        "old_price": old_price, "price_currency": "RUB", "purchase_price": None,
        "availability": availability, "available_quantity": quantity,
        "delivery_price": delivery_price, "delivery_terms": delivery_terms,
        "delivery_city_region": city, "pickup_available": pickup,
        "checked_at": CHECKED_AT, "date_checked": "2026-09-09",
        "source_type": classify_source(domain, local=local),
        "card_status": card_status, "stale_or_wrong": stale,
        "verification_checks": {
            "commercial_site_confirmed": True,
            "commercial_site_evidence": "Доступна карточка точной модели с условиями продажи",
            "domain_age_years": None, "wayback_status": "unavailable",
            "company": {}, "cms_status": "unavailable", "ip_status": "unavailable",
        },
    }


def run_test() -> dict:
    raw = [
        offer("СтройКлим", "https://stroyklim.com/product/konditsioner-royal-clima-rc-twn28hn/",
              23850, "in_stock", old_price=26500, quantity=1, delivery_price=699,
              delivery_terms="Доставка; самовывоз бесплатно, товар со склада в Чебоксарах",
              city="Чебоксары", pickup=True),
        offer("ClimaArt", "https://climaart.ru/triumph/konditsioner-royal-clima-rc-twn28hn",
              23900, "in_stock", city="Россия", pickup=None),
        offer("Royal-Rus", "https://royal-rus.ru/bytovye-konditsionery/seriya-triumph/nastennyy-konditsioner-royal-clima-rc-twn28hn/",
              26490, "card_available", old_price=33890, delivery_price=0,
              delivery_terms="Бесплатно по Москве от 10 000 ₽; доставка по России",
              city="Москва", pickup=None, local=True),
        offer("Точка Холода", "https://tochkaholoda.ru/catalog/kondicioner-royal-clima-rc-twn28hn",
              26490, "in_stock", delivery_terms="Цена доставки зависит от расстояния и объёма",
              city="Москва", pickup=None, local=True),
        offer("CityClimat", "https://cityclimat.ru/shop/byt/nastennye-konditsionery/royal-clima/rc-twn28hn/",
              26490, "card_available", delivery_price=0,
              delivery_terms="Бесплатная доставка по Москве; самовывоз со склада",
              city="Москва", pickup=True, local=True),
        offer("SDK-Climat", "https://www.sdk-climat.ru/kondicionery/split-sistemy-nastennye/royal-clima-1/triumph/rc-twn28hn",
              26990, "in_stock", city="Новосибирск", pickup=True),
        offer("Royal Clima — специализированный магазин", "https://kondicionery-royal-clima.ru/nastennye-split-sistemy-royal-clima/nastennye-split-sistemy-royal-clima-triumph/rc-twn28hn",
              29390, "in_stock", old_price=29890, delivery_price=0,
              delivery_terms="Доставка и самовывоз заявлены бесплатно", city="Москва", pickup=True,
              local=True),
        offer("ЕСС Холод", "https://ess-holod.ru/kondicionery/nastennye-kondicionery-dlya-doma/rc-twn28hn",
              29890, "in_stock", city="Саратов", pickup=None),
        offer("ZIWO", "https://ziwo.ru/Royal-Clima-RC-TWN28HN/", 32700, "in_stock",
              delivery_price=390, delivery_terms="Доставка по Москве; индивидуальная скидка от 3 шт.",
              city="Москва", pickup=True, local=True),
        offer("PromKlimat", "https://www.promklimat.ru/Royal-Clima-RC-TWN28HN.htm",
              43810, "card_available", delivery_price=0,
              delivery_terms="Доставка по Москве бесплатно", city="Москва", pickup=None, local=True),
        offer("Stoking", "https://stoking.ru/products/klassicheskie-split-sistemy-royal-clima-serii-triumph-upgrade-rc-twn28hn",
              None, "price_on_request", delivery_terms="Доставка по РФ; специальные условия для юрлиц",
              city="Пермь", pickup=None),
        offer("CLIMSTORE", "https://climstore.ru/product/royal-clima-rc-twn28hn-split-sistema/",
              None, "in_stock", delivery_price=0,
              delivery_terms="Доставка по Москве, самовывоз и доставка до ТК бесплатно",
              city="Москва", pickup=True, local=True),
        offer("СДМ Климат", "https://sdmclimate.ru/kondicionery/nastennye-kondicionery/nastennaya-split-sistema-royal-clima-rc-twn28hn",
              None, "price_on_request", delivery_price=700,
              delivery_terms="Москва в пределах МКАД, 1–3 дня", city="Москва", pickup=None, local=True),
        offer("Vozduhoff", "https://www.vozduhoff.ru/catalog/rasprodazha/royal-clima-rc-twn28hn-triumph-new/",
              26490, "in_stock", delivery_price=0,
              delivery_terms="Карточка одновременно сообщает, что модель снята с производства",
              city="Москва", pickup=True, local=True, stale=True),
    ]
    verified = [verify_supplier(x) for x in raw]
    # Для ранее глубоко проверенных доменов используем уже собранное доказательное
    # заключение, но сохраняем свежую карточку, цену и наличие текущего поиска.
    deep_path = ROOT / "data/supplier_verification_deep_test.json"
    if deep_path.exists():
        deep = {x["domain"]: x for x in json.loads(deep_path.read_text(encoding="utf-8"))["sites"]}
        for item in verified:
            known = deep.get(item["domain"])
            if known:
                for key in ("verification_status", "verification_comment", "risk_flags",
                            "positive_signals", "unavailable_checks", "verification_evidence"):
                    item[key] = known.get(key, item.get(key))
    prepared = prepare_offers(verified, MODEL, THRESHOLD)
    lists = build_call_lists(prepared)
    active = [x for x in prepared if not x.get("stale_or_wrong")]
    result = {
        "procurement": {"trade_number": "100205573126100053", "model": MODEL,
                        "quantity": 3, "customer_unit_price": CUSTOMER_PRICE,
                        "nmck": 76200,
                        "supplier_target_discount_percent": SUPPLIER_TARGET_DISCOUNT_PERCENT,
                        "supplier_target_price": THRESHOLD,
                        "delivery_city_region": "Москва, ул. Матросская Тишина, д. 18"},
        "search": {"searched_at": datetime.now(timezone.utc).isoformat(),
                   "fresh_price_check_date": "2026-09-09",
                   "marketplaces": [
                       {"name": "Ozon", "status": "Карточка точной модели не найдена в доступной выдаче"},
                       {"name": "Wildberries", "status": "Карточка точной модели не найдена в доступной выдаче"},
                       {"name": "Яндекс Маркет", "status": "Карточка точной модели не найдена в доступной выдаче"}],
                   "federal": [
                       {"name": "ВсеИнструменты", "status": "Найдена инструкция с моделью, товарная карточка не найдена"},
                       {"name": "Citilink", "status": "Карточка точной модели не найдена"},
                       {"name": "Xcom", "status": "Карточка точной модели не найдена"},
                       {"name": "Komus", "status": "Карточка точной модели не найдена"}]},
        "offers": prepared, "active_exact_offers": len(active),
        "unique_suppliers": len({x["domain"] for x in active}),
        "minimum_confirmed_public_price": min(x["public_price"] for x in active if x["public_price"] is not None),
        "offers_passing_target": sum(x.get("public_price") is not None and x["public_price"] <= THRESHOLD for x in active),
        **lists,
        "excluded_stale_or_wrong": [x for x in prepared if x.get("stale_or_wrong")],
        "automatic_access_limitations": [
            "Маркетплейсы могут ограничивать выдачу по региону, авторизации и CAPTCHA; защита не обходилась.",
            "Остаток 3 шт. почти нигде не опубликован: у одной карточки указан только 1 шт.",
            "Большинство профильных сайтов не публикует проверяемые юридические реквизиты прямо в карточке; им присвоена ручная проверка перед оплатой.",
            "Цена по запросу не является публичной ценой; запросы поставщикам не отправлялись.",
            "Публичная цена не является закупочной ценой и во всех записях purchase_price оставлена пустой.",
        ],
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def rerank_saved_result() -> dict:
    """Пересчитывает сохранённый рынок без web-поиска и supplier_verification."""
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    procurement = result["procurement"]
    procurement.pop("target_price_80_percent", None)
    procurement["supplier_target_discount_percent"] = SUPPLIER_TARGET_DISCOUNT_PERCENT
    procurement["supplier_target_price"] = supplier_target_price(procurement["customer_unit_price"])
    threshold = procurement["supplier_target_price"]
    for offer_item in result["offers"]:
        if "arbitration_cases" not in offer_item:
            checks = offer_item.get("verification_checks") or {}
            company = checks.get("current_company") or checks.get("company") or {}
            kad = check_kad(company.get("inn"))
            offer_item["arbitration_cases"] = kad
            offer_item["arbitration_cases_display"] = kad["reason"]
    prepared = prepare_offers(result["offers"], procurement["model"], threshold)
    active = [x for x in prepared if not x.get("stale_or_wrong")]
    result["offers"] = prepared
    result["offers_passing_target"] = sum(
        x.get("public_price") is not None and x["public_price"] <= threshold for x in active)
    lists = build_call_lists(prepared)
    result.update(lists)
    result["suppliers_for_call"] = (lists["suppliers_for_best_price_request"]
                                    + lists["requires_verification_before_work"])
    result["summary"] = {
        "active_exact_offers": len(active),
        "unique_suppliers": len({x["domain"] for x in active}),
        "supplier_target_discount_percent": SUPPLIER_TARGET_DISCOUNT_PERCENT,
        "supplier_target_price": threshold,
        "offers_passing_target": result["offers_passing_target"],
        "minimum_confirmed_public_price": result["minimum_confirmed_public_price"],
        "internet_search_repeated": False,
        "supplier_verification_repeated": False,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run_test(), ensure_ascii=False, indent=2))
