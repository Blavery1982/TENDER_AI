"""Free candidate-URL discovery for an exact model; snippets are never price evidence."""
from __future__ import annotations
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Callable

from model_search.product_evidence import (extract_skus, same_exact_model,
                                           manufacturer_model_in_text)

SEARCH_SOURCES=(
 ('Яндекс Маркет','https://market.yandex.ru/search?text={q}','market.yandex.ru'),
 ('XCOM','https://www.xcom-shop.ru/search/?q={q}','xcom-shop.ru'),
 ('KNS','https://www.kns.ru/search/?q={q}','kns.ru'),
 ('Ситилинк','https://www.citilink.ru/search/?text={q}','citilink.ru'),
 ('ВсеИнструменты','https://www.vseinstrumenti.ru/search/?what={q}','vseinstrumenti.ru'),
 ('ОНЛАЙН ТРЕЙД','https://www.onlinetrade.ru/sitesearch.html?query={q}','onlinetrade.ru'),
 ('DNS','https://www.dns-shop.ru/search/?q={q}','dns-shop.ru'),
 ('М.Видео','https://www.mvideo.ru/product-list-page?q={q}','mvideo.ru'),
 ('Royal-Rus','https://royal-rus.ru/?s={q}','royal-rus.ru'),
 ('ClimaArt','https://climaart.ru/index.php?route=product/search&search={q}','climaart.ru'),
 ('SDK-Climat','https://sdk-climat.ru/search/?q={q}','sdk-climat.ru'),
)

PUBLIC_SEARCH_SOURCES=(
 ('Bing RSS','https://www.bing.com/search?format=rss&setlang=ru-RU&q=%22{q}%22'),
)


class Links(HTMLParser):
 def __init__(self):super().__init__();self.rows=[];self.current=None
 def handle_starttag(self,tag,attrs):
  if tag=='a':self.current=[dict(attrs).get('href',''),'']
 def handle_data(self,data):
  if self.current:self.current[1]+=data
 def handle_endtag(self,tag):
  if tag=='a' and self.current:self.rows.append(tuple(self.current));self.current=None


def target_sku(model: str) -> str:
 values=extract_skus(model)
 if len(values)==1:return values[0]
 value=' '.join(str(model).split()).strip()
 if len(value)<4:raise ValueError('Не удалось однозначно выделить exact SKU/модель')
 return value


def _has_exact_sku(text: str, sku: str) -> bool:
 return manufacturer_model_in_text(urllib.parse.unquote(text),sku)


def extract_candidate_links(body: str, base_url: str, allowed_domain: str, sku: str) -> list[str]:
 parser=Links();parser.feed(body);result=[]
 for href,label in parser.rows:
  href=html.unescape(href).replace('\\/','/').replace('\\u002F','/')
  if not _has_exact_sku(href+' '+label,sku):continue
  url=urllib.parse.urljoin(base_url,href);host=(urllib.parse.urlsplit(url).hostname or '').removeprefix('www.')
  if host!=allowed_domain and not host.endswith('.'+allowed_domain):continue
  if any(x in urllib.parse.urlsplit(url).path.lower() for x in ('/search','/showcaptcha','/auth')):continue
  parts=urllib.parse.urlsplit(url)
  # Search/session/ad parameters are not part of the stable product identity.
  query=urllib.parse.urlencode([(k,v) for k,v in urllib.parse.parse_qsl(parts.query)
      if k.casefold() not in {'search','text','q','cpc','cc','show-uid','showuid','from-show-uid','do-waremd5','from','yclid'}])
  url=urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,query,''))
  if url not in result:result.append(url)
 return result


def discover_candidate_urls(model: str, *, fetcher: Callable, sources=SEARCH_SOURCES,
                            per_source_limit: int=8,
                            public_search_sources=PUBLIC_SEARCH_SOURCES) -> dict[str,Any]:
 sku=target_sku(model);candidates=[];reports=[]
 for name,template in public_search_sources:
  search_url=template.format(q=urllib.parse.quote(model))
  try:
   body,final,status=fetcher(search_url);low=body.lower()
   captcha=('showcaptcha' in final or 'smart-captcha' in low or 'captcha__' in low)
   urls=[]
   if not captcha:
    root=ET.fromstring(body)
    for item in root.findall('.//item'):
     url=(item.findtext('link') or '').strip()
     title=item.findtext('title') or ''
     description=item.findtext('description') or ''
     host=(urllib.parse.urlsplit(url).hostname or '').removeprefix('www.')
     if (url.startswith(('http://','https://')) and host and 'bing.com' not in host
             and _has_exact_sku(f'{url} {title} {description}',sku)):
      clean=urllib.parse.urlunsplit((*urllib.parse.urlsplit(url)[:3],
                                    urllib.parse.urlsplit(url).query,''))
      if clean not in urls:urls.append(clean)
     if len(urls)>=per_source_limit:break
   for url in urls:
    if url not in [x['url'] for x in candidates]:
     candidates.append({'url':url,'discovered_by':name,
                        'supplier_name':(urllib.parse.urlsplit(url).hostname or '').removeprefix('www.')})
   reports.append({'source':name,'status':'captcha' if captcha else 'ok',
                   'http_status':status,'candidate_urls':len(urls)})
  except Exception as exc:
   reports.append({'source':name,'status':'error',
                   'error':f'{type(exc).__name__}: {exc}','candidate_urls':0})
 for name,template,domain in sources:
  search_url=template.format(q=urllib.parse.quote(model))
  try:
   body,final,status=fetcher(search_url);low=body.lower();captcha=('showcaptcha' in final or 'smart-captcha' in low or 'captcha__' in low)
   urls=[] if captcha else extract_candidate_links(body,final,domain,sku)[:per_source_limit]
   # Yandex Market may normalize an exact query into a stable model category.
   if name=='Яндекс Маркет' and not captcha and _has_exact_sku(final,sku) and re.search(r'/card/|/product--|/category/',urllib.parse.urlsplit(final).path):urls.insert(0,final)
   for url in urls:
    if url not in [x['url'] for x in candidates]:candidates.append({'url':url,'discovered_by':name,'supplier_name':name})
   reports.append({'source':name,'status':'captcha' if captcha else 'ok','http_status':status,'candidate_urls':len(urls)})
  except Exception as exc:reports.append({'source':name,'status':'error','error':f'{type(exc).__name__}: {exc}','candidate_urls':0})
 return {'model':model,'sku':sku,'candidate_urls':candidates,'candidate_count':len(candidates),'source_reports':reports}
