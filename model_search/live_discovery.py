"""Universal, bounded live discovery of product models.

The module deliberately separates search/discovery from strict compliance.  Search
snippets can suggest a candidate, but never prove a tender requirement.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from model_search.search_mode import (determine_model_search_mode, blocked_discovery_result,
                                      EXACT_MODEL_OR_EQUIVALENT)
from model_search.query_planning import generate_queries, plausible_candidates
from model_search.product_evidence import (exact_model_key, same_exact_model, extract_skus,
    candidates_from_results, html_page, document_pages, source_priority, page_matches, public_offer)
from model_search.compliance import (assess_model, check_requirement, parameter_key,
                                     CORRESPONDS, MISMATCH, UNKNOWN)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data/cache/model_discovery"
TECH_TTL_DAYS = 30
MARKET_TTL_HOURS = 24
QUICK_FILTER_KEYS = {
    'product_type', 'inverter', 'room_area', 'cooling_capacity',
    'heating_capacity', 'mounting', 'outdoor_unit', 'duplex',
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**x, "parameter": x.get("parameter") or x.get("requirement_name") or "Требование",
             "required_value": x.get("required_value", x.get("value", "Требуется")),
             "operator": x.get("operator"), "unit": x.get("unit")} for x in requirements]


def quick_filter_requirements(requirements: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Choose a few high-discrimination mandatory requirements for cheap rejection."""
    mandatory=[row for row in requirements
               if row.get('mandatory',True) is not False and row.get('criticality')!='optional']
    strong=[row for row in mandatory if parameter_key(
        row.get('parameter') or row.get('requirement_name') or '') in QUICK_FILTER_KEYS]
    return (strong or mandatory)[:limit]


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[dict[str, str]]: ...
    def fetch(self, url: str) -> str | dict[str, Any] | list[dict[str, Any]]: ...


@dataclass
class WebProvider:
    timeout: float = 12
    retries: int = 2
    backoff: float = .8
    user_agent: str = "TENDER_AI/1.0 local model research"
    verified_sources: dict[str, str] = field(default_factory=dict)

    def _get(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req=urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept-Language":"ru,en;q=.8"})
                with urllib.request.urlopen(req,timeout=self.timeout) as response:
                    return response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8","replace")
            except urllib.error.HTTPError as exc:
                last=exc
                if exc.code not in (403,429) and exc.code < 500: break
            except (urllib.error.URLError,TimeoutError) as exc: last=exc
            if attempt < self.retries: time.sleep(self.backoff * (2**attempt) + random.random()/10)
        raise RuntimeError(f"HTTP недоступен после повторов: {type(last).__name__}") from last

    def search(self, query: str, limit: int = 6) -> list[dict[str, str]]:
        # Bing's documented RSS representation is simple, keyless and does not
        # require executing browser JavaScript. It is only candidate discovery.
        body=self._get("https://www.bing.com/search?format=rss&q="+urllib.parse.quote(query))
        root=ET.fromstring(body)
        rows=[]
        for item in root.findall("./channel/item"):
            url=(item.findtext("link") or "").strip(); title=(item.findtext("title") or "").strip()
            if url: rows.append({"url":url,"title":title,"snippet":item.findtext("description") or ""})
            if len(rows)>=limit: break
        return rows

    def fetch(self, url: str) -> dict[str, Any] | list[dict[str, Any]]:
        host=urllib.parse.urlsplit(url).hostname or ""
        source_type=self.verified_sources.get(host,"other")
        metadata={"source_type":source_type,"source_verified":host in self.verified_sources}
        req=urllib.request.Request(url,headers={"User-Agent":self.user_agent})
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            body=response.read(10_000_001)
            if len(body)>10_000_000: raise ValueError("Source exceeds document limit")
            mime=response.headers.get_content_type()
            final_url=response.geturl()
            encoding=response.headers.get_content_charset() or 'utf-8'
        # Redirected hosts do not inherit verified status from the starting URL.
        if urllib.parse.urlsplit(final_url).hostname!=host:
            metadata={"source_type":"other","source_verified":False}
        suffix=Path(urllib.parse.urlsplit(final_url).path).suffix.lower()
        if mime=='application/pdf' or body.startswith(b'%PDF'): suffix='.pdf'
        if suffix in ('.pdf','.docx','.doc','.xlsx'):
            with tempfile.TemporaryDirectory(prefix='tender_model_document_') as directory:
                path=Path(directory)/('source'+suffix);path.write_bytes(body)
                return document_pages(path,final_url,**{**metadata,'source_type':'official_document' if metadata['source_verified'] and source_type=='manufacturer' else metadata['source_type']})
        return {**html_page(body.decode(encoding,'replace'),final_url),**metadata}


def _source_priority(url: str, text: str = "") -> int:
    # Compatibility helper: a URL or the word "official" does not prove ownership.
    return 4


def _candidate_models(results, justification=None, product_name=None):
    candidates=candidates_from_results(results)
    # Реальное коммерческое имя из открытой карточки не обязано содержать SKU.
    # Заголовок выдачи/сниппет не считается таким подтверждением.
    noun_tokens=re.findall(r'[A-Za-zА-Яа-яЁё0-9]+',str(product_name or ''))
    for row in results:
        heading=row.get('verified_product_heading') or ''
        if (not heading or len(heading)>240 or not noun_tokens
                or not all(re.search(r'(?i)(?<!\w)'+re.escape(t)+r'(?!\w)',heading) for t in noun_tokens)
                or extract_skus(heading)):
            continue
        if any(same_exact_model(c['sku'],heading) for c in candidates):continue
        candidates.append({'brand':None,'model':heading,'sku':heading,'exact_model':heading,
                           'model_name':heading,'source_url':row['url'],'source_title':heading,
                           'candidate_source':'opened_product_heading','sources':[row['url']]})
    if justification:
        skus=extract_skus(justification);sku=skus[0] if len(skus)==1 else justification
        found=next((c for c in candidates if same_exact_model(c['sku'],sku)),None)
        if found: candidates.remove(found)
        candidates.insert(0,{**(found or {}),'brand':(found or {}).get('brand'),'model':sku,'sku':sku,
            'exact_model':justification,'model_name':justification,'candidate_source':'price_justification',
            'source_url':(found or {}).get('source_url'),'source_title':(found or {}).get('source_title'),
            'sources':(found or {}).get('sources',[])})
    return candidates


def _requirement_check(requirement, pages, sku=None):
    identities={x for page in pages for x in extract_skus(page.get('text',''))}
    return check_requirement(requirement,pages,sku or (next(iter(identities)) if len(identities)==1 else ''))


def _cache_key(item: dict[str,Any]) -> str:
    payload={"policy_version": 3, "mode_input": item, "name":item.get("item_name") or item.get("name"),"requirements":normalize_requirements(item.get("structured_requirements") or item.get("requirements") or [])}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _fresh(data: dict[str,Any], now: datetime) -> bool:
    try:
        if data.get("search_status") != "completed": return False
        checked=datetime.fromisoformat(data["checked_at"]); return now-checked <= timedelta(hours=MARKET_TTL_HOURS)
    except (KeyError,TypeError,ValueError): return False


def select_cheapest_compliant_model(candidates):
    eligible = [c for c in candidates if c.get('status') == 'fully_compliant'
                and c.get('production_status') != 'discontinued'
                and c.get('public_price') is not None
                and c.get('russia_availability') == 'available']
    return min(eligible, key=lambda c: c['public_price'], default=None)


def discover_models(item_requirements: dict[str,Any], *, provider: SearchProvider | None = None,
                    query_limit: int = 3, results_per_query: int = 6, candidate_limit: int = 8,
                    cache_dir: Path = CACHE_DIR, use_cache: bool = True,
                    logger: logging.Logger | None = None) -> dict[str,Any]:
    decision=determine_model_search_mode(item_requirements)
    if not decision['model_discovery_allowed']: return blocked_discovery_result(decision)
    # Для exact-or-equivalent сначала ищем исходную модель даже без таблицы
    # характеристик: эквивалент будет допущен только после отдельной проверки.
    if (decision['model_search_mode'] != EXACT_MODEL_OR_EQUIVALENT
            and not (item_requirements.get('structured_requirements') or item_requirements.get('requirements'))):
        return {**blocked_discovery_result(decision), 'warnings': ['Нет обязательных требований из разрешённых источников']}
    provider=provider or WebProvider();now=datetime.now(timezone.utc);checked=now.isoformat()
    cache_path=cache_dir/f"{_cache_key(item_requirements)}.json"
    if use_cache and cache_path.exists():
        cached=json.loads(cache_path.read_text(encoding='utf-8'))
        if _fresh(cached,now):
            return {**cached,'cache_status':'fresh_hit','live_queries_count':0}
    plan=generate_queries(item_requirements,query_limit)
    queries=[];results={};pages_by_url={};warnings=[]
    justification=item_requirements.get('price_justification_model') or item_requirements.get('model_from_justification')
    for number,query in enumerate(plan):
        if number==2 and len(plausible_candidates(list(results.values()),item_requirements))>=3: break
        queries.append(query)
        if logger: logger.info('stage=MODEL_DISCOVERY item=%s query=%s',item_requirements.get('item_number'),query)
        try:
            for row in provider.search(query,results_per_query):
                if row.get('url'): results.setdefault(row['url'],dict(row))
        except Exception as exc:
            warnings.append(f'Поисковый запрос частично недоступен: {type(exc).__name__}')
        # Bounded fetching of already discovered URLs; no recursive crawling.
        for url,row in list(results.items())[:12]:
            if url in pages_by_url: continue
            pages_by_url[url]=[]
            try:
                fetched=provider.fetch(url)
                pages=fetched if isinstance(fetched,list) else [fetched if isinstance(fetched,dict) else
                    {'url':url,'text':str(fetched),'content_kind':'product_page'}]
                for page in pages:page.setdefault('url',url)
                pages_by_url[url]=pages
                row['page_title']=' '.join(p.get('title','')+' '+p.get('heading','') for p in pages)
                row['verified_product_heading']=next((p.get('heading') for p in pages if p.get('heading') and p.get('content_kind')=='product_page'),None)
                row['page_text']='\n'.join(p.get('text','') for p in pages)
            except Exception as exc:
                warnings.append(f'Источник кандидата недоступен: {type(exc).__name__}')
    candidates=_candidate_models(list(results.values()),justification,item_requirements.get('product_name'))
    original=decision.get('original_model')
    if original:
        skus=extract_skus(original);sku=skus[0] if len(skus)==1 else original
        found=next((c for c in candidates if same_exact_model(c['sku'],sku)),{})
        candidates=[c for c in candidates if c is not found]
        candidates.insert(0,{**found,'exact_model':original,'model_name':original,'model':sku,'sku':sku,
            'brand':found.get('brand'),'candidate_source':'customer_specification','sources':found.get('sources',[]),
            'source_url':found.get('source_url'),'source_title':found.get('source_title')})
    candidates=candidates[:candidate_limit]
    requirements=normalize_requirements(item_requirements.get('structured_requirements') or item_requirements.get('requirements') or [])
    checked_candidates=[]
    for candidate in candidates:
        sku=candidate['sku']
        pages=[p for group in pages_by_url.values() for p in group if page_matches(p,sku)]
        quick_requirements=quick_filter_requirements(requirements)
        quick=assess_model(quick_requirements,pages,sku)
        if quick['status']=='non_compliant':
            assessment={**quick,'quick_filter_status':'rejected',
                        'deep_compliance_performed':False}
        else:
            assessment={**assess_model(requirements,pages,sku),
                        'quick_filter_status':'passed_or_unconfirmed',
                        'quick_filter_checks':quick['requirements_check'],
                        'deep_compliance_performed':True}
        offers=[offer for p in pages if (offer:=public_offer(p,sku)) is not None]
        official=[p['url'] for p in pages if source_priority(p)<=1]
        available=[o['url'] for o in offers if o['available']]
        discontinued=any(re.search(r'снят[аоы]? с производства|discontinued|obsolete',p.get('text',''),re.I) for p in pages)
        checked_candidates.append({**candidate,**assessment,'source_urls':list(dict.fromkeys(p['url'] for p in pages)),
            'official_sources':list(dict.fromkeys(official)),'russian_availability_sources':available,
            'public_price_sources':offers,'public_price':min((o['price'] for o in offers),default=None),'purchase_price':None,
            'production_status':'discontinued' if discontinued else 'production_not_confirmed',
            'russia_availability':'available' if available else 'not_confirmed','checked_at':checked,'warnings':[]})
    compliant=[c for c in checked_candidates if c['status']=='fully_compliant' and c['production_status']!='discontinued']
    selected=select_cheapest_compliant_model(checked_candidates)
    result={**decision,'search_status':'completed' if candidates and not warnings else 'partial','queries_planned':plan,
        'queries_used':queries,'live_queries_count':len(queries),'candidates_found':len(candidates),
        'candidates_checked':len(checked_candidates),'fully_compliant_count':len(compliant),'candidates':checked_candidates,
        'selected_model':({**selected,'selection_reason':'Самая дешёвая полностью соответствующая доступная модель среди проверенных кандидатов'} if selected else None),
        'selected_model_public_price':selected['public_price'] if selected else None,'purchase_price':None,
        'requirements_confirmed':sum(c['result']==CORRESPONDS for x in checked_candidates for c in x['requirements_check']),
        'requirements_unconfirmed':sum(c['result']==UNKNOWN for x in checked_candidates for c in x['requirements_check']),
        'rejected_candidates':[c['exact_model'] for c in checked_candidates if c['status']=='non_compliant'],
        'warnings':list(dict.fromkeys(warnings)),'checked_at':checked,
        'technical_fresh_until':(now+timedelta(days=TECH_TTL_DAYS)).isoformat(),
        'market_fresh_until':(now+timedelta(hours=MARKET_TTL_HOURS)).isoformat(),'cache_status':'miss'}
    cache_path.parent.mkdir(parents=True,exist_ok=True)
    cache_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
