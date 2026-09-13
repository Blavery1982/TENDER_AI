"""Small semantic query plans shared by product categories; no search calls."""
import re
from model_search.search_mode import determine_model_search_mode


def rows(item):
    return item.get('structured_requirements') or item.get('requirements') or []


def feature(row):
    p=str(row.get('parameter') or row.get('requirement_name') or '')
    v=str(row.get('required_value',row.get('value','')))
    text=f'{p} {v}'.strip()
    # Explicit vocabulary rules, not a purchase/model-specific mapping.
    if re.search('инвертор',p,re.I):
        return (1, 'неинверторный' if re.fullmatch(r'нет|не требуется|неинверторный|on[- /]?off',v.strip(),re.I) else 'инверторный' if v.lower() in ('да','требуется','инверторный') else text)
    if re.search(r'тип|вид|конструкц|технология|установка|монтаж|исполнение',p,re.I):
        rank=0 if re.search(r'тип|вид',p,re.I) else 1
    elif re.search(r'функц|фильтр|интерфейс|двусторон|дуплекс|калибров|защит|подключ|цветн',text,re.I): rank=2
    elif re.search(r'\d',text): rank=3
    else: rank=4
    if re.fullmatch(r'нет|не требуется|отсутствует',v.strip(),re.I):
        return rank, 'без '+re.sub(r'(?i)^наличие\s+','',p).strip()
    if rank in (0,1) and v and not re.fullmatch(r'да|требуется',v,re.I): text=v
    else:
        p=re.sub(r'(?i)^наличие\s+','',p)
        if re.fullmatch(r'да|требуется',v.strip(),re.I): v=''
        text=f'{p} {v}'.strip()
    text=re.sub(r'(?i)автоматическая очистка','самоочистка',text)
    text=re.sub(r'(?i)^дополнительные функции\s*','',text)
    text=text.replace('м^2','м²')
    text=re.sub(r'(?i)фильтров тонкой очистки воздуха','фильтр тонкой очистки',text)
    text=re.sub(r'(?i)антибактериального фильтра','антибактериальный фильтр',text)
    # Keep separate unit/operator fields when upstream normalized them.
    unit=row.get('unit')
    if unit and str(unit).casefold() not in text.casefold(): text+=' '+str(unit)
    op={'minimum':'≥','maximum':'≤','greater_than':'>','less_than':'<','>=':'≥','<=':'≤'}.get(row.get('operator'))
    if op and not re.search(r'≥|≤|>|<|не менее|не более|не ниже|не выше',text,re.I):
        text=re.sub(r'(?=\d)',op+' ',text,count=1)
    return rank,re.sub(r'\s+',' ',text).strip(' .')


def generate_queries(item, limit=3):
    decision=determine_model_search_mode(item)
    original=item.get('customer_required_model') or decision.get('original_model')
    name=str(item.get('item_name') or item.get('name') or 'товар').splitlines()[0][:100]
    if original:
        name=re.sub(re.escape(original),'',name,flags=re.I)
        # Drop any SKU/brand left in a long original-model product name.
        from model_search.product_evidence import extract_skus
        for sku in extract_skus(original): name=re.sub(re.escape(sku),'',name,flags=re.I)
    name=re.sub(r'(?i)или\s+(?:эквивалент|аналог)|\bмодель\s*:?', '',name).strip(' ,:-') or 'товар'
    groups={i:[] for i in range(5)}
    for row in rows(item):
        if re.search(r'(?i)^(модель|марка|бренд|артикул)\b',str(row.get('parameter') or row.get('requirement_name') or '')): continue
        rank,text=feature(row)
        if original and (original.casefold() in text.casefold() or any(s.casefold() in text.casefold() for s in extract_skus(original))): continue
        if text and text not in groups[rank]: groups[rank].append(text)
    structural=groups[0][:2]+groups[1][:2]
    functions=[]
    for f in sorted(groups[2],key=lambda x: ',' in x): functions.extend(x.strip() for x in f.split(',') if x.strip())
    functions=list(dict.fromkeys(functions))
    # Short independent groups, never concatenate every tender requirement.
    primary=' '.join([name,*structural,*groups[3][:1]])
    second=' '.join([name,*functions[:3]]) if functions else ' '.join([name,*groups[3][1:3],*groups[4][:1],'характеристики'])
    reserve=' '.join([name,*structural[:1],*groups[3][1:3],*functions[-3:]])
    justification=item.get('price_justification_model') or item.get('model_from_justification')
    plan=[f'"{original}" характеристики',primary,reserve if reserve!=primary else second] if original else [primary,second,reserve]
    if justification and not original: plan=[f'"{justification}" характеристики',primary,second]
    result=[]
    for q in plan:
        q=re.sub(r'\s+',' ',q).strip()
        if q and q not in result: result.append(q)
    return result[:max(0,min(limit,3))]


def plausible_candidates(results, item):
    """A SKU alone is insufficient to skip the reserve query: require product context."""
    from model_search.product_evidence import candidates_from_results
    name=str(item.get('product_type') or item.get('item_name') or item.get('name') or '')
    words=re.findall(r'[а-яёa-z]{3,}',name.casefold())
    generic={'товар','товары','поставка','бытовой','бытовая','оборудование','модель','или','эквивалент'}
    stems=[w[:6] for w in words if w not in generic]
    if not stems:return []
    relevant=[r for r in results if any(stem in ' '.join(str(r.get(k,'')) for k in ('title','snippet','page_title','page_text')).casefold() for stem in stems)]
    from model_search.product_evidence import extract_skus, exact_model_key
    supported={exact_model_key(sku) for r in relevant
               for sku in extract_skus(' '.join(str(r.get(k,'')) for k in ('title','snippet','page_title','page_text')))}
    return [c for c in candidates_from_results(relevant) if exact_model_key(c['sku']) in supported]
