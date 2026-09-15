"""Ограниченный сбор открытых свидетельств для уже выбранного поставщика."""
from urllib.parse import urljoin, urlsplit

from suppliers.external_checks import rdap_check, wayback_check
from suppliers.site_requisites import PageParser, extract_requisites, LEGAL_WORDS, determine_current_seller
from suppliers.verification import normalize_domain, verify_supplier
from suppliers.arbitration import unavailable_kad_result, minimal_kad_result
from suppliers.kad_client import KADClient, save_supplier_kad_evidence


def verify_live_supplier(offer, *, reader, max_legal_pages=3, kad_client=None,
                         company_checker=None, evidence_directory=None):
    domain = normalize_domain(offer["product_url"])
    base = f"https://{domain}"
    urls = [offer["product_url"], base + "/contacts", base + "/requisites"]
    evidence, errors, visited = [], [], set()
    legal_paths = ("contact", "requisit", "rekviz", "ofert", "offer", "payment", "legal", "company", "about")
    while urls and len(visited) < max_legal_pages:
        url = urls.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            body, final, status = reader(url)
            if status >= 400:
                raise ConnectionError("Юридическая страница недоступна")
            if normalize_domain(final) != domain:
                errors.append({"url": url, "error": "Другой домен после перехода"})
                continue
            parser = PageParser()
            parser.feed(body)
            text = " ".join(parser.text)
            requisites = extract_requisites(text)
            is_legal = final != offer["product_url"] and any(
                part.split(".")[0] in legal_paths + ("contacts", "rekvizity", "requisites", "oferta")
                for part in urlsplit(final).path.casefold().split("/"))
            evidence.append({"url": final, "requisites": requisites,
                             "is_legal_page": is_legal})
            links = [urljoin(final, href) for href in parser.links
                     if any(word in href.casefold() for word in LEGAL_WORDS + legal_paths)]
            urls[0:0] = [link for link in links if normalize_domain(link) == domain and link not in visited]
        except Exception as exc:
            errors.append({"url": url, "error": type(exc).__name__})
    seller = determine_current_seller(evidence)
    inns = seller["found_inns"]
    company = {"inn": seller["inn"], "active": None}
    company_error = None
    if seller["determined"] and company_checker is not None:
        try:
            checked_company = company_checker(seller["inn"])
            if (checked_company.get("inn") == seller["inn"] and checked_company.get("source")
                    and isinstance(checked_company.get("active"), bool)):
                company.update(checked_company)
            else:
                company_error = "Реестр не подтвердил компанию по текущему ИНН"
        except Exception as exc:
            company_error = type(exc).__name__
    if seller["determined"]:
        context = getattr(getattr(reader, "__self__", None), "context", None)
        client = kad_client if kad_client is not None else KADClient(context=context)
        kad = minimal_kad_result(client.check(seller["inn"]), seller["inn"])
    else:
        kad = unavailable_kad_result(None, seller["reason"],
                                     status="KAD_CURRENT_SELLER_UNDETERMINED")
    rdap = rdap_check(domain)
    history = wayback_check(domain)
    checks = {**rdap, **history, "domain_source": rdap.get("source"),
              "commercial_site_confirmed": bool(evidence),
              "commercial_site_evidence": "Прочитана карточка точной модели; публичная цена подтверждена",
              "legal_source": [page["url"] for page in evidence],
              "company": company, "current_company": company,
              "requisites_consistent": True if len(inns) == 1 else False if len(inns) > 1 else None,
              "current_seller_determined": seller["determined"],
              "company_registry_status": "available" if company.get("active") is not None else "unavailable",
              "company_registry_error": company_error, "kad_check": kad}
    result = verify_supplier({**offer, "verification_checks": checks})
    missing = ["История тематики сайта", "Получатель будущего счёта"]
    if company.get("active") is None:
        missing.append("Статус юрлица в реестре")
    if kad["technical_status"] != "KAD_CHECKED":
        missing.append("КАД")
    result.update(legal_pages=evidence, legal_page_errors=errors,
                  current_seller=seller,
                  kad_evidence_path=save_supplier_kad_evidence(domain, seller, kad, directory=evidence_directory),
                  live_checks_performed=["Страницы продавца и реквизиты", "RDAP/WHOIS", "Wayback CDX"]
                      + (["КАД"] if kad["technical_status"] == "KAD_CHECKED" else [])
                      + (["Статус юрлица в реестре"] if company.get("active") is not None else []),
                  live_checks_missing=missing)
    return result
