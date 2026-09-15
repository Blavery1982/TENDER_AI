"""Conservative fact-by-fact compliance. No snippet or word-presence proofs."""
from __future__ import annotations
import re
from model_search.semantic_fields import FIELD_ALIASES, FUNCTIONS, SOURCE_TYPES
from decimal import Decimal, InvalidOperation
from model_search.product_evidence import page_matches, source_priority, primary_content

CORRESPONDS='corresponds'
MISMATCH='does_not_comply'
UNKNOWN='could_not_confirm'
STATUS_RU={'fully_compliant':'Полностью соответствует ТЗ','non_compliant':'Не соответствует ТЗ','not_confirmed':'Соответствие не подтверждено'}
UNITS={'btu/h':('power',Decimal('0.29307107')), 'квт':('power',Decimal(1000)), 'вт':('power',Decimal(1)), 'kw':('power',Decimal(1000)), 'w':('power',Decimal(1)),
       'кг':('mass',Decimal(1000)), 'г':('mass',Decimal(1)), 'мг':('mass',Decimal('.001')),
       'kg':('mass',Decimal(1000)), 'g':('mass',Decimal(1)), 'mg':('mass',Decimal('.001')),
       'мм':('length',Decimal('.001')), 'см':('length',Decimal('.01')), 'м':('length',Decimal(1)),
       'м²':('area',Decimal(1)), 'дб':('sound',Decimal(1)), 'гц':('frequency',Decimal(1)),
       'дюйм':('inches',Decimal(1)), 'стр/мин':('ppm',Decimal(1)), '%':('percent',Decimal(1))}
UNITS.update({'в':('voltage',Decimal(1)), 'v':('voltage',Decimal(1)),
              'ач':('charge_capacity',Decimal(1)), 'ah':('charge_capacity',Decimal(1)),
              'л':('volume',Decimal(1)), 'l':('volume',Decimal(1)),
              'лет':('years',Decimal(1)), 'год':('years',Decimal(1)), 'года':('years',Decimal(1))})
UNITS.update({'г/м²':('areal_density',Decimal(1)), 'гр/м²':('areal_density',Decimal(1)),
              'кг/м²':('areal_density',Decimal(1000)), 'бар':('pressure',Decimal(100000)),
              'мпа':('pressure',Decimal(1000000)), '°с':('temperature_celsius',Decimal(1))})
NUM=r'[-+]?\d+(?:[.,]\d+)?'
UNIT_PATTERN='|'.join(re.escape(k) for k in sorted(UNITS,key=len,reverse=True))
UNIT_RE=re.compile(r'(?<![а-яa-z])('+UNIT_PATTERN+r')(?![а-яa-z²])',re.I)


def norm(value):
    s=str(value).casefold().replace('ё','е').replace('м^2','м²')
    s=re.sub(r'\bм2\b','м²',s)
    return re.sub(r'\s+',' ',s).strip(' .:|')


def parameter_key(value):
    p=norm(value)
    p=re.split(r'≥|≤|>=|<=|>|<',p)[0].strip()
    p=re.sub(r'(?:\s*[,(/]\s*|\s+)('+UNIT_PATTERN+r')\s*[)]?$', '',p)
    from model_search.customer_model import CONTEXT
    p=CONTEXT.get('requirement_name_aliases',{}).get(p,p)
    p=re.sub(r'^наличие\s+','',p)
    if p in FIELD_ALIASES:return FIELD_ALIASES[p]
    if p in ('power consumption','power input','input power'):return 'input_power'
    if p in ('режимы','режимы работы'):return 'functions'
    if 'инвертор' in p or p=='тип компрессора':return 'inverter'
    if p in ('цвет корпуса','цвет внутреннего блока'):return 'цвет'
    if p=='вид товара':return 'product_type'
    # Electrical input power is not delivered cooling/heating capacity.
    if 'потребляем' in p and 'мощност' in p:return 'input_power'
    if 'энергоэффектив' in p:
        return 'energy_efficiency_heating' if ('нагрев' in p or 'обогрев' in p) else 'energy_efficiency_cooling' if 'охлажд' in p else p
    if 'мощност' in p and 'охлажд' in p:return 'cooling_capacity'
    if 'мощност' in p and ('нагрев' in p or 'обогрев' in p):return 'heating_capacity'
    if p=='room_area':return 'room_area'
    if 'антибактериальн' in p and 'фильтр' in p:return 'antibacterial_filter'
    if 'тонкой очистки' in p:return 'fine_filter'
    if p in ('тип внутреннего блока','установка','способ установки','монтаж'):return 'mounting'
    if p in ('вид кондиционера','тип кондиционера'):return 'product_type'
    if p in ('вид блока кондиционера','наружный блок'):return 'outdoor_unit'
    if p in ('дополнительные функции','функции'):return 'functions'
    if p in ('автоматическая двусторонняя печать','двусторонняя печать','дуплекс'):return 'duplex'
    return re.sub(r'^наличие\s+','',p)


SYNONYMS={
    'настенный':('настенная','настенная установка','настенное исполнение','настенный внутренний блок','wall-mounted','wall_mounted'),
    'сплит-система':('сплит система','split system','split_system'),
    'самоочистка':('автоматическая очистка','автоочистка'),
    'автоматический выбор режима':('auto','автоматический режим'),
    'ночной режим':('ночной','sleep'), 'режим турбо':('турбо','turbo'),
    'самодиагностика':('self diagnosis',), 'неинверторный':('on-off','on/off','неинверторная'),
    'лазерный':('лазерная','лазерная печать'), 'цветной':('цветная','цветная печать'),
    'белый':('белая','white'),
}


def enum(value):
    s=norm(value)
    for canonical,variants in SYNONYMS.items():
        if s==canonical or s in variants:return canonical
    return s


def boolean(value, key):
    s=enum(value)
    if s in ('нет','не требуется','отсутствует','не поддерживается','не предусмотрен','не предусмотрена'):return False
    if s in ('да','есть','наличие','поддерживается','присутствует','требуется'):return True
    if key=='inverter':
        if s=='неинверторный':return False
        if s in ('инверторный','инверторная','inverter'):return True
    if key=='outdoor_unit' and s in ('наружный','внешний','наружный блок','внешний блок','outdoor unit'):return True
    return None


def numeric(value, unit=None):
    s=norm(value)
    s=re.sub(r'(?<=\d)[ \u00a0](?=\d{3}(?:\D|$))','',s)
    s=re.sub(r'(?<=\d)\s*[-–—]\s*(?=\d)',' до ',s)
    numbers=re.findall(NUM,s)
    if not numbers:return None
    # Numeric model names, resolutions, tolerances, percentages of another value:
    # ambiguous expressions must not be interpreted as a single scalar.
    if len(numbers)>2 or re.search(r'±|\d\s*[xх×]\s*\d',s):return None
    units=UNIT_RE.findall(s)
    actual_unit=norm(unit) if unit else units[0] if units else None
    if actual_unit and actual_unit not in UNITS:return None
    if len(set(units))>1:return None
    residue=UNIT_RE.sub('',s)
    residue=re.sub(NUM,'',residue)
    residue=re.sub(r'не менее|не более|не ниже|не выше|от|до|[\s≥≤<>=–—\-.,()]','',residue)
    if residue:return None
    try: values=[Decimal(n.replace(',','.')) for n in numbers]
    except InvalidOperation:return None
    if len(values)==2 and not re.search(r'\bот\b|\bдо\b|\d\s*[-–—]\s*\d',s):return None
    if len(values)==2 and values[0]>values[1]:return None
    dimension,factor=UNITS[actual_unit] if actual_unit else (None,Decimal(1))
    return [v*factor for v in values],dimension,actual_unit


def requirement_parts(requirement):
    p=str(requirement.get('parameter') or requirement.get('requirement_name') or '')
    value=str(requirement.get('required_value',requirement.get('value','')))
    op=requirement.get('operator') or requirement.get('comparison_type') or 'equals'
    embedded=re.search(r'(≥|≤|>=|<=|>|<)\s*(.+)$',p)
    if embedded:
        if norm(value) in ('','требуется'):value=embedded.group(2)
        op=embedded.group(1);p=p[:embedded.start()].strip()
    match=re.search(r'не менее|не ниже|не более|не выше|>=|<=|≥|≤|>|<',value)
    if match:op=match.group()
    mapping={'=':'equals','≥':'minimum','>=':'minimum','не менее':'minimum','не ниже':'minimum',
             '≤':'maximum','<=':'maximum','не более':'maximum','не выше':'maximum','>':'greater_than','<':'less_than'}
    op=mapping.get(op,op)
    if re.search(r'\bот\b.+\bдо\b|\d\s*[-–—]\s*\d',value):op='range'
    return p,value,op,requirement.get('unit')


def compare(required, actual, key, operator='equals', required_unit=None, actual_unit=None):
    rn=numeric(required,required_unit);an=numeric(actual,actual_unit)
    if rn is not None:
        if an is None or rn[1]!=an[1]:return UNKNOWN
        r,_,_=rn;a,_,_=an
        # A bound in a source is not an exact measured/model value.
        if re.search(r'≥|≤|>|<|не менее|не более|не ниже|не выше|\bдо\b|\bот\b',norm(actual)) and len(a)==1:
            upper=bool(re.match(r'^(?:до|не более|не выше|<=|≤|<)',norm(actual)))
            lower=bool(re.match(r'^(?:от|не менее|не ниже|>=|≥|>)',norm(actual)))
            if operator=='minimum' and upper and a[0]<r[0]:return MISMATCH
            if operator=='maximum' and lower and a[0]>r[0]:return MISMATCH
            return UNKNOWN
        lo,hi=min(a),max(a)
        if operator=='range' or len(r)==2:
            if len(r)!=2:return UNKNOWN
            return CORRESPONDS if lo>=r[0] and hi<=r[1] else MISMATCH if hi<r[0] or lo>r[1] else UNKNOWN
        target=r[0]
        if operator=='minimum':return CORRESPONDS if lo>=target else MISMATCH if hi<target else UNKNOWN
        if operator=='maximum':return CORRESPONDS if hi<=target else MISMATCH if lo>target else UNKNOWN
        if operator=='greater_than':return CORRESPONDS if lo>target else MISMATCH if hi<=target else UNKNOWN
        if operator=='less_than':return CORRESPONDS if hi<target else MISMATCH if lo>=target else UNKNOWN
        return CORRESPONDS if lo==hi==target else MISMATCH if target<lo or target>hi else UNKNOWN
    # A numeric requirement that was not safely parsed is never text-confirmed.
    if re.search(r'\d',required):
        if re.match(r'^[-+]?\d',norm(required)) or UNIT_RE.search(norm(required)): return UNKNOWN
        return CORRESPONDS if norm(required)==norm(actual) and operator=='equals' else UNKNOWN
    rb=boolean(required,key);ab=boolean(actual,key)
    if rb is not None:return UNKNOWN if ab is None else CORRESPONDS if rb==ab else MISMATCH
    if key.startswith('energy_'):
        ranks={'a+++':6,'a++':5,'a+':4,'a':3,'b':2,'c':1,'d':0}
        def grade(s):
            m=re.fullmatch(r'(?:не ниже\s+)?([a-dа-в])([+]{0,3})(?:\s*\([^)]*\))?',norm(s))
            return ranks.get(m.group(1).translate(str.maketrans({'а':'a','в':'b'}))+m.group(2)) if m else None
        r=grade(required);a=grade(actual)
        if r is None or a is None:return UNKNOWN
        return CORRESPONDS if (a>=r if operator=='minimum' else a==r) else MISMATCH
    if key=='functions':
        requested={enum(x) for x in re.split('[,;]',required) if x.strip()}
        found={enum(x) for x in re.split('[,;]',actual) if x.strip()}
        if requested<=found:return CORRESPONDS
        for function in requested:
            if any(x in found for x in (function+' нет','без '+function,function+' отсутствует')):return MISMATCH
        return UNKNOWN
    r=enum(required);a=enum(actual)
    if r==a:return CORRESPONDS
    if re.search(r'возможно|опционально|по запросу|не подтвержден',a):return UNKNOWN
    # Only known exclusive enum values constitute a proven contradiction.
    exclusive=(
        {'настенный','напольный','настольный','потолочный'},
        {'белый','черный','серый','красный','синий','зеленый'},
        {'лазерный','струйный','матричный','термопечать'},
        {'цветной','монохромный','черно-белый'},
        {'сплит-система','моноблок','мобильный'},
    )
    return MISMATCH if any(r in group and a in group for group in exclusive) else UNKNOWN


def facts(text):
    """Only local key/value rows; never mix numbers across the whole page."""
    text=re.sub(r'(?im)^(дополнительные функции|функции|режимы):[ \t]*\n[ \t]*',r'\1: ',str(text))
    for line in text.splitlines():
        line=line.strip(' |\t')
        if re.fullmatch(r'Режим [«\"]?Турбо[»\"]? и ночной режим работы, а также удобный таймер и LED-дисплей[.]?',line,re.I):
            yield 'turbo_mode','да',None,line
            yield 'sleep_mode','да',None,line
        match=re.match(r'^(.+?)\s*(?::|\||\t|\s[-–—]\s)\s*(.+)$',line)
        if not match:
            match=re.match(r'^([^\d]+?)\s+('+NUM+r'.*)$',line)
        if not match:continue
        p,value=match.groups()
        u=UNIT_RE.search(norm(p));unit=u.group(1) if u else None
        cells=[x.strip() for x in value.split('|')]
        if len(cells)==2:
            if norm(cells[1]) in UNITS: value=cells[0];unit=norm(cells[1])
            elif norm(cells[0]) in UNITS: value=cells[1];unit=norm(cells[0])
        key=parameter_key(p)
        yield key,value.strip(' |'),unit,line
        if key=='functions':
            for token in re.split(r'[,;+]',value):
                token=token.strip()
                function=FIELD_ALIASES.get(norm(token))
                if function in FUNCTIONS:yield function,'да',None,line+' [функция: '+token+']'
                else:
                    neg=re.fullmatch(r'(?:без\s+)?(.+?)(?:\s+(нет|отсутствует|не поддерживается))?',norm(token))
                    if neg and (norm(token).startswith('без ') or neg.group(2)):
                        function=FIELD_ALIASES.get(neg.group(1))
                        if function in FUNCTIONS:yield function,'нет',None,line+' [функция: '+token+']'


def normalized_value(value,unit,key):
    number=numeric(value,unit)
    if number:
        return {'values':[str(x) for x in number[0]],'dimension':number[1],
                'base_unit':'W' if number[1]=='power' else number[1], 'source_unit':number[2],
                'conversion_factor':str(UNITS[number[2]][1]) if number[2] else '1'}
    b=boolean(value,key)
    return b if b is not None else enum(value)


def check_requirement(requirement,pages,sku):
    p,required,operator,unit=requirement_parts(requirement);key=parameter_key(p)
    base={'requirement':p,'required_value':required,'operator':operator,'unit':unit or (numeric(required) or (None,None,None))[2],
          'found_value':None,'source':None,'source_page':None,'evidence':None,'result':UNKNOWN,'confidence':'low'}
    if key=='functions':
        tokens=[t.strip() for t in re.split(r'[,;+]',required) if t.strip()]
        checks=[check_requirement({'parameter':FIELD_ALIASES.get(norm(t),t),'value':'да'},pages,sku) for t in tokens]
        result=MISMATCH if any(x['result']==MISMATCH for x in checks) else CORRESPONDS if checks and all(x['result']==CORRESPONDS for x in checks) else UNKNOWN
        return {**base,'result':result,'function_checks':checks,'found_value':'; '.join(t for t,x in zip(tokens,checks) if x['result']==CORRESPONDS) or None,'source':next((x['source'] for x in checks if x['source']),None),'source_url':next((x['source'] for x in checks if x['source']),None),'parameter':key,'normalized_value':{t:x.get('normalized_value') for t,x in zip(tokens,checks)},'source_type':list(dict.fromkeys(x.get('source_type','OTHER') for x in checks)),'evidence':[x['evidence'] for x in checks],'confidence':'high' if result!=UNKNOWN else 'low'}
    observations=[]
    for page in sorted(pages,key=source_priority):
        if not page_matches(page,sku):continue
        for found_key,actual,actual_unit,fragment in facts(primary_content(page)):
            if found_key!=key:continue
            result=compare(required,actual,key,operator,unit,actual_unit)
            observations.append({**base,'found_value':actual,'actual_unit':actual_unit or (numeric(actual) or (None,None,None))[2],
                'source':page['url'],'source_url':page['url'],'source_page':page.get('page_number'),
                'source_type':SOURCE_TYPES.get(page.get('source_type','other'),page.get('source_type') if page.get('source_type') in SOURCE_TYPES.values() else 'OTHER') if page.get('source_verified') else 'OTHER',
                'parameter':key,'normalized_value':normalized_value(actual,actual_unit,key),
                'source_priority':source_priority(page),
                'evidence':fragment,'result':result,'confidence':'high' if result!=UNKNOWN else 'low'})
    definitive=[x for x in observations if x['result']!=UNKNOWN]
    if not definitive:return observations[0] if observations else base
    priority=min(x['source_priority'] for x in definitive)
    lower=[x for x in definitive if x['source_priority']>priority]
    definitive=[x for x in definitive if x['source_priority']==priority]
    if len({x['result'] for x in definitive})>1:
        return {**base,'result':UNKNOWN,'evidence':'Источники противоречат друг другу','conflicting_evidence':definitive}
    return {**definitive[0], 'lower_priority_observations':lower}


def assess_model(requirements,pages,sku):
    checks=[check_requirement(r,pages,sku) for r in requirements]
    mandatory=[c for r,c in zip(requirements,checks) if r.get('mandatory',True) is not False and r.get('criticality')!='optional']
    status='non_compliant' if any(c['result']==MISMATCH for c in mandatory) else 'fully_compliant' if mandatory and all(c['result']==CORRESPONDS for c in mandatory) else 'not_confirmed'
    return {'requirements_check':checks,'status':status,'technical_status':status,'status_ru':STATUS_RU[status]}
