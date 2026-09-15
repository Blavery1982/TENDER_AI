"""SKU extraction and evidence ingestion for already discovered product URLs."""
from __future__ import annotations
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

TOKEN=re.compile(r'(?<![\w/.-])(?:[A-ZА-Я]{2,8}[ \t]+\d{2,8}(?:[A-Za-z]{1,5})?|[A-Za-zА-Яа-я0-9]+(?:[-/.][A-Za-zА-Яа-я0-9]+)*)(?![\w/-])')
EXCLUDED=re.compile(r'^(?:IP\d+|(?:USB|HDMI|DDR|RJ|RS|ГОСТ|ISO|DIN|ОКПД2|OKPD2|ИНН|INN|КТРУ)\s*[-\d.]+|\d+(?:[.,]\d+)?(?:W|KW|V|HZ|GHZ|MHZ|KG|MG|G|MM|CM|GB|TB|КВТ|ВТ|КГ|ММ|ГЦ)|\d+[KК])$',re.I)
BRANDS=('Royal Clima','Samsung','LG','HP','Canon','Brother','Epson','Kern','Ohaus','A&D','Ballu','Hisense','Haier','Xerox','Pantum','Electrolux','Energolux','GoldStar')
SOURCE_ORDER={'OFFICIAL_MANUFACTURER':0,'OFFICIAL_MANUAL':1,'OFFICIAL_DATASHEET':1,'OFFICIAL_DISTRIBUTOR':2,'PROFESSIONAL_STORE':3,'OTHER':4,'official_manual':1,'official_datasheet':1,'manufacturer':0,'official_document':1,'official_distributor':2,'professional_store':3,'other':4}


def exact_model_key(value):
    """Manufacturer-model identity key; preserve meaningful suffixes/slashes."""
    return re.sub(r'[\s\-–—]+','',str(value)).upper()


def same_exact_model(left,right): return exact_model_key(left)==exact_model_key(right)


def manufacturer_model_in_text(text, model):
    """Find an exact manufacturer model with formatting-only separator changes.

    Store-specific SKU/product IDs are irrelevant.  Every alphanumeric token
    and meaningful slash in the manufacturer model remains mandatory.
    """
    source=str(text or '')
    target=str(model or '').strip()
    pieces=[re.escape(piece) for piece in re.split(r'[\s\-–—]+',target) if piece]
    if not pieces:return False
    pattern=r'(?<![A-ZА-ЯЁ0-9/])'+r'[\s\-–—]*'.join(pieces)+r'(?![A-ZА-ЯЁ0-9/]|[.\-–—][A-ZА-ЯЁ0-9])'
    return bool(re.search(pattern,source.upper(),re.I))


def normalized_identifier_phrase(value):
    """Normalize separators while preserving every significant model token."""
    return ' '.join(re.findall(r'[0-9A-Za-zА-Яа-яЁё]+', str(value).casefold()))


def exact_identifier_in_text(text, identifier):
    """Strict contiguous identity match for named/cyrillic models.

    Classic SKU matching continues to use ``extract_skus`` in ``page_matches``;
    this branch only makes an exact multi-token commercial name searchable.
    """
    target=normalized_identifier_phrase(identifier)
    source=normalized_identifier_phrase(text)
    if not target or len(target)<4:return False
    return manufacturer_model_in_text(text, identifier)


def valid_sku(value):
    """A model-shaped token, not an arbitrary letter/number combination."""
    if re.search(r'[а-яА-Я]', value): return False
    if any(re.fullmatch(re.escape(b)+r'\d+', value, re.I) for b in BRANDS): return False
    if re.search(r'\d+(?:[.,]\d+)?[xх×]\d', value, re.I): return False
    if '.' in value: return False
    if re.fullmatch(r'\d+(?:mm|cm|kg|kw|hz|w|v|gb|tb|rub)', value, re.I): return False
    # Составные обозначения мощности в заголовках товара не являются второй
    # моделью, например «PR1500ELCD 1500VA/1350W».
    if re.fullmatch(r'\d+(?:va|w|kw)(?:/\d+(?:va|w|kw))+', value, re.I): return False
    # Lowercase navigation slugs lack a mixed alphanumeric model segment.
    if re.fullmatch(r'[a-z]+-\d+(?:-[a-z0-9]+)*', value): return False
    return bool(re.fullmatch(r'(?:[A-Za-z]{1,8}(?:-[A-Za-z]{1,8})?[- ]?)?\d{2,}[A-Za-z][A-Za-z0-9/-]*|[A-Za-z]{2,8}[- ]\d{2,}(?:[A-Za-z0-9/-]*)', value))


def extract_skus(text):
    text=unquote(str(text))
    text=re.sub(r'(?i)\b(?:номер закупки|артикул закупки|код позиции|внутренний код|ОКПД2|OKPD2|ИНН|INN|КТРУ|ГОСТ|ISO|DIN)\s*[:№-]?\s*\S+',' ',text)
    text=re.sub(r'(?i)\.(?:html?|pdf|aspx?)\b',' ',text)
    result=[]
    for m in TOKEN.finditer(text):
        value=m.group().strip('.')
        prefix,_,suffix=value.partition(' ')
        if prefix.upper() in ('HP','LG') and re.search(r'[A-Za-z]',suffix): value=suffix
        if not valid_sku(value): continue
        if len(value)<4 or not re.search(r'[A-Za-zА-Яа-я]',value) or not re.search(r'\d',value): continue
        if EXCLUDED.fullmatch(value) or re.fullmatch(r'\d+[xх×]\d+|802\.\d+[a-z]*',value,re.I) or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',value): continue
        if exact_model_key(value) not in {exact_model_key(x) for x in result}: result.append(value)
    return result


def source_priority(page):
    # Labels only count when supplied by a verified source policy, not page prose.
    return SOURCE_ORDER.get(page.get('source_type'),4) if page.get('source_verified') else 4


def candidates_from_results(results):
    candidates={}
    for row in results:
        url=row.get('url') or '';title=row.get('title') or ''
        # URL paths are discovery hints; query strings can contain unrelated IDs.
        path=unquote(urlsplit(url).path).strip('/')
        pieces=path.split('/')
        url_hint='/'.join(pieces[-2:]) if pieces[-1].upper() in ('IN','OUT') else pieces[-1]
        chunks=[title,row.get('page_title',''),row.get('snippet',''),url_hint]
        # Body-only candidates need explicit model/brand context on their own line.
        for line in row.get('page_text','').splitlines():
            if re.search(r'модель|артикул|серия|\bmodel\b|'+ '|'.join(re.escape(b) for b in BRANDS),line,re.I): chunks.append(line)
        for chunk in chunks:
            for sku in extract_skus(chunk):
                key=exact_model_key(sku)
                brand=next((b for b in BRANDS if re.search(r'(?<!\w)'+re.escape(b)+r'(?!\w)',title+' '+str(chunk),re.I)),None)
                c=candidates.setdefault(key,{'brand':brand,'model':sku,'sku':sku,'exact_model':sku,'model_name':sku,
                    'source_url':url,'source_title':title,'candidate_source':'live_search','sources':[]})
                if not c['brand'] and brand: c['brand']=brand
                if url and url not in c['sources']: c['sources'].append(url)
    return list(candidates.values())


class ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.title=[];self.heading=[];self.tag='';self.skip=0;self.ld=False;self.json_text=[];self.products=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style'):
            self.skip+=1;self.ld=tag=='script' and attrs.get('type','').lower()=='application/ld+json';self.json_text=[]
        if tag in ('title','h1'): self.tag=tag
        if not self.skip and tag in ('tr','p','li','div','h1','h2','br','dt'): self.parts.append('\n')
        if not self.skip and tag in ('td','th','dd'): self.parts.append(' | ')
    def handle_endtag(self,tag):
        if tag=='script' and self.ld:
            try: self.products.append(json.loads(''.join(self.json_text)))
            except ValueError: pass
            self.ld=False
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        if tag==self.tag: self.tag=''
        if not self.skip and tag in ('tr','p','li','div','h1','h2','dd'): self.parts.append('\n')
    def handle_data(self,data):
        if self.ld: self.json_text.append(data)
        if self.skip:return
        self.parts.append(data)
        if self.tag=='title':self.title.append(data)
        if self.tag=='h1':self.heading.append(data)


class ProductScope(HTMLParser):
    """Keep DOM boundaries for table facts and exclude secondary product widgets."""
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    SECONDARY=re.compile(r'(?:^|[ _-])(?:related|recommendations?|recommended|upsells?|cross-sells?|carousel|breadcrumb|recently|viewed|similar)(?:$|[ _-])',re.I)
    def __init__(self):
        super().__init__();self.stack=[];self.primary=[];self.secondary=[];self.started=False
        self.cells=None;self.cell=None;self.canonical='';self.row_depth=None;self.cell_depth=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='link' and attrs.get('rel')=='canonical':self.canonical=attrs.get('href','')
        blocked=(self.stack[-1][1] if self.stack else False) or tag in ('nav','footer','aside','script','style','head') or bool(self.SECONDARY.search(attrs.get('class','')+' '+attrs.get('id','')))
        if tag=='h1' and not blocked:self.started=True
        if tag not in self.VOID:self.stack.append((tag,blocked))
        if not self.started or blocked:return
        if tag in ('div','p','li','h1','h2','h3','br','tr','dt'):self.primary.append('\n')
        classes=attrs.get('class','').split()
        if tag=='tr' or 'product-data__item' in classes:
            self.cells=[];self.row_depth=len(self.stack)
        if (tag in ('th','td') or 'product-data__item-div' in classes) and self.cells is not None:
            self.cell=[];self.cell_depth=len(self.stack)
    def handle_endtag(self,tag):
        blocked=self.stack[-1][1] if self.stack else False
        if self.started and not blocked:
            if self.cell is not None and len(self.stack)==self.cell_depth:
                self.cells.append(' '.join(''.join(self.cell).split()));self.cell=None
            if self.cells is not None and len(self.stack)==self.row_depth:
                if len(self.cells) in (2,3):self.primary.append('\n'+' | '.join(self.cells)+'\n')
                self.cells=None
            if tag in ('div','p','li','h1','h2','h3','dt','dd'):self.primary.append('\n')
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,data):
        if not self.started or (self.stack and self.stack[-1][1]):self.secondary.append(data);return
        if self.cell is not None:self.cell.append(data)
        else:self.primary.append(data)


class CurrentProductPrice(HTMLParser):
    """Текущая цена внутри основного товара; старые цены и рекомендации исключены."""
    DETAIL = re.compile(r'(?:^|[ _-])(?:summary|entry-summary|catalog-detail|product-detail|product-item-detail)(?:$|[ _-])', re.I)
    CURRENT = re.compile(r'(?:^|[ _-])(?:price|price__new(?:-val)?|product-item-detail-price-current|woocommerce-Price-amount)(?:$|[ _-])', re.I)
    OLD = re.compile(r'price__old|old[-_ ]price|price[-_ ]old|calc-total|wholesale-price|installment|рассроч', re.I)

    def __init__(self):
        super().__init__()
        self.stack, self.blocks, self.meta, self.currencies = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        marker = attrs.get('class','') + ' ' + attrs.get('id','')
        parent = self.stack[-1] if self.stack else ('',False,False,None)
        blocked = (parent[1] or tag in {'nav','footer','aside','head','script','style','del','s'}
                   or bool(ProductScope.SECONDARY.search(marker)) or bool(self.OLD.search(marker))
                   or attrs.get('aria-hidden')=='true' or 'display:none' in attrs.get('style','').replace(' ',''))
        detail = parent[2] or bool(self.DETAIL.search(marker)) or attrs.get('itemtype','').rstrip('/').endswith('/Product')
        block = parent[3]
        if detail and not blocked and self.CURRENT.search(marker) and block is None:
            block = []; self.blocks.append(block)
        prop = attrs.get('itemprop','')
        if detail and not blocked and prop=='price' and attrs.get('content'):
            self.meta.append(attrs['content'])
        if detail and not blocked and prop=='priceCurrency' and attrs.get('content'):
            self.currencies.append(attrs['content'].upper())
        if tag not in ProductScope.VOID:
            self.stack.append((tag,blocked,detail,block))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:]; break

    def handle_data(self, data):
        if self.stack and not self.stack[-1][1] and self.stack[-1][3] is not None:
            self.stack[-1][3].append(data)

    def offer(self):
        values = []
        if self.currencies and set(self.currencies)=={'RUB'}:
            values.extend((raw,'microdata') for raw in self.meta)
        for block in self.blocks:
            for raw in re.findall(r'(\d[\d \u00a0]*(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|RUB)', ''.join(block), re.I):
                values.append((raw,'html'))
        prices = {}
        for raw,method in values:
            try:
                price = float(re.sub(r'[\s\u00a0]','',raw).replace(',','.'))
            except (ValueError,TypeError):
                continue
            if math.isfinite(price) and price>0:
                prices.setdefault(price,method)
        if len(prices)!=1:
            return None
        price,method = next(iter(prices.items()))
        return {'price':price,'extraction_method':method}


def html_page(raw,url):
    p=ProductHTML();p.feed(raw)
    scope=ProductScope();scope.feed(raw)
    price=CurrentProductPrice();price.feed(raw)
    extra={'canonical_url':scope.canonical, 'current_product_price':price.offer()}
    if scope.started:
        extra.update(primary_product_content=''.join(scope.primary),secondary_product_content=''.join(scope.secondary))
    return {**extra,'url':url,'title':''.join(p.title),'heading':''.join(p.heading),
            'text':'\n'.join(re.sub(r'[ \t]+',' ',line).strip(' |') for line in ''.join(p.parts).splitlines() if line.strip(' |')),
            'structured_products':p.products,'content_kind':'product_page'}


def document_pages(path, url, *, extractor=None, source_type='other', source_verified=False):
    """Bridge an already downloaded PDF/manual into the existing extraction pipeline."""
    if extractor is None:
        from documents.procurement_audit import extract_document_with_evidence
        extractor=extract_document_with_evidence
    extracted=extractor(Path(path))
    pages=extracted.get('pages') or [{'text':extracted.get('text',''),'page_number':None}]
    return [{'url':url,'text':p.get('text',''),'page_number':p.get('page_number'),
             'document':Path(path).name,'content_kind':'document','source_type':source_type,'source_verified':source_verified,
             'extraction_uncertain':p.get('read_method')=='failed' or (p.get('read_method')=='ocr' and
                 (p.get('average_confidence') is None or p['average_confidence']<70))}
            for p in pages]


def _products(value):
    if isinstance(value,list):
        for x in value: yield from _products(x)
    elif isinstance(value,dict):
        kind=value.get('@type')
        if kind=='Product' or isinstance(kind,list) and 'Product' in kind: yield value
        for key in ('@graph','mainEntity'): yield from _products(value.get(key))


def primary_content(page):
    """Scope legacy extracted text to the main heading and stop at secondary blocks."""
    text=page.get('primary_product_content',page.get('text',''))
    if page.get('content_kind')=='document':return text
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    heading=' '.join(page.get('heading','').split())
    positions=[i for i,x in enumerate(lines) if ' '.join(x.split())==heading] if heading else []
    # Breadcrumb and product H1 can repeat; start at the actual product occurrence.
    start=positions[-1] if positions and 'primary_product_content' not in page else 0
    out=[]
    for line in lines[start:]:
        if re.match(r'^(?:похожие товары|рекомендуем|рекомендации|с этим товаром|вы смотрели|недавно просмотренные|related products|you may also like|контакты|полезные ссылки|запросы)$',line,re.I):break
        out.append(line)
    return '\n'.join(out)


def page_matches(page,sku):
    if page.get('content_kind')=='search_snippet' or page.get('extraction_uncertain'): return False
    target=exact_model_key(sku)
    classic=len(extract_skus(sku))==1 and same_exact_model(extract_skus(sku)[0], sku)
    if not classic:
        if page.get("heading"):
            return exact_identifier_in_text(page["heading"], sku)
        for field in ('heading','title'):
            if exact_identifier_in_text(page.get(field,''),sku):return True
        for product in _products(page.get('structured_products',[])):
            if any(exact_identifier_in_text(product.get(field,''),sku)
                   for field in ('name','model','mpn','sku')):return True
        for field in ('canonical_url','url'):
            if exact_identifier_in_text(unquote(urlsplit(page.get(field,'')).path),sku):return True
        return exact_identifier_in_text(primary_content(page),sku)
    # The primary identity takes precedence over recommendations elsewhere.
    for field in ('heading','title'):
        if manufacturer_model_in_text(page.get(field,''),sku):
            found={exact_model_key(x) for x in extract_skus(page.get(field,''))}
            unrelated={x for x in found if x!=target and not target.endswith(x)}
            return not unrelated
        found={exact_model_key(x) for x in extract_skus(page.get(field,''))}
        if found:return found=={target}
    for field in ('canonical_url','url'):
        path=unquote(urlsplit(page.get(field,'')).path).strip('/')
        parts=path.split('/')
        hint='/'.join(parts[-2:]) if parts[-1].upper() in ('IN','OUT') else parts[-1]
        found={exact_model_key(x) for x in extract_skus(hint)}
        if found:return found=={target}
    identities=[]
    for product in _products(page.get('structured_products',[])):
        # model/mpn are manufacturer identity fields. `sku` is frequently only
        # an internal identifier of this particular seller and cannot reject a
        # product whose manufacturer model is confirmed elsewhere.
        identifier=product.get('model') or product.get('mpn')
        if identifier:identities.append(str(identifier))
        if manufacturer_model_in_text(product.get('name',''),sku):return True
    if identities:return any(same_exact_model(x,sku) for x in identities)
    # Unlabelled/multi-model documents remain ambiguous.
    return {exact_model_key(x) for x in extract_skus(primary_content(page))}=={target}


def public_offer(page,sku):
    if not page_matches(page,sku): return None
    offers=[]
    for product in _products(page.get('structured_products',[])):
        identity=product.get('sku') or product.get('mpn') or ''
        if not (same_exact_model(identity,sku) or manufacturer_model_in_text(product.get('name',''),sku)): continue
        raw=product.get('offers') or []
        for offer in raw if isinstance(raw,list) else [raw]:
            if not isinstance(offer,dict): continue
            if offer.get('priceCurrency')!='RUB':continue
            try: price=float(str(offer['price']).replace(',','.'))
            except (KeyError,ValueError,TypeError):continue
            if math.isfinite(price) and price>0: offers.append({'price':price,'url':page['url'],'sku':sku,
                'availability_status':offer.get('availability'),
                'available':str(offer.get('availability','')).endswith('/InStock')})
    if not offers:
        current = page.get('current_product_price')
        if current:
            offers = [{**current, 'url':page['url'], 'sku':sku,
                       'available':bool(re.search(r'(?i)в наличии',primary_content(page)))
                       and not bool(re.search(r'(?i)нет в наличии|товар закончился|снят.{0,20}(?:продажи|производства)',primary_content(page)))}]
    if not offers:
        # Single explicit price row only. Ignore recommendations, installments, old prices.
        found=re.findall(r'(?im)^\s*цена\s*[:|]\s*(\d[\d \u00a0]*(?:[,.]\d{1,2})?)\s*(?:₽|руб\.?|RUB)\s*$',primary_content(page))
        if len(found)==1:
            price=float(re.sub(r'[\s\u00a0]','',found[0]).replace(',','.'))
            if price>0: offers=[{'price':price,'url':page['url'],'sku':sku,
                'available':bool(re.search(r'(?im)^\s*(?:наличие\s*[:|]\s*)?в наличии\s*$',page.get('text','')))}]
    for offer in offers:
        host=urlsplit(page['url']).hostname or ''
        offer['available']=offer['available'] and (host.endswith('.ru') or host.endswith('.рф'))
    return min(offers,key=lambda x:x['price']) if offers else None
