"""Поиск реквизитов не только в карточке товара, но и на служебных страницах."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from suppliers.verification import normalize_domain

STANDARD_PATHS = ("/contacts", "/contact", "/rekvizity", "/requisites", "/about",
                  "/company", "/o-kompanii", "/info", "/legal", "/privacy",
                  "/privacy-policy", "/privacypolicy", "/data-processing",
                  "/policy", "/offer", "/oferta", "/delivery", "/payment")
LEGAL_WORDS = ("контакт", "реквиз", "компан", "о нас", "политик", "оферт",
               "достав", "оплат", "соглаш")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.text=[]; self.links=[]; self.title=""; self._title=False
        self._ignored = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
            return
        if self._ignored:
            return
        attrs=dict(attrs)
        if tag == "a" and attrs.get("href"): self.links.append(attrs["href"])
        if tag == "title": self._title=True
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1
            return
        if tag == "title": self._title=False
    def handle_data(self, data):
        if self._ignored:
            return
        self.text.append(data)
        if self._title: self.title += data


def _fetch(url: str, timeout: int = 12) -> tuple[str, list[str], str]:
    request=Request(url,headers={"User-Agent":"TENDER_AI/1.0 supplier verification"})
    with urlopen(request,timeout=timeout) as response:
        html=response.read(1_500_000).decode("utf-8","ignore")
    parser=PageParser(); parser.feed(html)
    return re.sub(r"\s+"," "," ".join(parser.text)).strip(), parser.links, parser.title.strip()


def extract_requisites(text: str) -> dict:
    normalized=re.sub(r"[\u00a0\s]+"," ",text)
    inns=sorted(set(re.findall(r"(?i)\bИНН\s*[:№]?\s*(\d[\d\s-]{8,16}\d)",normalized)))
    ogrns=sorted(set(re.findall(r"(?i)\bОГРН(?:ИП)?\s*[:№]?\s*(\d[\d\s-]{11,18}\d)",normalized)))
    clean=lambda values,lengths: sorted({re.sub(r"\D","",x) for x in values if len(re.sub(r"\D","",x)) in lengths})
    companies=sorted(set(re.findall(r"\b(?:ООО|АО|ПАО|ИП)\s+[«\"']?[^«»\"'\n,.;]{2,80}",normalized)))
    emails=sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}",normalized)))
    phones=sorted(set(re.findall(r"(?:\+7|8)[\s()\-\d]{9,20}",normalized)))
    return {"inn":clean(inns,{10,12}),"ogrn":clean(ogrns,{13,15}),
            "companies":companies[:10],"emails":emails[:10],"phones":[x.strip() for x in phones[:10]]}


def determine_current_seller(pages: list[dict]) -> dict:
    """Устанавливает продавца по текущим страницам, без обращения к архивным ИНН."""
    inns = sorted({inn for page in pages for inn in page["requisites"]["inn"]})
    legal_pages = [page for page in pages if page.get("is_legal_page")
                   and page["requisites"]["inn"]]
    determined = len(inns) == 1 and bool(legal_pages)
    return {"determined": determined, "inn": inns[0] if determined else None,
            "found_inns": inns, "sources": [page["url"] for page in legal_pages],
            "reason": None if determined else "Актуальный продавец не установлен однозначно по текущим юридическим страницам"}


def crawl_legal_pages(base_url: str, max_pages: int = 24) -> dict:
    domain=normalize_domain(base_url); base=f"https://{domain}"
    queue=[base + path for path in STANDARD_PATHS] + [base]
    visited=set(); pages=[]
    while queue and len(visited) < max_pages:
        url=queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            text,links,title=_fetch(url)
        except (HTTPError,URLError,TimeoutError,OSError):
            continue
        data=extract_requisites(text)
        if any(data.values()): pages.append({"url":url,"title":title,"requisites":data})
        for href in links:
            absolute=urljoin(url,href)
            if normalize_domain(absolute)==domain and any(x in absolute.casefold() for x in LEGAL_WORDS):
                queue.append(absolute)
    inns={inn for p in pages for inn in p["requisites"]["inn"]}
    ogrns={x for p in pages for x in p["requisites"]["ogrn"]}
    return {"status":"completed","checked_at":datetime.now(timezone.utc).isoformat(),
            "pages_checked":len(visited),"pages_with_evidence":pages,
            "inn":sorted(inns),"ogrn":sorted(ogrns),
            "requisites_consistent":len(inns)<=1 and len(ogrns)<=1,
            "conflict":len(inns)>1 or len(ogrns)>1}
