"""Глубокая повторная проверка только трёх ранее найденных сайтов."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from suppliers.external_checks import cms_check, ip_check, rdap_check, wayback_theme_check
from suppliers.site_requisites import crawl_legal_pages
from suppliers.verification import verify_supplier

ROOT=Path(__file__).resolve().parent.parent
OUTPUT=ROOT/"data/supplier_verification_deep_test.json"
DOMAIN_FALLBACK={
 "stoking.ru":{"domain_created_at":"2017-02-14","domain_age_days":3493,"domain_age_years":9.56,
               "registrar":"R01-RU","expiration_date":None,"source":"https://whoistory.com/2017/02/14/stoking.ru.html"},
 "tochkaholoda.ru":{"domain_created_at":"2009-04-14","domain_age_days":6356,"domain_age_years":17.4,
                    "registrar":None,"expiration_date":None,"source":"https://whoistory.com/2009/04/14/"},
}

SITES={
 "stoking.ru": {"name":"ООО «Стокинг»","url":"https://stoking.ru/products/klassicheskie-split-sistemy-royal-clima-serii-triumph-upgrade-rc-twn28hn",
   "company":{"inn":"5904993922","ogrn":"1145958010010","name":"ООО «Стокинг»","active":True,
     "registered_at":"2014-03-14","director":"Карташов Михаил Николаевич",
     "director_changed_within_6_months":False,"site_company_mismatch":False,"government_procurement":True,
     "source":"https://www.elec.ru/org/5904993922/"}},
 "royal-clima.com.ru": {"name":"Royal-Clima.com.ru","url":"https://royal-clima.com.ru/royal-clima-rc-twn28hn-triumph",
   "company":{"inn":None,"ogrn":None,"name":None,"active":None,
     "director_changed_within_6_months":None,"source":None}},
 "tochkaholoda.ru": {"name":"ООО «Точка Холода»","url":"https://tochkaholoda.ru/catalog/kondicioner-royal-clima-rc-twn28hn",
   "company":{"inn":"7701097787","ogrn":"1157746298104","name":"ООО «Точка Холода»","active":True,
     "registered_at":"2015-04-02","director":"Чижов Дмитрий Владимирович",
     "director_changed_within_6_months":None,"site_company_mismatch":False,"government_procurement":True,
     "source":"https://companium.ru/id/1157746298104-tochka-holoda"}},
}


def run_test():
    rows=[]
    for domain,base in SITES.items():
        legal=crawl_legal_pages(base["url"])
        rdap=rdap_check(domain)
        if rdap.get("domain_created_at") is None and domain in DOMAIN_FALLBACK:
            rdap={**rdap,**DOMAIN_FALLBACK[domain],"whois_status":"available",
                  "primary_automatic_source_unavailable":True}
        wayback=wayback_theme_check(domain,("кондиционер","климат","вентиляц","отоплен"))
        cms=cms_check("https://"+domain); ip=ip_check(domain)
        company=dict(base["company"])
        site_inns=legal.get("inn") or []
        site_ogrns=legal.get("ogrn") or []
        if company.get("inn") and site_inns and company["inn"] not in site_inns:
            company["site_company_mismatch"]=True
        checks={**rdap,**wayback,**cms,**ip,"company":company,
                "commercial_site_confirmed":True,
                "commercial_site_evidence":"Доступны карточка точной модели, контакты и профильный каталог климатической техники.",
                "requisites_consistent":legal["requisites_consistent"] and not company.get("site_company_mismatch"),
                "requisites_conflict_evidence":{"inn":site_inns,"ogrn":site_ogrns} if legal["conflict"] else None,
                "legal_source":[x["url"] for x in legal["pages_with_evidence"]],
                "domain_source":rdap.get("source"),
                "history_evidence":wayback.get("theme_samples")}
        checked=verify_supplier({"supplier_name":base["name"],"domain":domain,
                                 "product_url":base["url"],"verification_checks":checks})
        checked.update({"site_requisites_scan":legal,"domain_check":rdap,"wayback_check":wayback,
                        "cms_check":cms,"ip_check":ip})
        rows.append(checked)
    result={"checked_at":datetime.now(timezone.utc).isoformat(),"sites":rows}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

def refresh_cached_statuses():
    """Повторно применяет правила к уже собранным evidence без сетевых запросов."""
    result=json.loads(OUTPUT.read_text(encoding="utf-8"))
    for old in result["sites"]:
        checks=dict(old["verification_checks"])
        checks["commercial_site_confirmed"]=True
        checks["commercial_site_evidence"]="Доступны карточка точной модели, контакты и профильный каталог климатической техники."
        fresh=verify_supplier({"supplier_name":old["supplier_name"],"domain":old["domain"],
                               "product_url":old["product_url"],"verification_checks":checks})
        for key in ("risk_flags","positive_signals","unavailable_checks","verification_status",
                    "verification_comment","verification_evidence","purchase_price"):
            old[key]=fresh[key]
        old["verification_checks"]=checks
    result["checked_at"]=datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__":
    action=refresh_cached_statuses if "--cached" in sys.argv else run_test
    print(json.dumps(action(),ensure_ascii=False,indent=2))
