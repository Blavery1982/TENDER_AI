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
    text=page.get('primary_product_content') or page.get('text','')
    if re.search(r'(?i)архивн\w*\s+товар|(?:товар|карточка).{0,30}в\s+архиве|(?:^|\n)\s*архив\s*(?:$|\n)',text): return 'Архив'
    if re.search(r'(?i)снят\w*\s+(?:с\s+продажи|с\s+производства)|продажи прекращены|не прода[её]тся',text): return 'Снят с продажи'
    if re.search(r'(?i)нет в наличии|товар закончился|снят с продажи',text): return 'Нет в наличии'
    structured = str((offer or {}).get('availability_status') or '').rstrip('/').rsplit('/', 1)[-1].casefold()
    if structured == 'discontinued': return 'Снят с продажи'
    if structured in {'outofstock','soldout'}: return 'Нет в наличии'
    if re.search(r'(?i)под заказ|доступен к заказу|предзаказ',text) or structured in {'preorder','backorder'}: return 'Под заказ'
    if structured == 'instock': return 'В наличии'
    if offer and offer.get('available'): return 'В наличии'
    if re.search(r'(?i)(?:^|\n)\s*в наличии\s*(?:\n|$)',text): return 'В наличии'
    return None


def _availability_raw(page: dict, offer: dict | None) -> str | None:
    """Сохранить исходное обозначение наличия, не подменяя его нормализацией."""
    text = page.get('primary_product_content') or page.get('text', '')
    patterns = (
        r'(?i)архив\w*\s+товар|(?:товар|карточка).{0,30}в\s+архиве|(?:^|\n)\s*архив\s*(?:$|\n)',
        r'(?i)снят\w*\s+(?:с\s+продажи|с\s+производства)|продажи прекращены|не прода[её]тся',
        r'(?i)(?:нет в наличии|товар закончился)',
        r'(?i)(?:под заказ|доступен к заказу|предзаказ)',
        r'(?i)(?:наличие\s*[:|]\s*)?в наличии',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return ' '.join(match.group(0).split()).strip(' |:') or None
    structured = (offer or {}).get('availability_status')
    return str(structured) if structured is not None else None


def normalize_availability(value: str | None) -> str:
    """Нормализовать наличие для будущей логики сроков без отбраковки order."""
    text = ' '.join(str(value or '').casefold().replace('_', ' ').split())
    if not text:
        return 'unknown'
    if any(marker in text for marker in ('архив', 'снят с продажи', 'снят с производства',
                                         'продажи прекращены', 'нет в наличии', 'товар закончился',
                                         'outofstock', 'soldout', 'discontinued')):
        return 'out_of_stock'
    if any(marker in text for marker in ('под заказ', 'доступен к заказу', 'предзаказ',
                                         'backorder', 'preorder')):
        return 'order'
    if any(marker in text for marker in ('в наличии', 'instock', 'in stock')):
        return 'in_stock'
    return 'unknown'


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
            if price>0: found.append({'price':price,'availability_status':offer.get('availability'),
                                     'available':'instock' in str(offer.get('availability','')).lower()})
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


def _json_ld_has_price(page: dict, sku: str, expected: float | None) -> bool:
    """Проверить, что найденная цена принадлежит Product/Offer в JSON-LD."""
    if expected is None:
        return False
    for product in _walk(page.get('structured_products') or []):
        if product.get('@type') != 'Product' or not page_matches(
                {**page, 'heading': product.get('name', ''), 'title': ''}, sku):
            continue
        raw = product.get('offers') or []
        for item in raw if isinstance(raw, list) else [raw]:
            if not isinstance(item, dict) or str(item.get('priceCurrency', '')).upper() != 'RUB':
                continue
            try:
                price = float(str(item['price']).replace(' ', '').replace(',', '.'))
            except (KeyError, TypeError, ValueError):
                continue
            if price == expected:
                return True
    return False


def _extract_offer(page: dict, sku: str) -> tuple[dict | None, str | None]:
    """Вернуть предложение и фактический парсер цены."""
    offer = public_offer(page, sku)
    if offer:
        return offer, 'json_ld' if _json_ld_has_price(page, sku, offer.get('price')) else offer.get('extraction_method','html')
    offer = _structured_offer(page, sku)
    if offer:
        return offer, 'json_ld'
    offer = _visible_offer(page)
    return (offer, 'html') if offer else (None, None)


def _direct_product_url(url: str) -> bool:
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname: return False
    if any(parsed.hostname==d or parsed.hostname.endswith('.'+d)
           for d in ('yandex.ru','ya.ru','google.ru','google.com','bing.com','bing.ru','yahoo.com','duckduckgo.com')) and parsed.hostname!='market.yandex.ru': return False
    path=parsed.path.casefold().rstrip('/')
    if not path or path in {'','/'}:return False
    return path not in {'/catalog','/products','/shop'} and not any(marker in path for marker in ('/search','/category','/categories'))


def _market_seller(body: str) -> str | None:
    """Return the merchant tied to a Market product card, never the marketplace label."""
    values=[]
    for raw in re.findall(r'"supplierName"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',body):
        try:value=json.loads('"'+raw+'"')
        except (json.JSONDecodeError,TypeError):continue
        if value and value not in values:values.append(value)
    return values[0] if len(values)==1 else None


def inspect_product_url(row: dict[str,Any], model: str, *, fetcher=_fetch,
                        transport: str = 'http', requirements=(), product_name=None) -> dict[str,Any] | None:
    # Поисковые/каталожные ссылки отбрасываются до сетевого запроса.
    if not _direct_product_url(row['product_url']):
        return None
    body,final,status=fetcher(row['product_url']); page=html_page(body,final)
    if status < 200 or status >= 400: return None
    sku=" ".join(str(model).split())
    if not _direct_product_url(final): return None
    if not page_matches(page,sku):
        # Название товара может стоять после модели или иметь другой порядок слов.
        # Обозначение берётся из уже собранного заказчиком имени, не угадывается.
        noun=" ".join(str(product_name or '').split())
        prefix=noun+' '
        designation=sku[len(prefix):] if noun and sku.casefold().startswith(prefix.casefold()) else None
        heading=primary_content(page)
        noun_tokens=re.findall(r'[A-Za-zА-Яа-яЁё0-9]+',noun)
        type_matches=bool(noun_tokens) and all(re.search(r'(?i)(?<!\w)'+re.escape(t)+r'(?!\w)',heading) for t in noun_tokens)
        if not designation or not type_matches or not page_matches(page,designation):return None
        sku=designation
    identity_text = ' '.join(str(page.get(key) or '') for key in ('heading', 'title'))
    if re.search(r'(?i)картридж|тонер|запчаст|аксессуар|комплектующ|услуг|аренд|ремонт|\bб/у\b|бывш(?:ий|ая|ее)\s+в\s+употреблении', identity_text):
        return None
    from model_search.compliance import assess_model
    assessment = assess_model(requirements, [page], sku)
    if assessment['status'] == 'non_compliant':
        return None
    offer, parser_method = _extract_offer(page, sku)
    availability = _availability(page, offer)
    availability_raw = _availability_raw(page, offer)
    extraction_method = transport if transport == 'playwright' and offer and offer.get('price') is not None else parser_method
    domain = urllib.parse.urlsplit(final).hostname
    confirmed_price = offer.get('price') if offer else None
    market_seller=_market_seller(body) if (urllib.parse.urlsplit(final).hostname or '').endswith('market.yandex.ru') else None
    if (urllib.parse.urlsplit(final).hostname or '').endswith('market.yandex.ru') and not market_seller:
        return None
    return {'exact_product_name':page.get('heading') or page.get('title') or row.get('product_name') or model,
            'model':model,'exact_manufacturer_model':sku,'exact_model_match':True,
            'supplier_sku':_supplier_sku(page,sku),
            'price':confirmed_price,'confirmed_price':confirmed_price,
            'currency':'RUB' if offer else None,
            'availability':availability,'availability_raw':availability_raw,
            'availability_normalized':normalize_availability(availability or availability_raw),
            'seller':market_seller or row.get('supplier_name') or domain,
            'seller_identity_confirmed': bool(market_seller),
            'url':normalize_product_url(final),'source':domain,'source_url':final,'domain':domain,
            'extraction_method':extraction_method,'exact_model_confirmed':True,'http_status':status,
            'product_page_available':True, 'price_confirmed_on_product_page':offer is not None,
            'customer_requirements_status':assessment['status'],
            'customer_requirements_check':assessment.get('requirements_check') or [],
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
            if offer:
                offer['price_run_id']=checked
                offers.append(offer)
            else: errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':'Страница не подтверждает точную модель'})
        except urllib.error.HTTPError as exc:
            errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':f'HTTP {exc.code}'})
        except Exception as exc:
            errors.append({'source':row.get('supplier_name'),'url':row['product_url'],'error':f'{type(exc).__name__}: {exc}'})
        if pause: time.sleep(pause)
    unique={x['url']:x for x in offers}; offers=list(unique.values())
    offers.sort(key=lambda x:(x['price'] is None,x['price'] or float('inf'),x['seller'] or ''))
    priced=[x for x in offers if x['price'] is not None]
    result={'target_model':model,'checked_at':checked,'price_run_id':checked,'candidate_discovery':discovery,
            'discovery_sources':discovery.get('source_reports',[]),
            'offers':offers,'offers_found':len(offers),'priced_offers_found':len(priced),
            'minimum_price':priced[0]['price'] if priced else None,'minimum_price_seller':priced[0]['seller'] if priced else None,
            'minimum_price_url':priced[0]['url'] if priced else None,'source_errors':errors}
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
