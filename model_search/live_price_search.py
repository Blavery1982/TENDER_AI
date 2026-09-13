"""Small live price MVP for an exact SKU using already discovered Russian URLs."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model_search.product_evidence import (html_page, page_matches, public_offer,
                                           primary_content, manufacturer_model_in_text,
                                           same_exact_model)
from model_search.product_evidence import extract_skus
from suppliers.market_search import normalize_model, normalize_product_url
from model_search.candidate_discovery import discover_candidate_urls

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_SEEDS=ROOT/'data/supplier_market_search_third_test.json'
OUTPUT=ROOT/'data/model_price_search_live_test.json'
MARKET='https://market.yandex.ru/search?text='
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36'


def _now(): return datetime.now(timezone.utc).isoformat()


def _target_sku(model: str) -> str:
    values=extract_skus(model)
    if len(values)==1: return values[0]
    value=' '.join(str(model).split()).strip()
    if len(value)<4: raise ValueError('Не удалось однозначно выделить точную модель')
    return value


def _seed_rows(path: Path, model: str) -> list[dict[str,Any]]:
    if not path.exists(): return []
    data=json.loads(path.read_text(encoding='utf-8')); rows=data.get('offers') or data.get('supplier_candidates') or []
    target=normalize_model(_target_sku(model))
    return [r for r in rows if normalize_model(r.get('model'))==target and r.get('product_url')]


def _fetch(url: str, timeout: float=15) -> tuple[str,str,int]:
    request=urllib.request.Request(url,headers={'User-Agent':UA,'Accept-Language':'ru-RU,ru;q=.9,en;q=.6'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        body=response.read(5_000_001)
        if len(body)>5_000_000: raise ValueError('Страница превышает лимит 5 МБ')
        return body.decode(response.headers.get_content_charset() or 'utf-8','replace'),response.geturl(),response.status


def _availability(page: dict, offer: dict | None) -> str | None:
    if offer and offer.get('available'): return 'В наличии'
    text=page.get('primary_product_content') or page.get('text','')
    if re.search(r'(?i)под заказ|доступен к заказу|предзаказ',text): return 'Под заказ'
    if re.search(r'(?i)нет в наличии|товар закончился|снят с продажи',text): return 'Нет в наличии'
    if re.search(r'(?i)(?:^|\n)\s*в наличии\s*(?:\n|$)',text): return 'В наличии'
    return None


def _walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values(): yield from _walk(child)
    elif isinstance(value,list):
        for child in value: yield from _walk(child)


def _structured_offer(page: dict, sku: str) -> dict | None:
    """Accept Product JSON-LD only when its own name identifies the exact SKU."""
    found=[]
    for product in _walk(page.get('structured_products') or []):
        if product.get('@type')!='Product' or not page_matches({**page,'heading':product.get('name',''),'title':''},sku): continue
        raw=product.get('offers') or []
        for offer in raw if isinstance(raw,list) else [raw]:
            if not isinstance(offer,dict) or str(offer.get('priceCurrency','')).upper()!='RUB': continue
            try: price=float(str(offer['price']).replace(' ','').replace(',','.'))
            except (KeyError,ValueError,TypeError): continue
            if price>0: found.append({'price':price,'available':'instock' in str(offer.get('availability','')).lower()})
    return min(found,key=lambda x:x['price']) if found else None


def _supplier_sku(page: dict, manufacturer_model: str) -> str | None:
    """Preserve a seller's internal SKU separately; never use it as a gate."""
    values=[]
    for product in _walk(page.get('structured_products') or []):
        if product.get('@type')!='Product':continue
        if not manufacturer_model_in_text(product.get('name',''),manufacturer_model):continue
        value=str(product.get('sku') or '').strip()
        if value and not same_exact_model(value,manufacturer_model) and value not in values:
            values.append(value)
    return values[0] if len(values)==1 else None


def _visible_offer(page: dict) -> dict | None:
    """Conservative fallback for a single explicitly labelled current price."""
    text=primary_content(page)
    patterns=[r'(?i)текущая цена\s*:\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)\s*(?:₽|руб)',
              r'(?im)^\s*цена\s*\n\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)\s*(?:₽|руб)']
    values=[]
    for pattern in patterns:
        for raw in re.findall(pattern,text):
            value=float(re.sub(r'[\s\u00a0]','',raw).replace(',','.'))
            if value>0 and value not in values: values.append(value)
    return {'price':values[0],'available':bool(re.search(r'(?i)в наличии|есть в наличии',text))} if len(values)==1 else None


def _direct_product_url(url: str) -> bool:
    path=urllib.parse.urlsplit(url).path.casefold().rstrip('/')
    if not path or path in {'','/'}:return False
    return not any(marker in path for marker in ('/search','/category'))


def _market_seller(body: str) -> str | None:
    """Return the merchant tied to a Market product card, never the marketplace label."""
    values=[]
    for raw in re.findall(r'"supplierName"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',body):
        try:value=json.loads('"'+raw+'"')
        except (json.JSONDecodeError,TypeError):continue
        if value and value not in values:values.append(value)
    return values[0] if len(values)==1 else None


def inspect_product_url(row: dict[str,Any], model: str, *, fetcher=_fetch) -> dict[str,Any] | None:
    body,final,status=fetcher(row['product_url']); page=html_page(body,final)
    sku=_target_sku(model)
    if not _direct_product_url(final) or not page_matches(page,sku): return None
    offer=public_offer(page,sku) or _structured_offer(page,sku) or _visible_offer(page)
    market_seller=_market_seller(body) if (urllib.parse.urlsplit(final).hostname or '').endswith('market.yandex.ru') else None
    return {'exact_product_name':page.get('heading') or page.get('title') or row.get('product_name') or model,
            'model':model,'exact_manufacturer_model':sku,'exact_model_match':True,
            'supplier_sku':_supplier_sku(page,sku),
            'price':offer.get('price') if offer else None,'currency':'RUB' if offer else None,
            'availability':_availability(page,offer),'seller':market_seller or row.get('supplier_name') or urllib.parse.urlsplit(final).hostname,
            'url':normalize_product_url(final),'source':urllib.parse.urlsplit(final).hostname,'http_status':status,
            'discovered_by':row.get('discovered_by') or row.get('source_discovery'),
            'checked_at':_now()}


def yandex_market_discovery(model: str, *, fetcher=_fetch) -> dict[str,Any]:
    url=MARKET+urllib.parse.quote(model)
    try:
        body,final,status=fetcher(url)
        exact=normalize_model(model) in normalize_model(body)
        captcha='showcaptcha' in final or 'smart-captcha' in body.lower()
        return {'source':'Яндекс Маркет','status':'found' if exact and not captcha else 'blocked' if captcha else 'not_found',
                'url':final,'exact_model_found':exact,'http_status':status,
                'note':'Найдена агрегированная страница модели; она используется для discovery, а не как цена отдельного продавца.' if exact else None}
    except Exception as exc:
        return {'source':'Яндекс Маркет','status':'error','error':f'{type(exc).__name__}: {exc}'}


def search_exact_model_prices(model: str, *, seeds_path: Path=DEFAULT_SEEDS, output_path: Path=OUTPUT,
                              fetcher=_fetch, pause: float=.25, automatic_discovery: bool=True,
                              include_saved_seeds: bool=True) -> dict[str,Any]:
    checked=_now(); offers=[]; errors=[]
    discovery=discover_candidate_urls(model,fetcher=fetcher) if automatic_discovery else {'candidate_urls':[],'candidate_count':0,'source_reports':[]}
    rows=list(discovery['candidate_urls'])
    if include_saved_seeds:rows.extend(_seed_rows(seeds_path,model))
    unique_rows={normalize_product_url(r.get('url') or r.get('product_url')):r for r in rows if r.get('url') or r.get('product_url')}
    rows=[]
    for url,row in unique_rows.items():rows.append({**row,'product_url':url})
    for row in rows:
        try:
            offer=inspect_product_url(row,model,fetcher=fetcher)
            if offer: offers.append(offer)
            else: errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':'Страница не подтверждает точную модель'})
        except urllib.error.HTTPError as exc:
            errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':f'HTTP {exc.code}'})
        except Exception as exc:
            errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':f'{type(exc).__name__}: {exc}'})
        if pause: time.sleep(pause)
    unique={x['url']:x for x in offers}; offers=list(unique.values())
    offers.sort(key=lambda x:(x['price'] is None,x['price'] or float('inf'),x['seller'] or ''))
    priced=[x for x in offers if x['price'] is not None]
    result={'target_model':model,'checked_at':checked,'candidate_discovery':discovery,
            'discovery_sources':discovery.get('source_reports',[]),
            'offers':offers,'offers_found':len(offers),'priced_offers_found':len(priced),
            'minimum_price':priced[0]['price'] if priced else None,'minimum_price_seller':priced[0]['seller'] if priced else None,
            'minimum_price_url':priced[0]['url'] if priced else None,'source_errors':errors}
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
